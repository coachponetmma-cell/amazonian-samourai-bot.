"""
Point d'entrée pour lancer le Bot Telegram en mode Polling
"""
import sys
import asyncio
import logging

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.core.config import settings
from app.services.telegram_bot import create_telegram_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("telegram_bot_runner")


def main():
    logger.info("🤖 Démarrage du Bot Telegram Coaching IA V2...")
    app = create_telegram_application()
    if app is None:
        logger.error(
            "❌ Impossible de démarrer le bot : TELEGRAM_BOT_TOKEN manquant dans le fichier .env.\n"
            "👉 Ouvre le fichier .env et renseigne : TELEGRAM_BOT_TOKEN=ton_token_obtenu_via_BotFather"
        )
        return

    logger.info("✅ Bot Telegram prêt et à l'écoute des messages (Mode Polling) !")
    app.run_polling()


if __name__ == "__main__":
    main()
