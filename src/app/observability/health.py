"""Health check and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["health"])


@router.get("/health/liveness")
async def liveness():
    return {"status": "alive"}


@router.get("/health/readiness")
async def readiness():
    # TODO: Check DB connectivity, secrets loaded, exporters ready
    return {"status": "ready"}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    # TODO: Wire up prometheus_client registry once instrumentation is added
    return ""
