"""FastAPI application entry point.

Mounts the deal and criteria API routers.
Adds request_id middleware per spec technical invariant:
"API responses must always include a request_id for traceability."
"""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deals import router as deals_router
from app.api.criteria import router as criteria_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Deal Screening Agent",
    description="AI-powered deal screening for investment firms",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request_id to every response for traceability (spec technical invariant)."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return structured error objects on failure (spec technical invariant)."""
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=500,
        headers={"X-Request-ID": request_id},
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred.",
            "request_id": request_id,
        },
    )


app.include_router(deals_router, prefix="/api/v1/deals", tags=["deals"])
app.include_router(criteria_router, prefix="/api/v1/criteria", tags=["criteria"])


@app.get("/health")
async def health_check():
    return {"status": "ok"}
