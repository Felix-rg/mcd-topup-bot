from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/health")
async def admin_health():
    return {"status": "ok", "service": "lixafa-admin"}
