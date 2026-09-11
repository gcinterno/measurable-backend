"""Opt-in local compiled-frontend smoke test; never reaches production API/assets."""
import os
from urllib.parse import urlsplit

import pytest

from test_report_generation import factory
from test_scheduled_report_refresh import test_scheduled_execution_uses_real_canonical_provider_builders as build_provider_report
from app.models import Export
from app.report_artifacts import PinnedRenderRequest
from app.scheduled_report_refresh import private_provider_io
from app.services import generate_pdf_from_export_page


@pytest.mark.parametrize("provider", ["facebook_pages", "instagram_business_login", "meta_ads", "shopify", "multi_source"])
def test_exact_snapshot_with_existing_compiled_frontend(factory, monkeypatch, tmp_path, provider):
    base = os.environ.get("PDF_TEST_FRONTEND_URL")
    if not base:
        pytest.skip("Set PDF_TEST_FRONTEND_URL to a disposable local compiled frontend")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1"}
    build_provider_report(factory, provider, monkeypatch)
    with factory() as db:
        payload = db.query(Export).filter_by(artifact_type="PDF").one().render_snapshot_json

    class OfflinePin(PinnedRenderRequest):
        def install(self, context):
            context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in {"localhost", "127.0.0.1"} else route.abort())
            super().install(context)

    pin = OfflinePin(payload, "local-version-fixture")
    with private_provider_io():
        pdf, debug = generate_pdf_from_export_page(export_url=f"{base}/share/reports/local-version-fixture?export=pdf",
            report_id=payload["report"]["id"], pinned_request=pin)
    assert pin.consumed and pdf.startswith(b"%PDF") and len(pdf) > 10000
    assert debug["page_count"] == (10 if provider == "multi_source" else 5)
    (tmp_path / f"{provider}.pdf").write_bytes(pdf)
