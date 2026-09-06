"""
Point d'entrée pour lancer le Bot Telegram en mode Webhook Flask 24/7 (Render).
"""
import sys
import logging

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.main import app, main

if __name__ == "__main__":
    main()