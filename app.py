import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from database import init_db


from routes import topup_routes
from routes import admin_routes
from engine import auto_engine_loop
from fastapi.responses import FileResponse

app = FastAPI(title="Mc'D TopUp API")

@app.get("/")
def home():
    return FileResponse("web/index.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(topup_routes.router)
app.include_router(admin_routes.router)


@app.on_event("startup")
async def startup_event():
    await init_db()
    # Menjalankan background task native di event loop FastAPI
    asyncio.create_task(auto_engine_loop())
    print("🚀 Engine Auto-Polling & Backup Database Berjalan (Async)!")

app.mount("/web", StaticFiles(directory="web"), name="web")
app.mount("/receipts", StaticFiles(directory="receipts"), name="receipts")