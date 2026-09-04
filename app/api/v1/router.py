from fastapi import APIRouter
from app.api.v1.endpoints import health, checkin, exercises

api_router = APIRouter()

api_router.include_router(health.router, tags=["Health"])
api_router.include_router(checkin.router, prefix="/athletes", tags=["Check-in & Readiness"])
api_router.include_router(exercises.router, tags=["Exercises"])

