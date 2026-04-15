from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deals import router as deals_router
from app.api.criteria import router as criteria_router
from app.auth import auth_router
from app.input_validation import InputValidationMiddleware
from app.observability import ObservabilityMiddleware, health_router
from app.observability.logger import request_id_var
from app.rate_limiter import RateLimitMiddleware, init_rate_limiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.secrets_config import bootstrap as sc_bootstrap, shutdown as sc_shutdown
    from app.secrets_config import get_config, get_secrets, Environment
    await sc_bootstrap()

    config = get_config()
    if config.env in (Environment.DEV, Environment.TEST):
        from app.auth.tokens import generate_ephemeral_keys
        generate_ephemeral_keys()
    else:
        from app.auth.tokens import init_signing_keys
        secrets = get_secrets()
        current = secrets.get("auth_signing_key_current")
        init_signing_keys(
            current_key_bytes=current.reveal(),
            current_kid=f"key_{current.version}",
            previous_key_bytes=(
                prev.reveal() if (prev := secrets.get_optional("auth_signing_key_previous")) else None
            ),
            previous_kid=f"key_{prev.version}" if prev else None,
        )

    # Register background job handlers
    from app.background_jobs import init_handlers
    init_handlers()

    # Rate limiter — uses in-memory store for portfolio phase
    rl_config = config.rate_limit
    init_rate_limiter(trusted_proxies=list(rl_config.trusted_proxy_cidrs))

    yield
    await sc_shutdown()


app = FastAPI(
    title="Deal Screening Agent",
    description="AI-powered deal screening for investment firms",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    InputValidationMiddleware,
    auth_required_prefixes=("/api/v1/deals", "/api/v1/criteria"),
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", request_id_var.get())
    return JSONResponse(
        status_code=500,
        headers={"X-Request-ID": request_id},
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred.",
            "request_id": request_id,
        },
    )


app.include_router(health_router)
app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(deals_router, prefix="/api/v1/deals", tags=["deals"])
app.include_router(criteria_router, prefix="/api/v1/criteria", tags=["criteria"])
