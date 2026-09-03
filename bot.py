"""
Point d'entrée pour lancer le Bot Telegram en mode Polling avec serveur Web Flask
"""
import sys
import asyncio
import logging
import os
import threading
from flask import Flask

# --- 1. SERVEUR WEB FLASK (Binding port pour Render Web Service) ---
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot status: Alive and Coaching!"

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app_web.run(host='0.0.0.0', port=port)

# Lancement du serveur HTTP en arrière-plan
threading.Thread(target=run_web, daemon=True).start()

# --- 2. CONFIGURATION ET ENCODAGE SYSTEME ---
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


# --- 3. DÉMARRAGE DU BOT TELEGRAM ---
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