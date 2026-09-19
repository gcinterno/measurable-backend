from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from app.models import (
    Dataset,
    Integration,
    IntegrationAccount,
    Report,
    ReportBlock,
    ReportGeneration,
    ReportTemplate,
    ReportTemplateVersion,
    Workspace,
)
from app.report_generation import (
    ExecutableReportConfiguration,
    GenerationError,
    GenerationOptions,
    SourceIdentity,
    generate_report,
)
from app.security import create_access_token
from test_report_generation import api_client, factory, seed


def _studio_spec(*, supported_modes=None, required_semantics=None) -> dict:
    return {
        "schema_version": "1.0",
        "id": "studio-facebook-instagram-engagement",
        "name": "Studio Facebook + Instagram Engagement",
        "report_type": "custom",
        "template_id": "studio-facebook-instagram-engagement",
        "generation_mode": "template",
        "datasource_requirements": {
            "supported_modes": supported_modes or ["multi_source"],
            "catalog_required": True,
            "required_source_count": 2,
            "minimum_source_count": 2,
            "required_canonical_semantics": required_semantics or ["engagement"],
            "optional_canonical_semantics": [],
        },
        "reporting_period": {"selector": "request.timeframe"},
        "theme_ref": {"id": "default_report_theme"},
        "slides": [
            {
                "id": "intro",
                "order": 1,
                "slide_type": "text",
                "title": "Starter Slide",
                "layout": "text",
                "blocks": [{"id": "intro-text", "type": "text", "bindings": []}],
            },
            {
                "id": "engagement",
                "order": 2,
                "slide_type": "metric",
                "title": "Engagement Overview",
                "layout": "metric_chart_source_split",
                "blocks": [
                    {
                        "id": "engagement-hero",
                        "type": "metric_hero",
                        "bindings": [{"canonical_semantic": "engagement"}],
                    },
                    {
                        "id": "engagement-chart",
                        "type": "timeseries_chart",
                        "bindings": [{"canonical_semantic": "engagement", "metric_path": "timeseries"}],
                    },
                    {
                        "id": "engagement-sources",
                        "type": "source_split",
                        "bindings": [{"canonical_semantic": "engagement", "metric_path": "source_split"}],
                    },
                    {"id": "engagement-read", "type": "executive_read", "bindings": []},
                ],
            },
        ],
        "metadata": {"authoring_surface": "report_studio"},
    }


def _template(factory, command, *, spec=None, workspace_id=None, published=True):
    with factory() as db:
        template = ReportTemplate(
            workspace_id=command.workspace_id if workspace_id is None else workspace_id,
            name="Studio template",
            slug=f"studio-template-{datetime.now(timezone.utc).timestamp()}",
            status="published" if published else "draft",
            generation_mode="manual_template",
            template_type="custom",
            datasource_requirements=dict((spec or _studio_spec())["datasource_requirements"]),
            metadata_json={},
        )
        db.add(template)
        db.flush()
        version = ReportTemplateVersion(
            report_template_id=template.id,
            version_number=1,
            schema_version="1.0",
            spec_json=spec or _studio_spec(),
            published_at=datetime.now(timezone.utc) if published else None,
        )
        db.add(version)
        db.flush()
        template.active_version_id = version.id
        template.published_version_id = version.id if published else None
        db.commit()
        return template.id, version.id


def _multi_source_command(factory, command, *, instagram_engagement=30):
    with factory() as db:
        facebook_dataset = db.get(Dataset, command.sources[0].dataset_id)
        facebook_data = dict(facebook_dataset.data)
        facebook_data.update(
            engagement=50,
            engagement_total=50,
            daily_engagement=[{"date": "2026-08-01", "value": 50}],
        )
        facebook_dataset.data = facebook_data
        instagram = Integration(
            workspace_id=command.workspace_id,
            provider="instagram_business",
            name="Instagram",
            status="connected",
        )
        db.add(instagram)
        db.flush()
        account = IntegrationAccount(
            workspace_id=command.workspace_id,
            integration_id=instagram.id,
            external_account_id="ig_456",
            display_name="Instagram account",
        )
        instagram_data = {
            **facebook_data,
            "integration_type": "instagram_business",
            "integration_id": instagram.id,
            "account_id": "ig_456",
            "page_id": "ig_456",
            "page_name": "Instagram account",
            "account_name": "Instagram account",
            "username": "instagram_account",
            "engagement": instagram_engagement,
            "engagement_total": instagram_engagement,
            "content_interactions": instagram_engagement,
            "total_interactions": instagram_engagement,
            "daily_engagement": (
                [{"date": "2026-08-01", "value": instagram_engagement}]
                if instagram_engagement is not None
                else []
            ),
        }
        dataset = Dataset(workspace_id=command.workspace_id, name="Instagram data", data=instagram_data)
        db.add_all([account, dataset])
        db.flush()
        instagram_source = SourceIdentity(
            dataset.id,
            instagram.provider,
            "instagram_business",
            instagram.id,
            account.id,
            "ig_456",
            position=1,
            label="Instagram account",
        )
        db.commit()
    return replace(
        command,
        sources=(command.sources[0], instagram_source),
        configuration=ExecutableReportConfiguration("multi_source", requested_slides=10),
        options=GenerationOptions(title="Studio real-data report"),
    )


def _selected(command, template_id, version_id=None, *, key="studio-generation"):
    return replace(
        command,
        configuration=replace(
            command.configuration,
            report_template_id=template_id,
            report_template_version_id=version_id,
        ),
        idempotency_key=key,
    )


def test_published_studio_template_generates_real_canonical_blocks_and_exact_provenance(factory):
    command = _multi_source_command(factory, seed(factory, provider="facebook_pages"))
    template_id, version_id = _template(factory, command)

    with factory() as db:
        result = generate_report(db, _selected(command, template_id, version_id))

    with factory() as db:
        report = db.get(Report, result.report_id)
        assert (report.report_template_id, report.report_template_version_id) == (template_id, version_id)
        assert json.loads(report.description)["generation_mode"] == "report_spec"
        block = db.query(ReportBlock).filter_by(report_version_id=result.version_id).one()
        payload = json.loads(block.data_json)
        assert payload["canonical_semantic"] == "engagement"
        assert payload["primary_value"] == 80
        assert {source["source_type"] for source in payload["source_contributions"]} == {
            "facebook_pages",
            "instagram_business",
        }
        assert "sample" not in block.data_json.lower()


def test_template_id_only_uses_current_published_version_and_historical_report_stays_pinned(factory):
    command = _multi_source_command(factory, seed(factory, provider="facebook_pages"))
    template_id, published_version_id = _template(factory, command)
    with factory() as db:
        result = generate_report(db, _selected(command, template_id, key="published-pointer"))
        template = db.get(ReportTemplate, template_id)
        replacement = ReportTemplateVersion(
            report_template_id=template_id,
            version_number=2,
            schema_version="1.0",
            spec_json={**_studio_spec(), "name": "Later published spec"},
            published_at=datetime.now(timezone.utc),
        )
        db.add(replacement)
        db.flush()
        template.published_version_id = replacement.id
        template.active_version_id = replacement.id
        db.commit()
        report = db.get(Report, result.report_id)
        assert report.report_template_version_id == published_version_id


def test_report_fetch_exposes_published_spec_and_provenance(factory, api_client):
    command = _multi_source_command(factory, seed(factory, provider="facebook_pages"))
    template_id, version_id = _template(factory, command)
    headers = {"Authorization": f"Bearer {create_access_token(str(command.actor_user_id))}"}
    body = {
        "title": "Studio API report",
        "requested_slides": 10,
        "report_template_id": template_id,
        "report_template_version_id": version_id,
        "sources": [
            {
                "dataset_id": source.dataset_id,
                "provider": source.provider,
                "source_type": source.source_type,
                "integration_id": source.integration_id,
                "integration_account_id": source.integration_account_id,
                "position": source.position,
            }
            for source in command.sources
        ],
    }

    created = api_client.post("/reports/multi-source", headers=headers, json=body)

    assert created.status_code == 200, created.text
    assert created.json()["report_template_id"] == template_id
    assert created.json()["report_template_version_id"] == version_id
    assert created.json()["report_spec"]["slides"][1]["layout"] == "metric_chart_source_split"
    version = api_client.get(
        f"/reports/{created.json()['id']}/versions/{created.json()['version']}",
        headers=headers,
    )
    assert version.status_code == 200
    assert version.json()["report_template_version_id"] == version_id
    runtime_payload = json.loads(version.json()["blocks"][0]["data_json"])
    assert runtime_payload["primary_value"] == 80

    with factory() as db:
        replacement = ReportTemplateVersion(
            report_template_id=template_id,
            version_number=2,
            schema_version="1.0",
            spec_json={**_studio_spec(), "name": "Later published spec"},
            published_at=datetime.now(timezone.utc),
        )
        db.add(replacement)
        db.flush()
        template = db.get(ReportTemplate, template_id)
        template.active_version_id = replacement.id
        template.published_version_id = replacement.id
        db.commit()

    historical = api_client.get(f"/reports/{created.json()['id']}", headers=headers)
    assert historical.status_code == 200
    assert historical.json()["report_template_version_id"] == version_id
    assert historical.json()["report_spec"]["name"] == "Studio Facebook + Instagram Engagement"


def test_generation_api_rejects_unpublished_version_with_explicit_4xx(factory, api_client):
    command = _multi_source_command(factory, seed(factory, provider="facebook_pages"))
    template_id, version_id = _template(factory, command, published=False)
    headers = {"Authorization": f"Bearer {create_access_token(str(command.actor_user_id))}"}
    response = api_client.post(
        "/reports/multi-source",
        headers=headers,
        json={
            "title": "Rejected draft",
            "requested_slides": 10,
            "report_template_id": template_id,
            "report_template_version_id": version_id,
            "sources": [
                {
                    "dataset_id": source.dataset_id,
                    "provider": source.provider,
                    "source_type": source.source_type,
                    "integration_id": source.integration_id,
                    "integration_account_id": source.integration_account_id,
                    "position": source.position,
                }
                for source in command.sources
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "report_template_version_unpublished"


def test_unpublished_mismatched_unknown_archived_and_unauthorized_templates_fail_closed(factory):
    command = _multi_source_command(factory, seed(factory, provider="facebook_pages"))
    published_template, published_version = _template(factory, command)
    draft_template, draft_version = _template(factory, command, published=False)
    other_template, other_version = _template(factory, command)
    with factory() as db:
        db.get(ReportTemplate, other_template).archived_at = datetime.now(timezone.utc)
        db.get(ReportTemplate, other_template).status = "archived"
        db.commit()

    cases = [
        (_selected(command, 999999, key="unknown-template"), "report_template_not_found"),
        (_selected(command, published_template, 999999, key="unknown-version"), "report_template_version_not_found"),
        (_selected(command, published_template, other_version, key="mismatch"), "report_template_version_mismatch"),
        (_selected(command, draft_template, draft_version, key="draft"), "report_template_version_unpublished"),
        (_selected(command, other_template, other_version, key="archived"), "report_template_archived"),
    ]
    for selected, expected_code in cases:
        with factory() as db, pytest.raises(GenerationError) as exc:
            generate_report(db, selected)
        assert exc.value.code == expected_code

    with factory() as db:
        # No report was created by any rejected selection.
        assert db.query(Report).first() is None
        foreign_workspace = Workspace(name="Foreign workspace")
        db.add(foreign_workspace)
        db.flush()
        db.get(ReportTemplate, published_template).workspace_id = foreign_workspace.id
        db.commit()
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, _selected(command, published_template, published_version, key="forbidden"))
    assert exc.value.code == "report_template_forbidden"


def test_datasource_count_mode_and_required_semantic_are_enforced(factory):
    base = seed(factory, provider="facebook_pages")
    command = _multi_source_command(factory, base)
    template_id, version_id = _template(factory, command)

    one_source = replace(command, sources=(command.sources[0],))
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, _selected(one_source, template_id, version_id, key="wrong-count"))
    assert exc.value.code == "report_template_source_count_incompatible"

    mode_template, mode_version = _template(
        factory,
        command,
        spec=_studio_spec(supported_modes=["meta_pages"]),
    )
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, _selected(command, mode_template, mode_version, key="wrong-mode"))
    assert exc.value.code == "report_template_mode_incompatible"

    with factory() as db:
        for source in command.sources:
            dataset = db.get(Dataset, source.dataset_id)
            row = dict(dataset.data)
            for field in (
                "engagement",
                "engagement_total",
                "content_interactions",
                "total_interactions",
                "accounts_engaged",
            ):
                row[field] = None
            row["daily_engagement"] = []
            row["engagement_daily"] = []
            dataset.data = row
        db.commit()
    with factory() as db, pytest.raises(GenerationError) as exc:
        generate_report(db, _selected(command, template_id, version_id, key="missing-semantic"))
    assert exc.value.code == "report_template_required_semantic_unavailable"
    assert exc.value.details == {"missing_canonical_semantics": ["engagement"]}


def test_no_template_selection_keeps_legacy_builder_and_null_provenance(factory, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "generate_meta_pages_ai_summary", lambda *_args, **_kwargs: "Report analysis")
    command = seed(factory, provider="facebook_pages")
    with factory() as db:
        result = generate_report(db, command)
        report = db.get(Report, result.report_id)
        assert report.report_template_id is None
        assert report.report_template_version_id is None
        assert db.query(ReportBlock).filter_by(report_version_id=result.version_id).count() == 5
        assert db.query(ReportGeneration).one().state == "consumed"
