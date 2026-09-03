import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.supabase import get_supabase_client
from app.api.v1.router import api_router

# Configuration des logs
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("coaching_ia_v2")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Démarrage
    logger.info("🚀 Démarrage du serveur Coaching IA V2...")
    client = get_supabase_client()
    if client:
        logger.info("✅ Connexion Supabase active.")
    else:
        logger.warning("⚠️ Mode local/fallback actif (Supabase non configuré).")
    yield
    # Arrêt
    logger.info("🛑 Arrêt du serveur Coaching IA V2...")


app = FastAPI(
    title="Coaching IA V2 - API",
    description="Backend de Coaching de Performance & Sport de Combat (Readiness Engine, Multi-Agents, Supabase)",
    version="2.0.0",
    lifespan=lifespan
)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclusion des routes API v1
app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Bienvenue sur l'API Coaching IA Base V2",
        "docs": "/docs",
        "status": "online"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
