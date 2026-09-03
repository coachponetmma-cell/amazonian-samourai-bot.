"""Contrôle pré-vol du Module 1 : à exécuter avant de démarrer le bot."""

from dotenv import load_dotenv

load_dotenv()

import os
import sys

from supabase import create_client


REQUIRED_ENV = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "TELEGRAM_BOT_TOKEN",
    "GEMINI_API_KEY",
    "COACH_TELEGRAM_ID",
)


def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        print("ECHEC — variables .env manquantes : " + ", ".join(missing))
        return 1

    try:
        client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
        client.table("athletes").select("*").limit(1).execute()
        for table in ("athlete_profiles", "checkins", "alerts", "debriefs"):
            client.table(table).select("*").limit(1).execute()
    except Exception as error:
        print(f"ECHEC — Supabase n'est pas prêt pour le Module 1 : {error}")
        return 1

    print("OK — configuration, accès Supabase et tables Module 1 + Module 2 vérifiés.")
    print("Tu peux démarrer le bot et tester /start, un check-in, puis /debrief.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
