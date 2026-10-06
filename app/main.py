import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Deque, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.database import initialize_database_runtime
from app.core.settings import settings
from app.core.write_quiescence import WriteQuiescenceActive, write_quiescence
from app.engine import auto_engine_loop
from app.routes import topup_routes
from app.routes.admin_routes import router as admin_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine_task = None

    try:
        await initialize_database_runtime()
    except Exception as exc:
        logger.critical("Startup database gagal: %s", exc)
        raise

    if write_quiescence.enabled:
        # Settings permits this only for staging + PostgreSQL.  Do not create a
        # writer task at all: the health acknowledgement is emitted directly by
        # the middleware below without entering a route or dependency.
        write_quiescence.mark_engine_stopped()
        logger.warning("Staging write quiescence aktif; embedded engine tidak dijalankan.")
    else:
        try:
            engine_task = asyncio.create_task(auto_engine_loop())
            logger.info("Engine auto-polling berjalan (async).")
        except Exception as exc:
            logger.exception("Background engine gagal dimulai: %s", exc)
            raise

    yield

    if engine_task and not engine_task.done():
        engine_task.cancel()
        try:
            await engine_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Platform top-up digital modern untuk pulsa dan game",
    lifespan=lifespan,
)

RateRule = Tuple[str, str, int, int]
RATE_LIMIT_RULES: tuple[RateRule, ...] = (
    ("POST", "/topup", 30, 60),
    ("POST", "/api/postpaid/inquiry", 20, 60),
    ("POST", "/api/pln/inquiry", 30, 60),
    ("POST", "/check-nickname", 60, 60),
    ("POST", "/api/customer/register", 5, 300),
    ("POST", "/api/customer/login", 12, 60),
    ("POST", "/api/customer/wallet/open-payment", 10, 60),
    ("POST", "/api/customer/wallet/sync-open-payment", 20, 60),
    ("POST", "/api/support/tickets", 10, 60),
    ("POST", "/admin/login", 10, 60),
)
ORDER_STATUS_RATE_RULE: RateRule = ("GET", "/topup/", 30, 60)
_rate_limit_buckets: dict[str, Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # A proxy must only be trusted after it has been explicitly configured.
    # Reading this client-controlled header here would let callers bypass the
    # process-local rate limit by rotating X-Forwarded-For values.
    return request.client.host if request.client else "unknown"


def _rate_rule_for(request: Request) -> Optional[RateRule]:
    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()
    if method == ORDER_STATUS_RATE_RULE[0] and path.startswith(ORDER_STATUS_RATE_RULE[1]):
        return ORDER_STATUS_RATE_RULE
    for rule in RATE_LIMIT_RULES:
        rule_method, rule_path, _, _ = rule
        if method == rule_method and path == rule_path:
            return rule
    return None


def _write_quiescence_response() -> JSONResponse:
    """A deterministic, non-audited maintenance response for every blocked route."""

    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "code": "WRITE_QUIESCENCE_ACTIVE",
            "message": "Staging sementara dalam pemeliharaan write-quiescence. Coba lagi nanti.",
        },
        headers={"Retry-After": "60", "Cache-Control": "no-store"},
    )


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # This branch is deliberately before rate limiting, routing, authentication,
    # dependency injection, request parsing, audit helpers, and static mounts.
    # In particular, it catches nominal GET endpoints that lazily create a
    # wallet, and malformed callbacks that normally write webhook audit data.
    if write_quiescence.enabled:
        if request.method.upper() == "GET" and request.url.path == "/admin/health":
            return JSONResponse(
                status_code=200,
                content=write_quiescence.maintenance_payload(),
                headers={"Cache-Control": "no-store"},
            )
        return _write_quiescence_response()

    try:
        # Conservatively treat every normal HTTP request as a possible writer.
        # Nested service guards are re-entrant for this request task, while a
        # concurrent transition cannot admit another top-level request.
        async with write_quiescence.writer_section("http_request"):
            rule = _rate_rule_for(request)
            if not rule:
                return await call_next(request)

            method, path, max_requests, window_seconds = rule
            now = time.monotonic()
            bucket_key = f"{method}:{path}:{_client_ip(request)}"
            bucket = _rate_limit_buckets[bucket_key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()

            if len(bucket) >= max_requests:
                retry_after = max(1, int(window_seconds - (now - bucket[0]))) if bucket else window_seconds
                return JSONResponse(
                    status_code=429,
                    content={"success": False, "message": "Terlalu banyak request, coba lagi sebentar."},
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)
            return await call_next(request)
    except WriteQuiescenceActive:
        # The gate may close in the narrow interval between the initial check
        # and lease admission.  The route still has not been entered.
        return _write_quiescence_response()


@app.get("/")
def home() -> FileResponse:
    return FileResponse("web/index.html")


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.parsed_allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(topup_routes.router)
app.include_router(admin_router)


@app.get("/admin")
def admin_login_page() -> FileResponse:
    return FileResponse("web/admin.html")


@app.get("/admin-dashboard")
def admin_dashboard_page() -> FileResponse:
    return FileResponse("web/admin-dashboard.html")


app.mount("/web", StaticFiles(directory="web"), name="web")
app.mount("/receipts", StaticFiles(directory="receipts"), name="receipts")
