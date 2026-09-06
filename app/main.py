"""
Serveur Web Flask & Webhook Telegram 24/7 pour Coaching IA V2 ("Amazonian Samourai").
Hébergé sur Render.
"""
import os
import sys
import asyncio
import logging
import threading
from flask import Flask, request, jsonify
from telegram import Update

# Configuration de l'encodage sur Windows si nécessaire
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.core.config import settings
from app.services.telegram_bot import (
    create_telegram_application,
    get_telegram_bot_instance
)

# Configuration des logs
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("amazonian_samourai")

app = Flask(__name__)

# Gestionnaire d'événements asyncio persistant pour python-telegram-bot
_asyncio_loop = None
_telegram_app = None
_loop_lock = threading.Lock()


def get_or_create_event_loop():
    global _asyncio_loop
    with _loop_lock:
        if _asyncio_loop is None or _asyncio_loop.is_closed():
            _asyncio_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_asyncio_loop)
        return _asyncio_loop


def init_bot_and_webhook():
    """
    Initialise l'application Telegram et configure le Webhook Render 24/7.
    """
    global _telegram_app
    if _telegram_app is not None:
        return _telegram_app

    logger.info("🚀 Initialisation du Bot Telegram & Webhook...")
    telegram_app = create_telegram_application()
    if not telegram_app:
        logger.error("❌ Impossible d'initialiser Telegram : TELEGRAM_BOT_TOKEN manquant.")
        return None

    loop = get_or_create_event_loop()
    try:
        loop.run_until_complete(telegram_app.initialize())
        _telegram_app = telegram_app
        logger.info("✅ Application Telegram initialisée avec succès.")

        # Configuration du Webhook sur Render
        render_url = (
            os.getenv("RENDER_EXTERNAL_URL")
            or getattr(settings, "RENDER_EXTERNAL_URL", None)
            or getattr(settings, "WEBHOOK_URL", None)
        )
        webhook_secret = (
            os.getenv("WEBHOOK_SECRET")
            or getattr(settings, "WEBHOOK_SECRET", None)
            or getattr(settings, "TELEGRAM_WEBHOOK_SECRET", None)
        )

        if render_url:
            webhook_url = f"{render_url.rstrip('/')}/telegram-webhook"
            logger.info(f"🌐 Enregistrement du Webhook Telegram sur : {webhook_url}")
            loop.run_until_complete(
                telegram_app.bot.set_webhook(
                    url=webhook_url,
                    secret_token=webhook_secret or None
                )
            )
            logger.info("✅ Webhook Telegram configuré 24/7 sur Render !")
        else:
            logger.info("ℹ️ RENDER_EXTERNAL_URL non configuré en local. Mode webhook prêt dès déploiement Render.")

    except Exception as e:
        logger.error(f"❌ Erreur lors de l'initialisation du bot/webhook : {e}", exc_info=True)

    return _telegram_app


@app.route("/", methods=["GET"])
def health_check():
    """
    Endpoint de santé (Ping Render / UptimeRobot).
    Permet de maintenir le serveur éveillé ou de tester son statut.
    """
    bot_ready = _telegram_app is not None
    return jsonify({
        "status": "online",
        "service": "Amazonian Samourai - Coaching IA V2",
        "bot_initialized": bot_ready,
        "mode": "Webhook Flask 24/7"
    }), 200


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    """
    Point d'entrée Webhook appelé par Telegram à chaque message ou interaction.
    """
    # 1. Vérification du secret token de sécurité si configuré
    expected_secret = (
        os.getenv("WEBHOOK_SECRET")
        or getattr(settings, "WEBHOOK_SECRET", None)
        or getattr(settings, "TELEGRAM_WEBHOOK_SECRET", None)
    )
    if expected_secret:
        incoming_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if incoming_secret != expected_secret:
            logger.warning("⛔ Requête Webhook rejetée : token secret invalide.")
            return jsonify({"error": "Unauthorized"}), 403

    # 2. Vérification de l'initialisation du bot
    global _telegram_app
    if _telegram_app is None:
        init_bot_and_webhook()
        if _telegram_app is None:
            logger.error("❌ Bot Telegram non initialisé lors de la réception du webhook.")
            return jsonify({"error": "Bot not initialized"}), 500

    # 3. Récupération des données Telegram
    update_data = request.get_json(force=True, silent=True)
    if not update_data:
        logger.warning("⚠️ Payload webhook vide ou invalide.")
        return jsonify({"status": "no data"}), 400

    # 4. Traitement asynchrone de l'Update dans la boucle asyncio
    try:
        update = Update.de_json(update_data, _telegram_app.bot)
        loop = get_or_create_event_loop()
        loop.run_until_complete(_telegram_app.process_update(update))
    except Exception as e:
        logger.error(f"❌ Erreur traitement webhook Telegram : {e}", exc_info=True)
        # On renvoie 200 pour éviter que Telegram ne boucle indéfiniment sur un message corrompu
        return jsonify({"status": "error", "message": str(e)}), 200

    return jsonify({"status": "ok"}), 200


@app.route("/set-webhook", methods=["GET", "POST"])
def manual_set_webhook():
    """
    Route utilitaire pour forcer manuellement la reconfiguration du webhook si nécessaire.
    """
    app_bot = init_bot_and_webhook()
    if not app_bot:
        return jsonify({"error": "Bot unavailable"}), 500

    render_url = (
        request.args.get("url")
        or os.getenv("RENDER_EXTERNAL_URL")
        or getattr(settings, "RENDER_EXTERNAL_URL", None)
        or getattr(settings, "WEBHOOK_URL", None)
    )
    if not render_url:
        return jsonify({"error": "No URL specified. Pass ?url=https://your-service.onrender.com"}), 400

    webhook_secret = (
        os.getenv("WEBHOOK_SECRET")
        or getattr(settings, "WEBHOOK_SECRET", None)
        or getattr(settings, "TELEGRAM_WEBHOOK_SECRET", None)
    )
    webhook_url = f"{render_url.rstrip('/')}/telegram-webhook"
    loop = get_or_create_event_loop()
    try:
        loop.run_until_complete(
            app_bot.bot.set_webhook(url=webhook_url, secret_token=webhook_secret or None)
        )
        return jsonify({"status": "ok", "webhook_url": webhook_url}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def main():
    port = int(os.getenv("PORT", getattr(settings, "PORT", 8000)))
    host = os.getenv("HOST", getattr(settings, "HOST", "0.0.0.0"))
    logger.info(f"🥋 Démarrage Serveur Flask Coaching IA V2 sur {host}:{port}...")
    init_bot_and_webhook()
    app.run(host=host, port=port)


# Initialisation au chargement du module pour serveurs WSGI (gunicorn / render)
init_bot_and_webhook()

if __name__ == "__main__":
    main()
