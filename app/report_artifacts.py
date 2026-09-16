"""Immutable PDF inputs and private, conditional S3 storage shared by export callers."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import settings
from .models import Export, Report, ReportVersion
from .report_generation import _database_now, _utc

RENDERER_VERSION = "share-slide-screenshots-frozen-v1"
PREVIEW_RENDERER_VERSION = "share-cover-screenshot-frozen-v1"
ARTIFACT_LEASE = timedelta(minutes=3)
MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_PREVIEW_BYTES = 5 * 1024 * 1024


class ArtifactError(Exception):
    def __init__(self, code, *, retryable=True):
        self.code, self.retryable = code, retryable
        super().__init__(code)


def snapshot_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def freeze_generated_version(db, report_id, version_id):
    """Called inside canonical finalization, before the report becomes editable.

    ReportVersion blocks are mutable in the legacy editor. Freeze the existing
    public render contract, including branding/metadata, not just a version FK.
    This does not render, upload, or acquire delivery quota.
    """
    from .main import _public_report_out
    report, version = db.get(Report, report_id), db.get(ReportVersion, version_id)
    payload = _public_report_out(db, report, version).model_dump(mode="json")
    artifact = Export(workspace_id=report.workspace_id, report_id=report_id, report_version_id=version_id,
        artifact_type="PDF", status="NOT_REQUESTED", render_snapshot_json=payload,
        snapshot_hash=snapshot_hash(payload), renderer_version=RENDERER_VERSION, content_type="application/pdf")
    db.add(artifact)
    db.flush()
    return artifact


class PinnedRenderRequest:
    """The existing share page fetches this payload client-side; no public token exists.

    Never fall back to the backend share resolver. A changed frontend fetch contract
    must fail closed rather than render the latest report. No credentials enter Chromium.
    """
    def __init__(self, payload, token):
        self.payload, self.token, self.consumed = payload, token, False

    def install(self, context):
        context.route("**/public/reports/**", self.handle)

    def handle(self, route):
        request = route.request
        if urlsplit(request.url).path != f"/public/reports/{self.token}" or request.method not in {"GET", "OPTIONS"}:
            route.abort()
            return
        headers = {"Access-Control-Allow-Origin": request.headers.get("origin", "null"), "Cache-Control": "no-store",
                   "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "authorization, content-type",
                   "Access-Control-Allow-Methods": "GET, OPTIONS"}
        if request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        self.consumed = True
        route.fulfill(status=200, content_type="application/json", headers=headers, body=json.dumps(self.payload))


def render_pdf(payload):
    from .main import _pdf_render_base_url
    from .services import generate_pdf_from_export_page
    from .scheduled_report_refresh import private_provider_io
    token = "frozen-export-" + uuid4().hex
    pinned = PinnedRenderRequest(payload, token)
    url = f"{_pdf_render_base_url()}/share/reports/{token}?export=pdf"
    # Legacy renderer forwards browser console/URLs. Keep those out of worker logs.
    with private_provider_io():
        data, _ = generate_pdf_from_export_page(export_url=url, report_id=payload["report"]["id"], pinned_request=pinned)
    if not pinned.consumed:
        raise ArtifactError("pdf_pinned_payload_not_consumed", retryable=False)
    if not data.startswith(b"%PDF") or not 0 < len(data) <= MAX_PDF_BYTES:
        raise ArtifactError("invalid_pdf_artifact", retryable=False)
    return data


def render_preview(payload):
    from .main import _pdf_render_base_url
    from .services import generate_thumbnail_from_export_page
    from .scheduled_report_refresh import private_provider_io
    token = "frozen-preview-" + uuid4().hex
    pinned = PinnedRenderRequest(payload, token)
    url = f"{_pdf_render_base_url()}/share/reports/{token}?export=pdf"
    with private_provider_io():
        data, _ = generate_thumbnail_from_export_page(
            export_url=url,
            report_id=payload["report"]["id"],
            pinned_request=pinned,
            image_type="jpeg",
        )
    if not pinned.consumed:
        raise ArtifactError("preview_pinned_payload_not_consumed", retryable=False)
    if not data.startswith(b"\xff\xd8\xff") or not 0 < len(data) <= MAX_PREVIEW_BYTES:
        raise ArtifactError("invalid_preview_artifact", retryable=False)
    return data


def s3_client():
    return boto3.client("s3", region_name=settings.aws_region,
        config=Config(connect_timeout=10, read_timeout=60, retries={"total_max_attempts": 2}))


@dataclass(frozen=True)
class ArtifactIdentity:
    id: int
    bucket: str
    key: str
    report_id: int
    version_id: int


def identity(row):
    return ArtifactIdentity(row.id, row.storage_bucket, row.output_s3_key, row.report_id, row.report_version_id)


@dataclass(frozen=True)
class PreviewArtifact:
    id: int
    content: bytes
    content_type: str
    checksum_sha256: str


def _owned(db, export_id, token):
    row = db.query(Export).filter(Export.id == export_id).with_for_update().populate_existing().one()
    if row.status != "RENDERING" or row.lease_token != token or _utc(row.lease_expires_at) <= _database_now(db):
        raise ArtifactError("artifact_lease_lost")
    return row


def renew_artifact(db, export_id, token):
    row = _owned(db, export_id, token)
    row.lease_expires_at = _database_now(db) + ARTIFACT_LEASE


def _head(s3, artifact, fingerprint):
    try:
        head = s3.head_object(Bucket=artifact.bucket, Key=artifact.key)
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    metadata = head.get("Metadata", {})
    if metadata.get("snapshot") != fingerprint or not metadata.get("sha256") or head.get("ContentType") != "application/pdf" or not 0 < head.get("ContentLength", 0) <= MAX_PDF_BYTES:
        raise ArtifactError("artifact_storage_identity_mismatch", retryable=False)
    return head


def ensure_pdf(factory, export_id, *, renderer=render_pdf, storage=None, on_claim=None):
    from .scheduled_report_execution import _write
    with factory() as db:
        _write(db)
        row = db.query(Export).filter(Export.id == export_id, Export.artifact_type == "PDF").with_for_update().one()
        if row.report_id is None or row.report_version_id is None:
            raise ArtifactError("artifact_report_deleted", retryable=False)
        if row.status == "READY":
            return identity(row)
        if row.status == "RENDERING" and _utc(row.lease_expires_at) > _database_now(db):
            raise ArtifactError("artifact_in_progress")
        payload = deepcopy(row.render_snapshot_json)
        if not payload or snapshot_hash(payload) != row.snapshot_hash or payload["version"]["id"] != row.report_version_id or payload["report"]["id"] != row.report_id:
            raise ArtifactError("artifact_snapshot_invalid", retryable=False)
        if row.renderer_version != RENDERER_VERSION:
            raise ArtifactError("artifact_renderer_unsupported", retryable=False)
        token = str(uuid4())
        row.status, row.lease_token, row.lease_expires_at = "RENDERING", token, _database_now(db) + ARTIFACT_LEASE
        row.storage_bucket = row.storage_bucket or settings.s3_outputs_bucket
        row.output_s3_key = row.output_s3_key or f"workspaces/{row.workspace_id}/reports/{row.report_id}/versions/{row.report_version_id}/exports/{row.id}.pdf"
        artifact, fingerprint = identity(row), row.snapshot_hash
        db.commit()
    try:
        if on_claim:
            on_claim(export_id, token)
        s3 = storage or s3_client()
        head = _head(s3, artifact, fingerprint)
        if head is None:
            pdf = renderer(payload)
            if not pdf.startswith(b"%PDF") or len(pdf) > MAX_PDF_BYTES:
                raise ArtifactError("invalid_pdf_artifact", retryable=False)
            checksum = hashlib.sha256(pdf).hexdigest()
            try:
                # Conditional writes prevent a stale renderer overwriting a winner's object.
                s3.put_object(Bucket=artifact.bucket, Key=artifact.key, Body=pdf,
                    ContentType="application/pdf", CacheControl="private, no-store", IfNoneMatch="*",
                    Metadata={"snapshot": fingerprint, "sha256": checksum, "renderer": RENDERER_VERSION})
            except ClientError as exc:
                if str(exc.response.get("Error", {}).get("Code")) not in {"PreconditionFailed", "412"}:
                    raise
            head = _head(s3, artifact, fingerprint)
        if head is None:
            raise ArtifactError("artifact_upload_not_visible")
        with factory() as db:
            row = _owned(db, export_id, token)
            row.status, row.completed_at, row.error_code = "READY", _database_now(db), None
            row.size_bytes, row.checksum_sha256 = head["ContentLength"], head["Metadata"]["sha256"]
            row.lease_token = row.lease_expires_at = None
            db.commit()
        return artifact
    except Exception as exc:
        with factory() as db:
            db.query(Export).filter(Export.id == export_id, Export.lease_token == token).update({
                "status": "FAILED", "error_code": exc.code if isinstance(exc, ArtifactError) else "pdf_storage_or_render_failed",
                "lease_token": None, "lease_expires_at": None}, synchronize_session=False)
            db.commit()
        raise


def _preview_head(s3, artifact, fingerprint, pdf_checksum):
    try:
        head = s3.head_object(Bucket=artifact.bucket, Key=artifact.key)
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    metadata = head.get("Metadata", {})
    if (metadata.get("snapshot") != fingerprint or metadata.get("pdf-sha256") != pdf_checksum
            or not metadata.get("sha256") or head.get("ContentType") != "image/jpeg"
            or not 0 < head.get("ContentLength", 0) <= MAX_PREVIEW_BYTES):
        raise ArtifactError("preview_storage_identity_mismatch", retryable=False)
    return head


def _preview_bytes(s3, artifact):
    response = s3.get_object(Bucket=artifact.bucket, Key=artifact.key)
    body = response["Body"]
    data = body.read() if hasattr(body, "read") else body
    if not isinstance(data, bytes) or not data.startswith(b"\xff\xd8\xff") or not 0 < len(data) <= MAX_PREVIEW_BYTES:
        raise ArtifactError("invalid_preview_artifact", retryable=False)
    return data


def ensure_preview(factory, pdf_export_id, *, renderer=render_preview, storage=None):
    """Create/reuse the private cover image for one immutable PDF version.

    PREVIEW uses the existing Export lifecycle rather than adding a second report,
    version, quota event, or schema. Its deterministic row/key is private and is
    exposed to recipients only as an inline CID attachment.
    """
    from .scheduled_report_execution import _write
    with factory() as db:
        _write(db)
        pdf = db.query(Export).filter(Export.id == pdf_export_id, Export.artifact_type == "PDF").with_for_update().one()
        if pdf.status != "READY" or not pdf.report_id or not pdf.report_version_id or not pdf.checksum_sha256:
            raise ArtifactError("preview_pdf_unavailable")
        previews = db.query(Export).filter_by(
            workspace_id=pdf.workspace_id,
            report_id=pdf.report_id,
            report_version_id=pdf.report_version_id,
            artifact_type="PREVIEW",
        ).with_for_update().all()
        if len(previews) > 1:
            raise ArtifactError("preview_identity_duplicated", retryable=False)
        if previews:
            row = previews[0]
        else:
            row = Export(
                workspace_id=pdf.workspace_id,
                report_id=pdf.report_id,
                report_version_id=pdf.report_version_id,
                artifact_type="PREVIEW",
                status="NOT_REQUESTED",
                render_snapshot_json=deepcopy(pdf.render_snapshot_json),
                snapshot_hash=pdf.snapshot_hash,
                renderer_version=PREVIEW_RENDERER_VERSION,
                content_type="image/jpeg",
            )
            db.add(row)
            db.flush()
        if (row.snapshot_hash != pdf.snapshot_hash or row.render_snapshot_json != pdf.render_snapshot_json
                or row.renderer_version != PREVIEW_RENDERER_VERSION):
            raise ArtifactError("preview_snapshot_invalid", retryable=False)
        if row.status == "RENDERING" and _utc(row.lease_expires_at) > _database_now(db):
            raise ArtifactError("artifact_in_progress")
        token = str(uuid4())
        row.status, row.lease_token = "RENDERING", token
        row.lease_expires_at = _database_now(db) + ARTIFACT_LEASE
        row.storage_bucket = row.storage_bucket or pdf.storage_bucket or settings.s3_outputs_bucket
        row.output_s3_key = row.output_s3_key or (
            f"workspaces/{row.workspace_id}/reports/{row.report_id}/versions/"
            f"{row.report_version_id}/previews/{row.id}.jpg"
        )
        preview_id, payload, fingerprint, pdf_checksum = row.id, deepcopy(row.render_snapshot_json), row.snapshot_hash, pdf.checksum_sha256
        artifact = identity(row)
        db.commit()
    try:
        s3 = storage or s3_client()
        head = _preview_head(s3, artifact, fingerprint, pdf_checksum)
        if head is None:
            content = renderer(payload)
            if not content.startswith(b"\xff\xd8\xff") or len(content) > MAX_PREVIEW_BYTES:
                raise ArtifactError("invalid_preview_artifact", retryable=False)
            checksum = hashlib.sha256(content).hexdigest()
            try:
                s3.put_object(
                    Bucket=artifact.bucket,
                    Key=artifact.key,
                    Body=content,
                    ContentType="image/jpeg",
                    CacheControl="private, no-store",
                    IfNoneMatch="*",
                    Metadata={
                        "snapshot": fingerprint,
                        "pdf-sha256": pdf_checksum,
                        "sha256": checksum,
                        "renderer": PREVIEW_RENDERER_VERSION,
                    },
                )
            except ClientError as exc:
                if str(exc.response.get("Error", {}).get("Code")) not in {"PreconditionFailed", "412"}:
                    raise
            head = _preview_head(s3, artifact, fingerprint, pdf_checksum)
        if head is None:
            raise ArtifactError("preview_upload_not_visible")
        content = _preview_bytes(s3, artifact)
        if hashlib.sha256(content).hexdigest() != head["Metadata"]["sha256"]:
            raise ArtifactError("preview_storage_identity_mismatch", retryable=False)
        with factory() as db:
            row = _owned(db, preview_id, token)
            row.status, row.completed_at, row.error_code = "READY", _database_now(db), None
            row.size_bytes, row.checksum_sha256 = head["ContentLength"], head["Metadata"]["sha256"]
            row.lease_token = row.lease_expires_at = None
            db.commit()
        return PreviewArtifact(preview_id, content, "image/jpeg", head["Metadata"]["sha256"])
    except Exception as exc:
        with factory() as db:
            db.query(Export).filter(Export.id == preview_id, Export.lease_token == token).update({
                "status": "FAILED",
                "error_code": exc.code if isinstance(exc, ArtifactError) else "preview_storage_or_render_failed",
                "lease_token": None,
                "lease_expires_at": None,
            }, synchronize_session=False)
            db.commit()
        raise


def download_url(artifact, *, expires=900, storage=None):
    if not 1 <= expires <= 86400:
        raise ValueError("Download expiry must be between one second and one day.")
    return (storage or s3_client()).generate_presigned_url("get_object",
        Params={"Bucket": artifact.bucket, "Key": artifact.key,
                "ResponseContentType": "application/pdf", "ResponseContentDisposition": f'attachment; filename="report-{artifact.report_id}-version-{artifact.version_id}.pdf"'},
        ExpiresIn=expires)


def report_view_url(artifact, *, expires=900, storage=None):
    if not 1 <= expires <= 86400:
        raise ValueError("View expiry must be between one second and one day.")
    return (storage or s3_client()).generate_presigned_url("get_object",
        Params={"Bucket": artifact.bucket, "Key": artifact.key,
                "ResponseContentType": "application/pdf", "ResponseContentDisposition": 'inline; filename="measurable-report.pdf"'},
        ExpiresIn=expires)


def email_download_url(artifact, *, expires=900, storage=None):
    if not 1 <= expires <= 86400:
        raise ValueError("Download expiry must be between one second and one day.")
    return (storage or s3_client()).generate_presigned_url("get_object",
        Params={"Bucket": artifact.bucket, "Key": artifact.key,
                "ResponseContentType": "application/pdf", "ResponseContentDisposition": 'attachment; filename="measurable-report.pdf"'},
        ExpiresIn=expires)
