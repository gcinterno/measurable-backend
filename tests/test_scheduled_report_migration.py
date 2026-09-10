from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import runpy
from threading import Barrier

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from test_report_generation import factory, postgres_factory, seed
from test_scheduled_reports import NOW, add_history, body
from app.models import Report, ReportSource, ReportVersion, User
from app.scheduled_report_models import ScheduledReport, ScheduledReportRevision, ScheduledReportRun, ScheduledReportSource
import app.scheduled_reports as schedules


MIGRATION = Path(__file__).parents[1] / "alembic/versions/20260909_000031_add_scheduled_reports.py"
TABLES = ("scheduled_reports", "scheduled_report_revisions", "scheduled_report_sources", "scheduled_report_runs")


def migrate(connection, direction):
    migration = runpy.run_path(str(MIGRATION))
    with Operations.context(MigrationContext.configure(connection)):
        migration[direction]()


def migrate_execution(connection, direction):
    migration = runpy.run_path(str(MIGRATION.with_name("20260910_000032_scheduled_report_execution.py")))
    with Operations.context(MigrationContext.configure(connection)):
        migration[direction]()


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_migration_upgrade_downgrade_reupgrade_preserves_historical_reports(request, fixture):
    factory = request.getfixturevalue(fixture)
    command = seed(factory, used=3, provider="facebook_pages")
    with factory() as db:
        report = db.query(Report).order_by(Report.id).first()
        source = command.sources[0]
        db.add(ReportSource(report_id=report.id, workspace_id=command.workspace_id, provider=source.provider,
                            source_type=source.source_type, integration_id=source.integration_id,
                            integration_account_id=source.integration_account_id, dataset_id=source.dataset_id, position=0))
        db.add(ReportVersion(report_id=report.id, version=1))
        db.commit()
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        migrate_execution(connection, "downgrade")
        before = {table: {index["name"] for index in inspect(connection).get_indexes(table)} for table in TABLES}
        migrate(connection, "downgrade")
        assert all(table not in inspect(connection).get_table_names() for table in TABLES)
        migrate(connection, "upgrade")
        after = {table: {index["name"] for index in inspect(connection).get_indexes(table)} for table in TABLES}
        assert before == after
        assert connection.execute(text("SELECT count(*) FROM reports")).scalar_one() == 3
        assert connection.execute(text("SELECT count(*) FROM report_sources")).scalar_one() == 1
        assert connection.execute(text("SELECT integration_account_id FROM report_sources")).scalar_one() == command.sources[0].integration_account_id
        assert connection.execute(text("SELECT count(*) FROM report_versions")).scalar_one() == 1
        due = next(index for index in inspect(connection).get_indexes("scheduled_reports") if index["name"] == "ix_scheduled_reports_due")
        assert due["column_names"] == ["next_run_at", "id"]
        migrate_execution(connection, "upgrade")
    with factory() as db:
        output = schedules.create_scheduled_report(schedules.ScheduleCreateInput.model_validate(body(command)), db.get(User, command.actor_user_id), db)
        add_history(db, output)
        db.commit()
    with pytest.raises(RuntimeError, match="configuration/history"):
        with engine.begin() as connection:
            migrate(connection, "downgrade")
    with factory() as db:
        assert db.query(ScheduledReport).count() == db.query(ScheduledReportRun).count() == 1
        assert db.query(Report).count() == 3


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_revision_workspace_and_current_revision_foreign_keys(request, fixture):
    factory = request.getfixturevalue(fixture)
    first = seed(factory, provider="facebook_pages")
    second = seed(factory, provider="facebook_pages")
    with factory() as db:
        output = schedules.create_scheduled_report(schedules.ScheduleCreateInput.model_validate(body(first)), db.get(User, first.actor_user_id), db)
        with pytest.raises(IntegrityError), db.begin_nested():
            revision = db.query(ScheduledReportRevision).one()
            db.add(ScheduledReportSource(schedule_id=output["id"], workspace_id=second.workspace_id,
                                         configuration_revision=revision.revision, position=1,
                                         provider="meta", source_type="facebook_pages", external_account_id="other"))
            db.flush()
        db.query(ScheduledReport).update({"configuration_revision": 99})
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.query(ScheduledReport).one().configuration_revision == 1


def test_postgres_concurrent_edits_serialize_revision_and_reject_stale_editor(postgres_factory):
    factory = postgres_factory
    command = seed(factory, provider="facebook_pages")
    with factory() as db:
        output = schedules.create_scheduled_report(schedules.ScheduleCreateInput.model_validate(body(command)), db.get(User, command.actor_user_id), db)
    barrier = Barrier(2)
    def edit(hour):
        with factory() as db:
            user = db.get(User, command.actor_user_id)
            barrier.wait(timeout=10)
            try:
                result = schedules.update_scheduled_report(output["id"], schedules.ScheduleUpdateInput(local_time=f"{hour:02d}:00", expected_revision=1), command.workspace_id, user, db)
                return result["configuration_revision"]
            except HTTPException as exc:
                db.rollback()
                return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(edit, (9, 10)))
    assert sorted(results) == [2, 409]
    with factory() as db:
        assert db.query(ScheduledReportRevision).count() == 2
        assert db.query(ScheduledReportSource).count() == 2


def test_postgres_duplicate_occurrence_rejected_across_configuration_revisions(postgres_factory):
    factory = postgres_factory
    command = seed(factory, provider="facebook_pages")
    with factory() as db:
        first = schedules.create_scheduled_report(schedules.ScheduleCreateInput.model_validate(body(command)), db.get(User, command.actor_user_id), db)
        second = schedules.update_scheduled_report(first["id"], schedules.ScheduleUpdateInput(local_time="09:00"), command.workspace_id, db.get(User, command.actor_user_id), db)
    barrier = Barrier(2)
    def insert(snapshot):
        with factory() as db:
            barrier.wait(timeout=10)
            try:
                add_history(db, snapshot)
                db.commit()
                return "created"
            except IntegrityError:
                db.rollback()
                return "duplicate"
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(insert, (first, second))) == ["created", "duplicate"]
    with factory() as db:
        assert db.query(ScheduledReportRun).count() == 1


def test_phase_one_migration_is_still_parent():
    migration = runpy.run_path(str(MIGRATION))
    assert migration["down_revision"] == "20260909_000030"
    assert migration["revision"] == "20260909_000031"
