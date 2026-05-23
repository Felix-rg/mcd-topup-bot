from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import threading

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

threading.Thread(target=auto_engine_loop, daemon=True).start()

app.mount("/web", StaticFiles(directory="web"), name="web")
app.mount("/receipts", StaticFiles(directory="receipts"), name="receipts")