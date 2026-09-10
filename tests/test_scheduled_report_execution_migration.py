from pathlib import Path
import runpy

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from test_report_generation import factory, postgres_factory
from test_scheduled_report_execution import make_schedule, dispatch_claim


MIGRATION = Path(__file__).parents[1] / "alembic/versions/20260910_000032_scheduled_report_execution.py"


def migrate(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        runpy.run_path(str(MIGRATION))[direction]()


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_execution_upgrade_downgrade_reupgrade_preserves_config_and_reports(request, fixture):
    factory = request.getfixturevalue(fixture)
    _, output = make_schedule(factory, used=3)
    with factory.kw["bind"].begin() as connection:
        before = {col["name"] for col in inspect(connection).get_columns("scheduled_report_runs")}
        migrate(connection, "downgrade")
        assert "lease_token" not in {col["name"] for col in inspect(connection).get_columns("scheduled_report_runs")}
        migrate(connection, "upgrade")
        assert before == {col["name"] for col in inspect(connection).get_columns("scheduled_report_runs")}
        assert connection.execute(text("SELECT count(*) FROM reports")).scalar_one() == 3
        assert connection.execute(text("SELECT count(*) FROM scheduled_report_revisions")).scalar_one() == 1
        indexes = {i["name"] for i in inspect(connection).get_indexes("scheduled_report_runs")}
        assert {"ix_scheduled_report_runs_runnable", "ix_scheduled_report_runs_expired", "uq_scheduled_report_run_occurrence"} <= indexes
    dispatch_claim(factory, output)
    with pytest.raises(RuntimeError, match="Execution history"):
        with factory.kw["bind"].begin() as connection:
            migrate(connection, "downgrade")


def test_execution_migration_parent():
    assert runpy.run_path(str(MIGRATION))["down_revision"] == "20260909_000031"
