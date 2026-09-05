import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from telegram import Update
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.core.supabase import get_supabase_client
from app.api.v1.router import api_router
from app.services.telegram_bot import (
    create_telegram_application,
    get_telegram_bot_instance,
    run_automatic_hebdo_summary
)

# Configuration des logs
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("coaching_ia_v2")

scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Démarrage
    logger.info("🚀 Démarrage du serveur Coaching IA V2...")
    client = get_supabase_client()
    if client:
        logger.info("✅ Connexion Supabase active.")
    else:
        logger.warning("⚠️ Mode local/fallback actif (Supabase non configuré).")
    
    # Initialisation Telegram Bot & Webhook / Scheduler
    telegram_app = create_telegram_application()
    if telegram_app:
        await telegram_app.initialize()
        app.state.telegram_app = telegram_app
        
        # Configuration webhook si Render ou WEBHOOK_URL présent
        webhook_url = getattr(settings, "WEBHOOK_URL", None)
        if webhook_url:
            full_webhook = f"{webhook_url.rstrip('/')}/telegram-webhook"
            logger.info(f"Configuration du Webhook Telegram : {full_webhook}")
            await telegram_app.bot.set_webhook(url=full_webhook)
        else:
            logger.info("Aucun WEBHOOK_URL détecté, mode polling ou webhook manuel.")

    # Démarrage du scheduler pour le bilan hebdo (Dimanche à 08:00 Asia/Bangkok)
    try:
        scheduler.add_job(
            run_automatic_hebdo_summary,
            CronTrigger(day_of_week="sun", hour=8, minute=0, timezone="Asia/Bangkok"),
            args=[get_telegram_bot_instance()],
            id="automatic_hebdo_job",
            replace_existing=True
        )
        scheduler.start()
        logger.info("📅 Scheduler hebdo activé (Dimanche 08:00 Bangkok).")
    except Exception as e:
        logger.error(f- "Erreur initialisation scheduler: {e}")

    yield

    # Arrêt
    logger.info("🛑 Arrêt du serveur Coaching IA V2...")
    if scheduler.running:
        scheduler.shutdown()
    if hasattr(app.state, "telegram_app"):
        await app.state.telegram_app.shutdown()


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


@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    """
    Endpoint recevant les mises à jour Telegram en mode Webhook 24/7 (Render).
    """
    telegram_app = getattr(app.state, "telegram_app", None)
    if not telegram_app:
        return {"status": "error", "message": "Telegram app not initialized"}
    
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"status": "ok"}


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
