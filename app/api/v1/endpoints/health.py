from fastapi import APIRouter
from app.core.config import settings
from app.core.supabase import get_supabase_client

router = APIRouter()


@router.get("/health", summary="Health check de l'application")
async def health_check():
    supabase_client = get_supabase_client()
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "supabase_connected": supabase_client is not None,
        "version": "2.0.0"
    }
