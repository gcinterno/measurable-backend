from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from functools import partial
import json
import struct
from types import SimpleNamespace

from botocore.exceptions import ClientError, ReadTimeoutError
import pytest
from sqlalchemy.exc import IntegrityError

from test_report_generation import factory, postgres_factory, seed, persist_stub
from test_scheduled_reports import client, headers, url
from test_scheduled_report_execution import make_schedule, dispatch_claim, refresh
from app.models import Export, Report, ReportBlock, ReportGeneration, ReportVersion, ScheduledReportDelivery, Subscription, User
from app.scheduled_report_models import ScheduledReport, ScheduledReportRevision, ScheduledReportRun
import app.report_artifacts as artifacts
import app.report_generation_builders as builders
import app.scheduled_report_delivery as delivery
import app.scheduled_report_execution as execution
import app.scheduled_report_worker as worker
import app.scheduled_reports as schedules
from app.scheduled_report_email import MEASURABLE_LOGO_PNG, ScheduledReportEmailContext, render_scheduled_report_email


class MemoryS3:
    def __init__(self):
        self.objects, self.puts, self.urls = {}, [], []
        self.failure = None

    def head_object(self, *, Bucket, Key):
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(obj["Body"]), "ContentType": obj["ContentType"], "Metadata": obj["Metadata"]}

    def put_object(self, **kwargs):
        if self.failure:
            raise self.failure
        key = kwargs["Bucket"], kwargs["Key"]
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.puts.append(kwargs)
        self.objects[key] = kwargs

    def generate_presigned_url(self, method, **kwargs):
        self.urls.append((method, kwargs))
        return "https://private.test/pdf?signature=DO_NOT_LOG"

    def get_object(self, *, Bucket, Key):
        obj = self.objects[(Bucket, Key)]
        return {"Body": obj["Body"]}


@pytest.fixture(autouse=True)
def stub_generation(monkeypatch):
    monkeypatch.setattr(builders, "build_report", persist_stub)


def email_schedule(factory, *, used=0):
    command, output = make_schedule(factory, used=used)
    with factory() as db:
        schedules.update_scheduled_report(output["id"], schedules.ScheduleUpdateInput(delivery={
            "mode": "EMAIL_PDF", "recipients": ["original@example.com"]}), command.workspace_id, db.get(User, command.actor_user_id), db)
        row = db.get(ScheduledReport, output["id"])
        row.next_run_at = execution._database_now(db) - timedelta(days=8)
        db.commit()
    return command, output


def completed(factory, *, used=0, email=True):
    command, output = email_schedule(factory, used=used) if email else make_schedule(factory, used=used)
    claim = dispatch_claim(factory, output)
    execution.execute_run(factory, claim, refresher=refresh)
    with factory() as db:
        run = db.get(ScheduledReportRun, claim.run_id)
        assert run.status == "SUCCEEDED", run.error_code
        artifact_id = db.query(Export).filter_by(report_version_id=run.report_version_id, artifact_type="PDF").one().id
    return command, output, claim.run_id, artifact_id


@pytest.fixture
def io():
    s3, renders, messages, previews = MemoryS3(), [], [], []
    def render(payload):
        renders.append(deepcopy(payload))
        return b"%PDF-1.4 fixture"
    def send(**kwargs):
        messages.append(kwargs)
        return "ses-message-1"
    builder = partial(artifacts.ensure_pdf, renderer=render, storage=s3)
    signer = partial(artifacts.download_url, storage=s3)
    def preview_builder(factory, export_id):
        previews.append(export_id)
        return SimpleNamespace(content=b"\xff\xd8\xff email preview", content_type="image/jpeg")
    return SimpleNamespace(s3=s3, renders=renders, messages=messages, previews=previews,
                           renderer=render, sender=send, builder=builder,
                           preview_builder=preview_builder, signer=signer)


def deliver(factory, io, **kwargs):
    claim = delivery.claim_delivery(factory)
    assert claim is not None
    delivery.execute_delivery(factory, claim, artifact_builder=io.builder,
        preview_builder=kwargs.pop("preview_builder", io.preview_builder),
        sender=kwargs.pop("sender", io.sender), signer=io.signer,
        viewer=kwargs.pop("viewer", lambda report_id: f"https://app.test/reports/{report_id}"), **kwargs)
    return claim


def retry_due(factory):
    with factory() as db:
        row = db.query(ScheduledReportDelivery).one()
        row.next_attempt_at = execution._database_now(db) - timedelta(seconds=1)
        db.commit()


def assert_single_generation(factory, used=1):
    with factory() as db:
        assert db.query(Report).count() == used
        assert db.query(ReportGeneration).filter_by(state="consumed").count() == 1


@pytest.mark.parametrize("locale,headline,view,chat,download,expiry,footer", [
    ("en", "Your report is ready", "View report", "Chat with your data", "Download PDF",
     "Your private PDF download link expires in 24 hours.", "AI Marketing Report Generator"),
    ("es", "Tu reporte está listo", "Ver reporte", "Chatea con tus datos", "Descargar PDF",
     "Tu enlace privado de descarga del PDF vence en 24 horas.", "Generador de Reportes de Marketing con IA"),
])
def test_premium_email_html_and_text_are_localized(locale, headline, view, chat, download, expiry, footer):
    report_url = "https://app.test/reports/97"
    download_url = "https://private.test/download?signature=signed"
    rendered = render_scheduled_report_email(ScheduledReportEmailContext(
        title="Measurable Growth <Q3>",
        reporting_start=date(2026, 9, 1),
        reporting_end=date(2026, 9, 7),
        source_label="ATRIA Marketing",
        generated_at=datetime(2026, 9, 8, 14, 30, tzinfo=timezone.utc),
        locale=locale,
        view_url=report_url,
        download_url=download_url,
    ))
    assert rendered.subject == headline
    for value in (headline, view, chat, download, expiry, footer, "ATRIA Marketing"):
        assert value in rendered.html and value in rendered.text
    assert "cid:measurable-report-preview" in rendered.html
    assert "cid:measurable-logo" in rendered.html
    assert "Measurable Growth &lt;Q3&gt;" in rendered.html
    assert "Measurable Growth <Q3>" in rendered.text
    assert rendered.html.count(f'href="{report_url}"') == 2
    assert rendered.text.count(report_url) == 2
    assert f'href="https://private.test/download?signature=signed"' in rendered.html
    assert download_url in rendered.text
    assert "workspace_id" not in rendered.html and "integration_id" not in rendered.html


def test_email_visual_shell_is_white_and_preview_is_centered_with_official_logo():
    rendered = render_scheduled_report_email(ScheduledReportEmailContext(
        title="Weekly performance",
        reporting_start=date(2026, 9, 1),
        reporting_end=date(2026, 9, 7),
        source_label="Internal source",
        generated_at=datetime(2026, 9, 8, 14, 30, tzinfo=timezone.utc),
        locale="en",
        view_url="https://app.test/reports/97",
        download_url="https://private.test/download",
    ))
    assert '<meta name="color-scheme" content="light">' in rendered.html
    assert '<meta name="supported-color-schemes" content="light">' in rendered.html
    assert ":root { color-scheme: light !important; supported-color-schemes: light !important; }" in rendered.html
    assert rendered.html.count('bgcolor="#ffffff"') >= 7
    assert rendered.html.count("background-color:#ffffff") >= 7
    assert "background:#f5f7fa" not in rendered.html
    assert '<img class="email-logo" src="cid:measurable-logo" width="190" alt="Measurable" align="center"' in rendered.html
    assert "height=\"" not in rendered.html.split('src="cid:measurable-logo"', 1)[1].split(">", 1)[0]
    assert '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" align="center" bgcolor="#f8fafc"' in rendered.html
    assert '<img src="cid:measurable-report-preview" width="468"' in rendered.html
    assert 'align="center" style="display:block;width:100%;max-width:468px;height:auto;margin:0 auto;' in rendered.html
    assert "max-width:488px" in rendered.html
    assert "negative" not in rendered.html and "translate" not in rendered.html and "position:absolute" not in rendered.html
    assert MEASURABLE_LOGO_PNG.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", MEASURABLE_LOGO_PNG[16:24])
    assert (width, height) == (800, 300) and width / height == pytest.approx(8 / 3)
    assert "@media only screen and (max-width:620px)" in rendered.html
    assert ".email-card { padding:30px 20px 28px !important; }" in rendered.html


def test_report_page_url_uses_configured_frontend_and_existing_report_route(monkeypatch):
    monkeypatch.setattr(delivery.settings, "frontend_url", "https://app.measurable.test/")
    monkeypatch.setattr(delivery.settings, "frontend_base_url", "https://fallback.invalid")
    assert delivery.report_page_url(97) == "https://app.measurable.test/reports/97"


def test_delivery_email_uses_frozen_report_data_and_inline_preview(factory, io):
    _, _, run_id, _ = completed(factory)
    deliver(factory, io)
    message = io.messages[0]
    assert message["subject"] == "Your report is ready"
    assert "Client performance" in message["html_body"]
    assert "Facebook Pages" in message["html_body"]
    assert "Reporting period" in message["html_body"] and "Reporting period" in message["text_body"]
    assert "cid:measurable-report-preview" in message["html_body"]
    assert "cid:measurable-logo" in message["html_body"]
    with factory() as db:
        report_id = db.get(ScheduledReportRun, run_id).report_id
    report_url = f"https://app.test/reports/{report_id}"
    assert message["html_body"].count(f'href="{report_url}"') == 2
    assert message["text_body"].count(report_url) == 2
    assert message["html_body"].count('href="https://private.test/pdf?signature=DO_NOT_LOG"') == 1
    assert len(message["inline_images"]) == 2
    assert message["inline_images"][0].content == b"\xff\xd8\xff email preview"
    assert message["inline_images"][0].filename == "report-preview.jpg"
    assert message["inline_images"][1].content == MEASURABLE_LOGO_PNG
    assert message["inline_images"][1].content_type == "image/png"
    assert message["inline_images"][1].filename == "measurable-logo.png"
    with factory() as db:
        assert db.query(Report).count() == db.query(ReportVersion).count() == db.query(ScheduledReportRun).count() == 1
        assert db.get(ScheduledReportRun, run_id).status == "SUCCEEDED"
        assert db.query(ReportGeneration).filter_by(state="consumed").count() == 1


def test_delivery_uses_locale_from_the_immutable_schedule_revision(factory, io):
    command, output = email_schedule(factory)
    with factory() as db:
        schedules.update_scheduled_report(
            output["id"],
            schedules.ScheduleUpdateInput(generation_options={
                "title": "Rendimiento semanal",
                "locale": "es",
                "ai_mode": "standard",
            }),
            command.workspace_id,
            db.get(User, command.actor_user_id),
            db,
        )
        db.get(ScheduledReport, output["id"]).next_run_at = execution._database_now(db) - timedelta(days=8)
        db.commit()
    claim = dispatch_claim(factory, output)
    execution.execute_run(factory, claim, refresher=refresh)
    deliver(factory, io)
    message = io.messages[0]
    assert message["subject"] == "Tu reporte está listo"
    assert "REPORTE PROGRAMADO" in message["html_body"]
    assert "Ver reporte" in message["html_body"] and "Chatea con tus datos" in message["html_body"]
    assert "Descargar PDF" in message["html_body"]
    assert "Tu enlace privado de descarga del PDF vence en 24 horas." in message["text_body"]


def test_preview_artifact_is_version_scoped_private_and_reused(factory, io):
    _, _, _, export_id = completed(factory)
    io.builder(factory, export_id)
    rendered = []

    def preview_renderer(payload):
        rendered.append(deepcopy(payload))
        return b"\xff\xd8\xff deterministic cover"

    first = artifacts.ensure_preview(factory, export_id, renderer=preview_renderer, storage=io.s3)
    second = artifacts.ensure_preview(factory, export_id, renderer=preview_renderer, storage=io.s3)
    assert first.id == second.id and first.content == second.content
    assert len(rendered) == 1
    preview_put = next(item for item in io.s3.puts if item["ContentType"] == "image/jpeg")
    assert preview_put["CacheControl"] == "private, no-store" and preview_put["IfNoneMatch"] == "*"
    assert preview_put["Metadata"]["snapshot"] and preview_put["Metadata"]["pdf-sha256"]
    with factory() as db:
        previews = db.query(Export).filter_by(artifact_type="PREVIEW").all()
        assert len(previews) == 1 and previews[0].report_version_id
        assert db.query(Report).count() == db.query(ReportVersion).count() == db.query(ScheduledReportRun).count() == 1
        assert db.query(ReportGeneration).filter_by(state="consumed").count() == 1


def test_version_snapshot_survives_same_version_edits_new_versions_and_branding(factory, io):
    _, _, run_id, export_id = completed(factory)
    with factory() as db:
        artifact = db.get(Export, export_id)
        frozen = deepcopy(artifact.render_snapshot_json)
        report = db.get(Report, artifact.report_id)
        report.name, report.description = "EDITED", '{"branding":{"brand_name":"CHANGED"}}'
        db.query(ReportBlock).filter_by(report_version_id=artifact.report_version_id).first().data_json = '{"text":"CHANGED"}'
        db.add(ReportVersion(report_id=report.id, version=2))
        db.commit()
    deliver(factory, io)
    assert io.renders == [frozen]
    with factory() as db:
        assert db.get(ScheduledReportRun, run_id).report_version_id == frozen["version"]["id"]
        assert db.get(Export, export_id).status == "READY"


def test_private_version_scoped_s3_pdf_and_secure_url(factory, io):
    command, _, _, export_id = completed(factory)
    deliver(factory, io)
    put = io.s3.puts[0]
    assert put["ContentType"] == "application/pdf" and "ACL" not in put
    assert put["IfNoneMatch"] == "*" and put["CacheControl"] == "private, no-store"
    assert put["Key"].startswith(f"workspaces/{command.workspace_id}/reports/")
    assert put["Key"].endswith(f"/exports/{export_id}.pdf")
    with factory() as db:
        row = db.get(Export, export_id)
        assert row.size_bytes == len(put["Body"]) and row.checksum_sha256
        assert row.storage_bucket == put["Bucket"] and row.output_s3_key == put["Key"]
        assert "signature" not in json.dumps(row.render_snapshot_json)
    assert io.s3.urls[0][1]["ExpiresIn"] == 86400


def test_generate_only_has_no_delivery_or_eager_pdf(factory, io):
    _, _, _, export_id = completed(factory, email=False)
    assert delivery.claim_delivery(factory) is None
    with factory() as db:
        assert db.query(ScheduledReportDelivery).count() == 0
        assert db.get(Export, export_id).status == "NOT_REQUESTED"


def test_exact_revision_recipients_survive_edit_and_retry(factory, io):
    command, output, run_id, _ = completed(factory)
    with factory() as db:
        original = db.get(ScheduledReportRun, run_id).configuration_revision
        schedules.update_scheduled_report(output["id"], schedules.ScheduleUpdateInput(delivery={"mode": "EMAIL_PDF", "recipients": ["replacement@example.com"]}),
            command.workspace_id, db.get(User, command.actor_user_id), db)
        assert db.get(ScheduledReport, output["id"]).configuration_revision == original + 1
        assert db.get(ScheduledReportRun, run_id).configuration_revision == original
    deliver(factory, io)
    assert io.messages[0]["recipients"] == ["original@example.com"]


@pytest.mark.parametrize("config", [
    {"mode": "EMAIL_PDF", "recipients": []}, {"mode": "EMAIL_PDF", "recipients": ["invalid"]},
    {"mode": "EMAIL_PDF", "recipients": ["a@example.com", "A@example.com"]},
    {"mode": "EMAIL_PDF", "recipients": [f"u{i}@example.com" for i in range(11)]},
    {"mode": "EMAIL_PDF", "recipients": ["a@example.com\r\nBcc:x@example.com"]},
    {"mode": "GENERATE_ONLY", "recipients": ["a@example.com"]},
    {"mode": "EMAIL_PDF", "recipients": ["a@example.com"], "access_token": "SECRET"},
])
def test_delivery_configuration_rejects_invalid_recipients_and_secrets(factory, client, config):
    command, output = make_schedule(factory)
    result = client.patch(url(command, output), json={"delivery": config}, headers=headers(command))
    assert result.status_code == 422
    assert "SECRET" not in result.text


def test_redundant_delivery_edit_does_not_create_revision(factory):
    command, output = make_schedule(factory)
    with factory() as db:
        result = schedules.update_scheduled_report(output["id"], schedules.ScheduleUpdateInput(delivery={"mode": "GENERATE_ONLY"}),
            command.workspace_id, db.get(User, command.actor_user_id), db)
        assert result["configuration_revision"] == 1


@pytest.mark.parametrize("stage", ["PDF", "S3", "SES"])
def test_delivery_failure_retry_never_regenerates_or_reconsumes(factory, io, stage):
    _, _, run_id, export_id = completed(factory)
    def fail(*args, **kwargs):
        raise RuntimeError("secret-provider-credentials")
    if stage == "PDF":
        io.builder = partial(artifacts.ensure_pdf, renderer=fail, storage=io.s3)
    elif stage == "S3":
        io.s3.failure = RuntimeError("secret-provider-credentials")
    sender = io.sender if stage != "SES" else lambda **kw: (_ for _ in ()).throw(ClientError({"Error": {"Code": "Throttling"}}, "SendEmail"))
    deliver(factory, io, sender=sender)
    with factory() as db:
        run = db.get(ScheduledReportRun, run_id)
        assert run.status == "SUCCEEDED"
        row = db.query(ScheduledReportDelivery).one()
        assert row.status == "PENDING" and row.attempt_count == 1
        assert "secret" not in str(execution.run_output(run))
    assert_single_generation(factory)
    io.s3.failure = None
    io.builder = partial(artifacts.ensure_pdf, renderer=io.renderer, storage=io.s3)
    retry_due(factory)
    deliver(factory, io)
    assert_single_generation(factory)
    assert len(io.renders) == (2 if stage == "S3" else 1)
    assert len(io.s3.puts) == len(io.messages) == 1
    with factory() as db:
        assert db.query(ScheduledReportDelivery).one().status == "DELIVERED"
        assert db.get(Export, export_id).status == "READY"
    assert delivery.claim_delivery(factory) is None


def test_final_allowance_success_delivers_then_quota_block_no_delivery(factory, io):
    command, output, run_id, _ = completed(factory, used=9)
    deliver(factory, io)
    with factory() as db:
        run = db.get(ScheduledReportRun, run_id)
        assert run.status == "SUCCEEDED" and run.quota_json["reports_used"] == 10 and run.quota_json["limit_reached"]
        schedule = db.get(ScheduledReport, output["id"])
        next_run, _ = execution.request_run_now(db, schedule, "next-quota-blocked")
        next_id = next_run.id
        db.commit()
    execution.execute_run(factory, execution.claim_run(factory, worker_id="test", run_id=next_id), refresher=refresh)
    with factory() as db:
        assert db.get(ScheduledReportRun, next_id).status == "QUOTA_BLOCKED"
        assert db.query(ScheduledReportDelivery).count() == 1
        assert db.query(Export).count() == 1
        assert db.get(ScheduledReport, output["id"]).status == "ACTIVE"
    assert_single_generation(factory, used=10)


def test_terminal_email_failure_does_not_disable_recurrence(factory, io):
    _, output, run_id, _ = completed(factory)
    def rejected(**kwargs):
        raise ClientError({"Error": {"Code": "MessageRejected", "Message": "private address"}}, "SendEmail")
    deliver(factory, io, sender=rejected)
    with factory() as db:
        assert db.get(ScheduledReportRun, run_id).status == "SUCCEEDED"
        assert db.query(ScheduledReportDelivery).one().status == "FAILED"
        assert db.get(ScheduledReport, output["id"]).status == "ACTIVE"
    assert delivery.claim_delivery(factory) is None


def test_retry_exhaustion_is_persisted_and_bounded(factory, io):
    completed(factory)
    def throttled(**kwargs):
        raise ClientError({"Error": {"Code": "Throttling"}}, "SendEmail")
    for _ in range(3):
        retry_due(factory)
        deliver(factory, io, sender=throttled)
    with factory() as db:
        assert db.query(ScheduledReportDelivery).one().status == "FAILED"
        assert db.query(ScheduledReportDelivery).one().attempt_count == 3
    assert delivery.claim_delivery(factory) is None
    assert len(io.renders) == len(io.s3.puts) == 1


@pytest.mark.parametrize("boundary", ["claim", "upload", "artifact"])
def test_restart_recovers_without_report_or_artifact_duplication(factory, io, boundary):
    _, _, _, export_id = completed(factory)
    claim = delivery.claim_delivery(factory)
    if boundary == "upload":
        original = io.s3.put_object
        def crash(**kwargs):
            original(**kwargs)
            raise KeyboardInterrupt("simulated hard crash after upload")
        io.s3.put_object = crash
        with pytest.raises(KeyboardInterrupt):
            io.builder(factory, export_id)
        io.s3.put_object = original
    elif boundary == "artifact":
        io.builder(factory, export_id)
    with factory() as db:
        now = execution._database_now(db)
        db.get(ScheduledReportDelivery, claim.id).lease_expires_at = now - timedelta(seconds=1)
        db.get(Export, export_id).lease_expires_at = now - timedelta(seconds=1)
        db.commit()
    deliver(factory, io)
    assert_single_generation(factory)
    assert len(io.renders) == len(io.s3.puts) == len(io.messages) == 1


@pytest.mark.parametrize("boundary", ["acceptance_crash", "timeout"])
def test_ambiguous_ses_acceptance_is_unknown_and_never_resent(factory, io, boundary):
    completed(factory)
    def ambiguous(**kwargs):
        io.messages.append(kwargs)
        if boundary == "acceptance_crash":
            raise KeyboardInterrupt("SES accepted but process died")
        raise ReadTimeoutError(endpoint_url="https://email.test")
    if boundary == "acceptance_crash":
        with pytest.raises(KeyboardInterrupt):
            deliver(factory, io, sender=ambiguous)
        with factory() as db:
            db.query(ScheduledReportDelivery).one().lease_expires_at = execution._database_now(db) - timedelta(seconds=1)
            db.commit()
    else:
        deliver(factory, io, sender=ambiguous)
    assert delivery.claim_delivery(factory) is None
    with factory() as db:
        row = db.query(ScheduledReportDelivery).one()
        assert row.status == "UNKNOWN" and row.error_code == "email_acceptance_unknown"
    assert len(io.messages) == 1
    assert_single_generation(factory)


def test_local_finalization_retry_never_resends(factory, io, monkeypatch):
    completed(factory)
    finish, calls = delivery._finish, []
    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("DB unavailable")
        return finish(*args, **kwargs)
    monkeypatch.setattr(delivery, "_finish", flaky)
    deliver(factory, io)
    assert len(calls) == 2 and len(io.messages) == 1
    with factory() as db:
        assert db.query(ScheduledReportDelivery).one().ses_message_id == "ses-message-1"


@pytest.mark.parametrize("fixture", ["factory", "postgres_factory"])
def test_concurrent_delivery_claim_unique_and_stale_worker_fenced(request, fixture):
    factory = request.getfixturevalue(fixture)
    completed(factory)
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: delivery.claim_delivery(factory), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    old = next(claim for claim in claims if claim)
    with factory() as db:
        db.get(ScheduledReportDelivery, old.id).lease_expires_at = execution._database_now(db) - timedelta(seconds=1)
        db.commit()
    new = delivery.claim_delivery(factory)
    assert new.token != old.token
    with pytest.raises(execution.LeaseLost):
        delivery._finish(factory, old, status="DELIVERED", message_id="old-message")


def test_heartbeat_renews_delivery_and_artifact_leases(factory, io):
    _, _, _, export_id = completed(factory)
    claim = delivery.claim_delivery(factory)
    def on_claim(export_id, token):
        with factory() as db:
            now = execution._database_now(db)
            db.get(Export, export_id).lease_expires_at = now + timedelta(seconds=1)
            db.get(ScheduledReportDelivery, claim.id).lease_expires_at = now + timedelta(seconds=1)
            db.commit()
        heartbeat = delivery.DeliveryHeartbeat(factory, claim)
        heartbeat.attach(export_id, token)
        with factory() as db:
            now = execution._database_now(db)
            assert execution._utc(db.get(Export, export_id).lease_expires_at) > now + timedelta(minutes=2)
            assert execution._utc(db.get(ScheduledReportDelivery, claim.id).lease_expires_at) > now + timedelta(minutes=2)
    io.builder(factory, export_id, on_claim=on_claim)


def test_run_now_reuses_report_pdf_delivery_and_preserves_cursor(factory, client, io):
    command, output = email_schedule(factory)
    with factory() as db:
        cursor = db.get(ScheduledReport, output["id"]).next_run_at
    request_headers = {**headers(command), "Idempotency-Key": "delivery-once"}
    endpoint = url(command, output, "/run-now")
    first = client.post(endpoint, headers=request_headers)
    assert first.status_code == 202, first.text
    claim = execution.claim_run(factory, worker_id="test", run_id=first.json()["id"])
    execution.execute_run(factory, claim, refresher=refresh)
    deliver(factory, io)
    repeat = client.post(endpoint, headers=request_headers)
    assert repeat.status_code == 200 and repeat.json()["id"] == first.json()["id"]
    assert repeat.json()["delivery"]["status"] == "DELIVERED"
    with factory() as db:
        assert db.get(ScheduledReport, output["id"]).next_run_at == cursor
        assert db.query(ScheduledReportDelivery).count() == 1
    assert delivery.claim_delivery(factory) is None
    assert_single_generation(factory)


def test_download_requires_workspace_access_and_returns_exact_pdf(factory, client, io, monkeypatch):
    command, output, run_id, export_id = completed(factory, email=False)
    monkeypatch.setattr(artifacts, "ensure_pdf", io.builder)
    monkeypatch.setattr(artifacts, "download_url", io.signer)
    endpoint = url(command, output, f"/runs/{run_id}/pdf")
    assert client.get(endpoint).status_code == 401
    second = seed(factory, provider="facebook_pages")
    assert client.get(endpoint, headers=headers(second)).status_code == 403
    foreign = endpoint.replace(f"workspace_id={command.workspace_id}", f"workspace_id={second.workspace_id}")
    assert client.get(foreign, headers=headers(second)).status_code == 404
    result = client.get(endpoint, headers=headers(command))
    assert result.status_code == 200, result.text
    assert result.json()["artifact_id"] == export_id and result.json()["expires_in"] == 900
    assert result.headers["cache-control"] == "private, no-store"
    history = client.get(url(command, output, "/runs"), headers=headers(command))
    assert "lease_token" not in history.text and "signature" not in history.text
    assert '"downloadable":true' in history.text
    assert len(io.renders) == 1 and len(io.messages) == 0


def test_snapshot_and_delivery_uniqueness(factory):
    _, _, run_id, export_id = completed(factory)
    with factory() as db:
        row = db.get(Export, export_id)
        db.add(Export(workspace_id=row.workspace_id, report_id=row.report_id, report_version_id=row.report_version_id, artifact_type="PDF"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(ScheduledReportDelivery(workspace_id=row.workspace_id, run_id=run_id))
        with pytest.raises(IntegrityError):
            db.commit()


def test_worker_once_resumes_persisted_delivery(factory, io, monkeypatch):
    completed(factory)
    monkeypatch.setattr(worker, "execute_delivery", lambda factory, claim: delivery.execute_delivery(factory, claim,
        artifact_builder=io.builder, preview_builder=io.preview_builder,
        sender=io.sender, signer=io.signer,
        viewer=lambda report_id: f"https://app.test/reports/{report_id}"))
    worker.run_worker(factory, once=True)
    assert len(io.messages) == 1
    assert_single_generation(factory)


def test_delivery_logs_omit_payload_recipients_and_download_signature(factory, io, caplog):
    completed(factory)
    with caplog.at_level("INFO"):
        deliver(factory, io)
    assert "DO_NOT_LOG" not in caplog.text and "original@example.com" not in caplog.text
    assert "ses-message-1" not in caplog.text  # Identifier is a structured extra, not free-text payload.


def test_crash_after_canonical_commit_recovers_delivery_without_regeneration(factory, io, monkeypatch):
    _, output = email_schedule(factory)
    claim = dispatch_claim(factory, output)
    original = execution.generate_report
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt("Crash after report commit")
    monkeypatch.setattr(execution, "generate_report", crash)
    with pytest.raises(KeyboardInterrupt):
        execution.execute_run(factory, claim, refresher=refresh)
    assert_single_generation(factory)
    with factory() as db:
        assert db.query(ScheduledReportDelivery).count() == 0
        db.get(ScheduledReportRun, claim.run_id).lease_expires_at = execution._database_now(db) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(execution, "generate_report", lambda *a, **kw: pytest.fail("Must reconcile existing report"))
    reclaimed = execution.claim_run(factory, worker_id="recovered")
    execution.execute_run(factory, reclaimed, refresher=lambda *a, **kw: pytest.fail("No fresh sync on committed-report recovery"))
    deliver(factory, io)
    assert_single_generation(factory)
    assert len(io.messages) == 1


def test_delivery_rejects_cross_workspace_artifact_before_io(factory, io):
    _, _, _, export_id = completed(factory)
    other = seed(factory)
    with factory() as db:
        db.get(Export, export_id).workspace_id = other.workspace_id
        db.commit()
    deliver(factory, io)
    assert not io.renders and not io.messages
    with factory() as db:
        assert db.query(ScheduledReportDelivery).one().error_code == "delivery_report_identity_invalid"


def test_transient_failure_does_not_block_next_occurrence(factory, io):
    _, output, _, _ = completed(factory)
    def throttled(**kwargs):
        raise ClientError({"Error": {"Code": "Throttling"}}, "SendEmail")
    deliver(factory, io, sender=throttled)
    with factory() as db:
        schedule = db.get(ScheduledReport, output["id"])
        schedule.next_run_at = execution._database_now(db) - timedelta(seconds=1)
        db.commit()
    new_ids = execution.dispatch_due(factory)
    assert len(new_ids) == 1
    execution.execute_run(factory, execution.claim_run(factory, worker_id="next", run_id=new_ids[0]), refresher=refresh)
    with factory() as db:
        assert db.get(ScheduledReportRun, new_ids[0]).status == "SUCCEEDED"
        assert db.query(Report).count() == 2 and db.query(ScheduledReportDelivery).count() == 2


def test_snapshot_failure_rolls_back_report_and_capacity(factory, monkeypatch):
    _, output = email_schedule(factory)
    monkeypatch.setattr(artifacts, "freeze_generated_version", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("snapshot write failed")))
    execution.execute_run(factory, dispatch_claim(factory, output), refresher=refresh)
    with factory() as db:
        assert db.query(Report).count() == 0 and db.query(Export).count() == 0
        assert db.query(ReportGeneration).one().state == "failed"
        assert db.query(ScheduledReportDelivery).count() == 0
