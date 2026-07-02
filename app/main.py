import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.database import init_db
from app.core.settings import settings
from app.engine import auto_engine_loop
from app.routes import topup_routes
from app.routes.admin_routes import router as admin_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine_task = None

    try:
        await init_db()
    except Exception as exc:
        print(f"Database init warning: {exc}")

    try:
        engine_task = asyncio.create_task(auto_engine_loop())
        print("🚀 Engine Auto-Polling & Backup Database Berjalan (Async)!")
    except Exception as exc:
        print(f"Background engine warning: {exc}")

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


@app.get("/")
def home() -> FileResponse:
    return FileResponse("web/index.html")


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.parsed_allowed_origins if settings.parsed_allowed_origins != [""] else ["*"],
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
