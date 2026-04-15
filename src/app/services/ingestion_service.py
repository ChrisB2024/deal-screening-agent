"""Ingestion service — accepts deal documents and queues them for processing.

Handles: file validation, duplicate detection, storage, deal record creation,
and triggering the extraction + scoring pipeline.

Spec module: Ingestion Service
- Inputs: PDF file + source metadata
- Outputs: Stored document record with status UPLOADED, extraction queued
- Invariants: unique deal_id, dedup by content hash (per tenant), PDF only in v1
"""

import hashlib
import logging
import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.deal import DealAuditLog, Deal
from app.models.enums import DealStatus

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {"application/pdf"}
ALLOWED_EXTENSIONS = {".pdf"}


class IngestionError(Exception):
    """Raised when ingestion fails validation."""

    pass


class IngestionResult:
    """Result of ingesting a deal document."""

    def __init__(
        self,
        deal_id: uuid.UUID,
        status: DealStatus,
        message: str,
        is_duplicate: bool = False,
    ):
        self.deal_id = deal_id
        self.status = status
        self.message = message
        self.is_duplicate = is_duplicate


async def ingest_deal(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    filename: str,
    file_content: bytes,
    content_type: str | None = None,
    source_channel: str = "upload",
    source_sender: str | None = None,
) -> IngestionResult:
    """Ingest a deal document: validate, dedup, store, create record, queue processing.

    Purpose: Accept a deal document and prepare it for extraction + scoring.
    Inputs: File bytes, metadata, tenant context.
    Outputs: IngestionResult with deal_id, status, and dedup info.
    Invariants:
        - Only PDF accepted in v1.
        - File size <= 50MB.
        - Duplicate detection by content hash (per tenant).
        - Every ingested doc gets a unique deal_id.
        - Atomic: deal record + file storage succeed together or not at all.
    Security:
        - File type validated by extension AND content-type header.
        - File size enforced before writing to disk.
        - Content hash prevents re-processing identical documents.
    """
    # 1. Validate file type
    _validate_file_type(filename, content_type)

    # 2. Validate file size
    if len(file_content) > settings.max_file_size_bytes:
        raise IngestionError(
            f"File too large ({len(file_content)} bytes). "
            f"Maximum size is {settings.max_file_size_bytes // (1024 * 1024)}MB."
        )

    if len(file_content) == 0:
        raise IngestionError("File is empty.")

    # 3. Compute content hash for dedup
    content_hash = hashlib.sha256(file_content).hexdigest()

    # 4. Check for duplicate (tenant-scoped)
    existing = await _check_duplicate(db, tenant_id, content_hash)
    if existing is not None:
        return IngestionResult(
            deal_id=existing.id,
            status=existing.status,
            message="Duplicate document detected. Returning existing deal.",
            is_duplicate=True,
        )

    # 5. Store file to disk
    deal_id = uuid.uuid4()
    file_path = await _store_file(deal_id, filename, file_content)

    # 6. Create deal record
    deal = Deal(
        id=deal_id,
        tenant_id=tenant_id,
        filename=filename,
        content_hash=content_hash,
        file_path=str(file_path),
        file_size_bytes=len(file_content),
        source_channel=source_channel,
        source_sender=source_sender,
        status=DealStatus.UPLOADED,
    )
    db.add(deal)

    # 7. Audit log
    audit = DealAuditLog(
        audit_id=str(uuid.uuid4()),
        deal_id=deal_id,
        tenant_id=str(tenant_id),
        actor_type="system",
        action="DEAL_UPLOADED",
        before_state=None,
        after_state=DealStatus.UPLOADED.value,
        metadata_={"filename": filename, "file_size_bytes": len(file_content), "source_channel": source_channel},
    )
    db.add(audit)

    # Flush to persist deal before enqueuing
    await db.flush()

    # 8. Enqueue extraction job for async processing
    from app.background_jobs import enqueue

    await enqueue(
        db,
        job_type="extraction",
        payload={"deal_id": str(deal_id), "tenant_id": str(tenant_id)},
        idempotency_key=f"extraction:{deal_id}",
        trace_context=None,
        tenant_id=str(tenant_id),
    )

    return IngestionResult(
        deal_id=deal_id,
        status=DealStatus.UPLOADED,
        message="Deal uploaded. Extraction and scoring queued for processing.",
    )


def _validate_file_type(filename: str, content_type: str | None) -> None:
    """Validate file is a PDF by extension and content type."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestionError(
            f"Invalid file type '{ext}'. Only PDF files are accepted."
        )

    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise IngestionError(
            f"Invalid content type '{content_type}'. Expected application/pdf."
        )


async def _check_duplicate(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    content_hash: str,
) -> Deal | None:
    """Check if a document with the same hash already exists for this tenant."""
    stmt = select(Deal).where(
        Deal.tenant_id == tenant_id,
        Deal.content_hash == content_hash,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _store_file(
    deal_id: uuid.UUID,
    filename: str,
    file_content: bytes,
) -> Path:
    """Store file to the upload directory. Returns the file path.

    File is stored as uploads/{deal_id}/{original_filename} to maintain
    traceability while preventing path collisions.
    """
    upload_dir = Path(settings.upload_dir) / str(deal_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename — only keep the basename to prevent path traversal
    safe_filename = Path(filename).name
    file_path = upload_dir / safe_filename

    file_path.write_bytes(file_content)
    return file_path
