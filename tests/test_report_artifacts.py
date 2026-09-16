import json
from email import policy
from email.parser import BytesParser
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import services
from app import report_artifacts as artifacts
from app.scheduled_report_email import MEASURABLE_LOGO_PNG
from test_report_generation import factory, postgres_factory
from test_scheduled_report_delivery import completed, io, stub_generation, MemoryS3


def test_render_contract_supplies_only_exact_frozen_payload():
    payload = {"version": {"id": 91}, "report": {"id": 3}, "blocks": [{"data": {"text": "original"}}]}
    pin = artifacts.PinnedRenderRequest(payload, "opaque-fixture")
    route = Mock(request=SimpleNamespace(url="https://api.test/public/reports/opaque-fixture", method="GET", headers={"origin": "https://frontend.test"}))
    pin.handle(route)
    assert pin.consumed
    kwargs = route.fulfill.call_args.kwargs
    assert json.loads(kwargs["body"]) == payload
    assert kwargs["headers"]["Access-Control-Allow-Origin"] == "https://frontend.test"
    assert kwargs["headers"]["Access-Control-Allow-Credentials"] == "true"


@pytest.mark.parametrize("url,method", [("https://api.test/public/reports/current", "GET"), ("https://api.test/public/reports/pin/download/pdf", "GET"), ("https://api.test/public/reports/pin", "POST")])
def test_render_contract_blocks_mutable_or_unexpected_fetch(url, method):
    pin = artifacts.PinnedRenderRequest({}, "pin")
    route = Mock(request=SimpleNamespace(url=url, method=method, headers={}))
    pin.handle(route)
    route.abort.assert_called_once()
    assert not pin.consumed


def test_cors_preflight_does_not_count_as_rendered_payload():
    pin = artifacts.PinnedRenderRequest({}, "pin")
    route = Mock(request=SimpleNamespace(url="https://api.test/public/reports/pin", method="OPTIONS", headers={}))
    pin.handle(route)
    assert route.fulfill.call_args.kwargs["status"] == 204 and not pin.consumed


def test_pdf_rejects_renderer_that_never_consumes_snapshot(monkeypatch):
    monkeypatch.setattr(artifacts.settings, "report_export_base_url", "https://frontend.test")
    monkeypatch.setattr(services, "generate_pdf_from_export_page", lambda **kw: (b"%PDF-test", {}))
    with pytest.raises(artifacts.ArtifactError, match="pdf_pinned_payload_not_consumed"):
        artifacts.render_pdf({"report": {"id": 3}})


def test_preview_renderer_uses_exact_frozen_payload(monkeypatch):
    monkeypatch.setattr(artifacts.settings, "report_export_base_url", "https://frontend.test")
    payload = {"report": {"id": 3, "title": "Frozen title"}, "version": {"id": 9}, "blocks": [{"id": 1}]}
    captured = []

    def render(**kwargs):
        pinned = kwargs["pinned_request"]
        captured.append(pinned.payload)
        pinned.consumed = True
        assert kwargs["image_type"] == "jpeg"
        return b"\xff\xd8\xff exact cover", {}

    monkeypatch.setattr(services, "generate_thumbnail_from_export_page", render)
    assert artifacts.render_preview(payload) == b"\xff\xd8\xff exact cover"
    assert captured == [payload]


@pytest.mark.parametrize("data", [b"", b"<html>not PDF</html>"])
def test_invalid_pdf_cannot_be_uploaded(factory, io, data):
    _, _, _, export_id = completed(factory)
    with pytest.raises(artifacts.ArtifactError, match="invalid_pdf"):
        artifacts.ensure_pdf(factory, export_id, renderer=lambda payload: data, storage=io.s3)
    assert io.s3.puts == []


def test_ready_artifact_is_reused_without_any_s3_or_renderer_calls(factory, io):
    _, _, _, export_id = completed(factory)
    first = io.builder(factory, export_id)
    second = artifacts.ensure_pdf(factory, export_id, renderer=Mock(side_effect=AssertionError), storage=Mock(side_effect=AssertionError))
    assert first == second and len(io.renders) == 1


def test_tampered_snapshot_is_rejected(factory, io):
    from app.models import Export
    _, _, _, export_id = completed(factory)
    with factory() as db:
        row = db.get(Export, export_id)
        row.render_snapshot_json = {"version": {"id": 123}, "report": {"id": 234}}
        db.commit()
    with pytest.raises(artifacts.ArtifactError, match="artifact_snapshot_invalid"):
        io.builder(factory, export_id)
    assert not io.renders


@pytest.mark.parametrize("expires", [0, -1, 86401])
def test_invalid_download_expiry_rejected(expires):
    with pytest.raises(ValueError):
        artifacts.download_url(None, expires=expires)


def test_email_pdf_links_keep_existing_private_signing_with_safe_filenames(io):
    artifact = artifacts.ArtifactIdentity(8, "private-bucket", "private/key.pdf", 12, 34)
    artifacts.report_view_url(artifact, expires=86400, storage=io.s3)
    artifacts.email_download_url(artifact, expires=86400, storage=io.s3)
    view = io.s3.urls[-2][1]
    download = io.s3.urls[-1][1]
    assert view["ExpiresIn"] == download["ExpiresIn"] == 86400
    assert view["Params"]["ResponseContentDisposition"] == 'inline; filename="measurable-report.pdf"'
    assert download["Params"]["ResponseContentDisposition"] == 'attachment; filename="measurable-report.pdf"'
    assert "12" not in view["Params"]["ResponseContentDisposition"]
    assert "34" not in download["Params"]["ResponseContentDisposition"]


def test_s3_client_supports_conditional_put_without_public_acl():
    client = artifacts.s3_client()
    assert "IfNoneMatch" in client.meta.service_model.operation_model("PutObject").input_shape.members


def test_ses_transport_preserves_auth_and_hides_delivery_recipients(monkeypatch):
    ses = Mock()
    ses.send_email.return_value = {"MessageId": "accepted"}
    monkeypatch.setattr(services, "_ses_client", lambda **kw: ses)
    for purpose, destination in [("email_verification", "ToAddresses"), ("scheduled_report", "BccAddresses")]:
        assert services.send_email_message(recipients=["one@example.com", "two@example.com"], subject="Subject", html_body="<p>Hello</p>", text_body="Hello", purpose=purpose) == "accepted"
        kwargs = ses.send_email.call_args.kwargs
        assert kwargs["Destination"] == {destination: ["one@example.com", "two@example.com"]}
        assert kwargs["ReplyToAddresses"] == ["hello@measurableapp.com"]
        assert kwargs["Message"]["Body"]["Text"]["Data"] == "Hello"


def test_ses_transport_sends_cid_preview_as_private_raw_mime(monkeypatch):
    ses = Mock()
    ses.send_raw_email.return_value = {"MessageId": "accepted-inline"}
    monkeypatch.setattr(services, "_ses_client", lambda **kw: ses)
    result = services.send_email_message(
        recipients=["one@example.com", "two@example.com"],
        subject="Your report is ready",
        html_body='<img src="cid:measurable-logo"><img src="cid:measurable-report-preview"><a href="https://private.test">View report</a>',
        text_body="View report: https://private.test",
        purpose="scheduled_report",
        single_attempt=True,
        inline_images=(services.InlineEmailImage(
            content_id="measurable-report-preview",
            content=b"\xff\xd8\xff preview",
            content_type="image/jpeg",
            filename="report-preview.jpg",
        ), services.InlineEmailImage(
            content_id="measurable-logo",
            content=MEASURABLE_LOGO_PNG,
            content_type="image/png",
            filename="measurable-logo.png",
        )),
    )
    assert result == "accepted-inline"
    kwargs = ses.send_raw_email.call_args.kwargs
    assert kwargs["Destinations"] == ["one@example.com", "two@example.com"]
    parsed = BytesParser(policy=policy.default).parsebytes(kwargs["RawMessage"]["Data"])
    assert parsed["To"] == "undisclosed-recipients:;" and parsed["Bcc"] is None
    parts = list(parsed.walk())
    preview = next(part for part in parts if part.get("Content-ID") == "<measurable-report-preview>")
    assert preview.get_content_type() == "image/jpeg"
    assert preview.get_filename() == "report-preview.jpg"
    assert preview.get_payload(decode=True) == b"\xff\xd8\xff preview"
    logo = next(part for part in parts if part.get("Content-ID") == "<measurable-logo>")
    assert logo.get_content_type() == "image/png"
    assert logo.get_filename() == "measurable-logo.png"
    assert logo.get_payload(decode=True) == MEASURABLE_LOGO_PNG


def test_scheduled_ses_disables_sdk_retries(monkeypatch):
    client = Mock()
    monkeypatch.setattr(services.boto3, "client", client)
    services._ses_client(single_attempt=True)
    config = client.call_args.kwargs["config"]
    assert config.retries["total_max_attempts"] == 1
    assert config.read_timeout == 30


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_stale_renderer_cannot_overwrite_winner_s3_object_or_finalize(request, fixture, io):
    from app.models import Export
    from app.report_generation import _database_now
    factory = request.getfixturevalue(fixture)
    _, _, _, export_id = completed(factory)
    started, release = Event(), Event()
    def old_renderer(payload):
        started.set()
        assert release.wait(15)
        return b"%PDF-old-worker"
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(artifacts.ensure_pdf, factory, export_id, renderer=old_renderer, storage=io.s3)
        assert started.wait(10)
        try:
            with factory() as db:
                # The first renderer is blocked on external work, not a DB row lock.
                row = db.query(Export).filter_by(id=export_id).with_for_update(nowait=True).one()
                row.lease_expires_at = _database_now(db) - timedelta(seconds=1)
                db.commit()
            artifacts.ensure_pdf(factory, export_id, renderer=lambda payload: b"%PDF-winning-worker", storage=io.s3)
        finally:
            release.set()
        with pytest.raises(artifacts.ArtifactError, match="artifact_lease_lost"):
            first.result()
    assert len(io.s3.puts) == 1 and io.s3.puts[0]["Body"] == b"%PDF-winning-worker"
    with factory() as db:
        assert db.get(Export, export_id).status == "READY"
