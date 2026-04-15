"""Audit log writer.

Writes deal_audit_log rows in the caller's DB transaction so the audit
record and the state change are atomic (spec invariant #4).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .logger import get_logger, request_id_var, trace_id_var

_logger = get_logger("audit")


async def record(
    deal_id: str,
    action: str,
    actor_type: str,
    actor_id: str | None,
    before_state: str | None,
    after_state: str | None,
    metadata: dict[str, Any],
    db_session: AsyncSession,
    tenant_id: str = "default",
) -> str:
    """Insert an audit row in the caller's transaction.

    Returns the audit_id (ULID-style UUID for now).
    """
    audit_id = str(uuid4())
    import json
    await db_session.execute(
        text("""
            INSERT INTO deal_audit_log
                (audit_id, deal_id, tenant_id, actor_type, actor_id,
                 action, before_state, after_state, metadata,
                 request_id, trace_id, created_at)
            VALUES
                (:audit_id, :deal_id, :tenant_id, :actor_type, :actor_id,
                 :action, :before_state, :after_state, :metadata::jsonb,
                 :request_id, :trace_id, :created_at)
        """),
        {
            "audit_id": audit_id,
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "action": action,
            "before_state": before_state,
            "after_state": after_state,
            "metadata": json.dumps(metadata),
            "request_id": request_id_var.get(),
            "trace_id": trace_id_var.get(),
            "created_at": datetime.now(timezone.utc),
        },
    )

    _logger.info(
        "audit.recorded",
        audit_id=audit_id,
        deal_id=deal_id,
        action=action,
        actor_type=actor_type,
        before_state=before_state,
        after_state=after_state,
    )
    return audit_id
