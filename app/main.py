from __future__ import annotations

import time
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.utils import get_openapi
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from .api_v1 import router as api_v1_router
from .limiting import limiter
from .security import require_api_key
from .settings import settings

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Production-ready API for YouTube channel/video insights plus URL metadata enrichment. "
        "RapidAPI-compatible authentication and per-subscriber rate limiting included."
    ),
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # RapidAPI-friendly security schemes
    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes["ApiKeyAuth"] = {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    security_schemes["RapidApiKey"] = {"type": "apiKey", "in": "header", "name": "X-RapidAPI-Key"}
    security_schemes["RapidApiProxySecret"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-RapidAPI-Proxy-Secret",
        "description": "Optional. Required if REQUIRE_RAPIDAPI_PROXY_SECRET=true.",
    }

    # Either direct API key OR RapidAPI key (optionally + proxy secret)
    schema["security"] = [
        {"ApiKeyAuth": []},
        {"RapidApiKey": []},
        {"RapidApiKey": [], "RapidApiProxySecret": []},
    ]

    schema.setdefault("tags", [])
    schema["tags"] = [
        {"name": "System", "description": "Health and operational endpoints."},
        {"name": "Metadata", "description": "Fetch and parse web page metadata."},
        {"name": "YouTube", "description": "YouTube Data API v3 powered endpoints."},
    ]

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

if settings.enable_gzip:
    app.add_middleware(GZipMiddleware, minimum_size=1000)


def _split_csv(value: str) -> list[str]:
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


allow_origins = _split_csv(settings.cors_allow_origins)
allow_methods = _split_csv(settings.cors_allow_methods)
allow_headers = _split_csv(settings.cors_allow_headers)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
)


@app.middleware("http")
async def add_request_metadata(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Process-Time-Ms"] = str(duration_ms)
    return response


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request, exc):  # noqa: ANN001
    return JSONResponse(
        status_code=429,
        content={
            "error": {"code": "rate_limited", "message": "Rate limit exceeded"},
            "request_id": getattr(getattr(request, "state", None), "request_id", None),
        },
    )


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # If detail is already a structured payload, keep it.
    detail = exc.detail
    if isinstance(detail, dict) and ("error" in detail or "detail" in detail):
        payload: dict[str, object] = detail
    else:
        payload = {
            "error": {"code": "http_error", "message": str(detail)},
        }

    if "request_id" not in payload:
        rid = getattr(getattr(request, "state", None), "request_id", None)
        payload["request_id"] = str(rid) if rid else None
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.get("/health")
@limiter.limit("120/minute")
def health(request: Request):
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "health": "/health",
        "api": "/v1/meta",
    }


app.include_router(
    api_v1_router,
    prefix="/v1",
    tags=["v1"],
    dependencies=[Depends(require_api_key)],
)

if settings.enable_metrics:
    Instrumentator().instrument(app).expose(app, include_in_schema=False, endpoint="/metrics")
