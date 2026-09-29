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


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
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
