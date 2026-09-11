from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, text

from test_report_generation import factory, postgres_factory, seed
from test_scheduled_report_delivery import email_schedule

MIGRATION = Path(__file__).parents[1] / "alembic/versions/20260910_000033_report_pdf_delivery.py"


def migrate(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        runpy.run_path(str(MIGRATION))[direction]()


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_upgrade_downgrade_reupgrade_preserves_legacy_exports_reports_and_sources(request, fixture):
    factory = request.getfixturevalue(fixture)
    command = seed(factory, used=3)
    with factory.kw["bind"].begin() as connection:
        connection.execute(text("INSERT INTO exports(workspace_id,status,output_s3_key) VALUES (:workspace,'done','legacy.pptx')"), {"workspace": command.workspace_id})
        migrate(connection, "downgrade")
        assert "scheduled_report_deliveries" not in inspect(connection).get_table_names()
        assert "artifact_type" not in {col["name"] for col in inspect(connection).get_columns("exports")}
        migrate(connection, "upgrade")
        assert connection.execute(text("SELECT artifact_type FROM exports")).scalar_one() == "PPTX"
        assert connection.execute(text("SELECT count(*) FROM reports")).scalar_one() == 3
        assert connection.execute(text("SELECT output_s3_key FROM exports")).scalar_one() == "legacy.pptx"
        indexes = {index["name"] for index in inspect(connection).get_indexes("scheduled_report_deliveries")}
        assert {"ix_scheduled_deliveries_pending", "ix_scheduled_deliveries_expired", "ix_scheduled_deliveries_workspace"} <= indexes
        assert "uq_exports_pdf_version" in {index["name"] for index in inspect(connection).get_indexes("exports")}
        migrate(connection, "downgrade")
        migrate(connection, "upgrade")


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_downgrade_refuses_delivery_configuration(request, fixture):
    factory = request.getfixturevalue(fixture)
    email_schedule(factory)
    with pytest.raises(RuntimeError, match="Delivery configuration exists"):
        with factory.kw["bind"].begin() as connection:
            migrate(connection, "downgrade")


def test_downgrade_refuses_frozen_snapshot(factory):
    command = seed(factory, used=1)
    with factory.kw["bind"].begin() as connection:
        connection.execute(text("INSERT INTO exports(workspace_id,status,artifact_type) VALUES (:workspace,'NOT_REQUESTED','PDF')"), {"workspace": command.workspace_id})
    with pytest.raises(RuntimeError, match="PDF/delivery history"):
        with factory.kw["bind"].begin() as connection:
            migrate(connection, "downgrade")


def test_phase4_migration_parent():
    assert runpy.run_path(str(MIGRATION))["down_revision"] == "20260910_000032"
