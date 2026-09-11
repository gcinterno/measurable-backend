"""Independent post-generation retries. This module must never call generate_report.

SES SendEmail has no idempotency token. Commit EMAIL_SENDING before the call;
unknown acceptance (timeout/crash) is terminal UNKNOWN, not an automatic resend.
DELIVERED means accepted by SES, not a verified recipient inbox receipt.
"""
from dataclasses import dataclass
from datetime import timedelta
from html import escape
import logging
from threading import Event, Lock, Thread
from uuid import uuid4

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from fastapi import HTTPException
from sqlalchemy import and_, or_

from .models import Export, ScheduledReportDelivery
from .report_artifacts import ArtifactError, download_url, ensure_pdf, identity, renew_artifact
from .report_generation import _database_now, _utc
from .scheduled_report_execution import _write, LeaseLost
from .scheduled_report_models import ScheduledReportRevision, ScheduledReportRun
from .scheduled_report_refresh import private_provider_io
from .services import send_email_message

logger = logging.getLogger(__name__)
DELIVERY_LEASE = timedelta(minutes=3)
MAX_ATTEMPTS = 3
RETRY_DELAYS = (60, 300)


def delivery_configuration(db, run):
    from .scheduled_reports import DeliveryInput
    revision = db.get(ScheduledReportRevision, (run.schedule_id, run.workspace_id, run.configuration_revision))
    return DeliveryInput.model_validate(revision.snapshot_json.get("delivery", {}))


def prepare_delivery(db, run):
    if delivery_configuration(db, run).mode == "GENERATE_ONLY":
        return
    existing = db.query(ScheduledReportDelivery).filter_by(run_id=run.id).first()
    if existing is not None:
        return
    artifact = db.query(Export).filter_by(artifact_type="PDF", workspace_id=run.workspace_id,
        report_id=run.report_id, report_version_id=run.report_version_id).first()
    db.add(ScheduledReportDelivery(workspace_id=run.workspace_id, run_id=run.id,
        export_id=artifact.id if artifact else None, status="PENDING" if artifact else "FAILED",
        error_code=None if artifact else "render_snapshot_unavailable"))


def delivery_output(db, run):
    config = delivery_configuration(db, run)
    delivery = db.query(ScheduledReportDelivery).filter_by(run_id=run.id, workspace_id=run.workspace_id).first()
    artifact = db.query(Export).filter_by(artifact_type="PDF", workspace_id=run.workspace_id,
        report_id=run.report_id, report_version_id=run.report_version_id).first() if run.report_version_id else None
    return {"mode": config.mode, "status": delivery.status if delivery else "NOT_REQUESTED",
            "artifact_id": artifact.id if artifact else None, "pdf_status": artifact.status if artifact else "NOT_REQUESTED",
            "downloadable": bool(artifact and artifact.status == "READY"),
            "attempt_count": delivery.attempt_count if delivery else 0,
            "delivered_at": _utc(delivery.delivered_at) if delivery and delivery.delivered_at else None,
            "next_attempt_at": _utc(delivery.next_attempt_at) if delivery and delivery.next_attempt_at else None,
            "error_code": delivery.error_code if delivery else None}


@dataclass(frozen=True)
class DeliveryClaim:
    id: int
    token: str


def claim_delivery(factory):
    with factory() as db:
        _write(db)
        now = _database_now(db)
        row = db.query(ScheduledReportDelivery).filter(or_(
            and_(ScheduledReportDelivery.status == "PENDING", or_(ScheduledReportDelivery.next_attempt_at.is_(None), ScheduledReportDelivery.next_attempt_at <= now)),
            and_(ScheduledReportDelivery.status.in_(["RENDERING_PDF", "EMAIL_SENDING"]), ScheduledReportDelivery.lease_expires_at <= now)
        )).order_by(ScheduledReportDelivery.created_at, ScheduledReportDelivery.id).with_for_update(skip_locked=True).first()
        if row is None:
            return None
        if row.status == "EMAIL_SENDING":
            row.status, row.error_code, row.completed_at = "UNKNOWN", "email_acceptance_unknown", now
            row.lease_token = row.lease_expires_at = None
            db.commit()
            return None
        if row.attempt_count >= MAX_ATTEMPTS:
            row.status, row.error_code, row.completed_at = "FAILED", "delivery_retry_exhausted", now
            row.lease_token = row.lease_expires_at = None
            db.commit()
            return None
        row.status, row.lease_token = "RENDERING_PDF", str(uuid4())
        row.attempt_count += 1
        row.next_attempt_at = None
        row.lease_expires_at, row.heartbeat_at = now + DELIVERY_LEASE, now
        claim = DeliveryClaim(row.id, row.lease_token)
        db.commit()
        return claim


def owned_delivery(db, claim):
    row = db.query(ScheduledReportDelivery).filter_by(id=claim.id).with_for_update().populate_existing().one()
    if row.status not in {"RENDERING_PDF", "EMAIL_SENDING"} or row.lease_token != claim.token or _utc(row.lease_expires_at) <= _database_now(db):
        raise LeaseLost()
    return row


class DeliveryHeartbeat:
    def __init__(self, factory, claim, interval=20):
        self.factory, self.claim, self.interval = factory, claim, interval
        self.stop, self.lost, self.mutex = Event(), Event(), Lock()
        self.artifact = None
        self.thread = Thread(target=self._loop, daemon=True, name=f"delivery-heartbeat-{claim.id}")

    def pulse(self):
        with self.mutex, self.factory() as db:
            row = owned_delivery(db, self.claim)
            if self.artifact:
                artifact = db.get(Export, self.artifact[0])
                if artifact is not None and artifact.status != "READY":
                    renew_artifact(db, *self.artifact)
            row.heartbeat_at = _database_now(db)
            row.lease_expires_at = row.heartbeat_at + DELIVERY_LEASE
            db.commit()

    def attach(self, export_id, token):
        with self.mutex:
            self.artifact = (export_id, token)
        self.pulse()

    def _loop(self):
        while not self.stop.wait(self.interval):
            try:
                self.pulse()
            except Exception:
                self.lost.set()
                return

    def __enter__(self):
        self.pulse()
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


def _finish(factory, claim, *, status, error=None, retryable=False, message_id=None):
    with factory() as db:
        row = owned_delivery(db, claim)
        now = _database_now(db)
        if retryable and row.attempt_count < MAX_ATTEMPTS:
            row.status, row.next_attempt_at = "PENDING", now + timedelta(seconds=RETRY_DELAYS[row.attempt_count - 1])
        else:
            row.status, row.completed_at = status, now
        row.error_code = error
        if message_id:
            row.ses_message_id, row.delivered_at = message_id, now
        row.lease_token = row.lease_expires_at = None
        run = db.get(ScheduledReportRun, row.run_id)
        logger.info("scheduled_report_delivery", extra={"workspace_id": row.workspace_id,
            "scheduled_report_run_id": row.run_id, "artifact_id": row.export_id,
            "report_id": run.report_id, "report_version_id": run.report_version_id,
            "stage": row.status, "attempt_number": row.attempt_count, "result": error or row.status,
            "ses_message_id": message_id})
        db.commit()


def _failure(exc, sending):
    if isinstance(exc, ArtifactError):
        return "FAILED", exc.code, exc.retryable
    if isinstance(exc, NoCredentialsError):
        return "FAILED", "delivery_credentials_unavailable", False
    if isinstance(exc, EndpointConnectionError):
        return "FAILED", "delivery_endpoint_unavailable", True
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if sending and (exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) >= 500 or code in {"RequestTimeout", "InternalError", "ServiceUnavailable"}):
            return "UNKNOWN", "email_acceptance_unknown", False
        if code in {"Throttling", "ThrottlingException", "TooManyRequestsException", "ServiceUnavailable", "SlowDown", "RequestTimeout", "InternalError"}:
            return "FAILED", "email_provider_retryable" if sending else "artifact_storage_retryable", True
        return "FAILED", "email_provider_rejected" if sending else "artifact_storage_rejected", False
    if isinstance(exc, HTTPException):
        return "FAILED", "email_configuration_unavailable" if sending else "pdf_render_failed", not sending and exc.status_code >= 500
    # A socket timeout or crash after SendEmail may mean SES already accepted it.
    return ("UNKNOWN", "email_acceptance_unknown", False) if sending else ("FAILED", "pdf_storage_or_render_failed", True)


def execute_delivery(factory, claim, *, artifact_builder=ensure_pdf, sender=send_email_message, signer=download_url, heartbeat_interval=20):
    sending = False
    try:
        with DeliveryHeartbeat(factory, claim, interval=heartbeat_interval) as heartbeat:
            with factory() as db:
                row = owned_delivery(db, claim)
                run = db.get(ScheduledReportRun, row.run_id)
                artifact = db.get(Export, row.export_id) if row.export_id else None
                if run.workspace_id != row.workspace_id or run.status != "SUCCEEDED" or artifact is None or artifact.workspace_id != run.workspace_id or artifact.report_id != run.report_id or artifact.report_version_id != run.report_version_id:
                    raise ArtifactError("delivery_report_identity_invalid", retryable=False)
                config = delivery_configuration(db, run)
                if config.mode != "EMAIL_PDF":
                    raise ArtifactError("delivery_configuration_invalid", retryable=False)
                recipients = [str(email) for email in config.recipients]
                export_id = artifact.id
                # The immutable report title is used instead of the mutable schedule name.
                title = artifact.render_snapshot_json["report"]["title"]
                period = f"{run.reporting_start_date.isoformat()} to {run.reporting_end_date.isoformat()}"
            with private_provider_io():
                artifact = artifact_builder(factory, export_id, on_claim=heartbeat.attach)
                url = signer(artifact, expires=86400)
            html = f'<p>Your scheduled marketing report is ready: {escape(title)}.</p><p>Reporting period: {escape(period)}.</p><p><a href="{escape(url, quote=True)}">Download PDF</a></p><p>This private download link expires within 24 hours.</p>'
            text = f"Your scheduled marketing report is ready: {title}.\nReporting period: {period}.\nDownload PDF: {url}\nThis private download link expires within 24 hours."
            with factory() as db:
                row = owned_delivery(db, claim)
                if heartbeat.lost.is_set():
                    raise LeaseLost()
                row.status, row.send_started_at = "EMAIL_SENDING", _database_now(db)
                db.commit()
            sending = True
            # Never allow SDK retries of an ambiguously accepted send.
            with private_provider_io():
                message_id = sender(recipients=recipients, subject="Your scheduled marketing report is ready",
                    html_body=html, text_body=text, purpose="scheduled_report", single_attempt=True)
            if not message_id:
                raise RuntimeError("Missing SES acceptance identity")
            # Retry local finalization only. No more S3/PDF/SES calls after acceptance.
            for attempt in range(3):
                try:
                    _finish(factory, claim, status="DELIVERED", message_id=message_id)
                    return
                except LeaseLost:
                    raise
                except Exception:
                    if attempt == 2:
                        raise
    except LeaseLost:
        logger.warning("scheduled_delivery_ownership_lost", extra={"delivery_id": claim.id})
    except Exception as exc:
        status, code, retryable = _failure(exc, sending)
        try:
            _finish(factory, claim, status=status, error=code, retryable=retryable)
        except Exception:
            # Persisted lease recovery handles DB outage without automatically resending.
            logger.error("scheduled_delivery_finalization_unavailable", extra={"delivery_id": claim.id})
