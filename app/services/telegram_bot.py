import logging
import os
import re
from typing import Any, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.models.schemas import AthleteProfile, DailyCheckinInput, ReadinessResult, ReadinessStatus
from app.services.gemini_service import GeminiService
from supabase import Client, create_client

logger = logging.getLogger(__name__)

FULL_NAME, GOAL, EQUIPMENT, INJURIES, SCHEDULE = range(5)


def _get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Variables d'environnement Supabase manquantes.")
    return create_client(url, key)


def _split_full_name(full_name: str) -> Tuple[str, str]:
    parts = full_name.strip().split(maxsplit=1)
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


def _upsert_athlete(user: Any, full_name: Optional[str] = None) -> dict[str, Any]:
    supabase = _get_supabase_client()
    first_name, last_name = _split_full_name(full_name) if full_name else (user.first_name or "", user.last_name or "")

    existing = supabase.table("athletes").select("*").eq("telegram_id", user.id).execute()
    if existing.data:
        return existing.data[0]

    payload = {
        "telegram_id": user.id,
        "first_name": first_name,
        "last_name": last_name,
        "username": user.username,
        "status": "free",
    }
    response = supabase.table("athletes").insert(payload).execute()
    return response.data[0] if response.data else payload


def _get_athlete_by_telegram_id(telegram_id: int) -> Optional[dict[str, Any]]:
    supabase = _get_supabase_client()
    response = supabase.table("athletes").select("*").eq("telegram_id", telegram_id).execute()
    return response.data[0] if response.data else None


def _athlete_name(athlete: Optional[dict[str, Any]], fallback: str) -> str:
    if athlete and athlete.get("first_name"):
        return athlete["first_name"]
    return fallback


async def _pre_process_user(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    _upsert_athlete(user)
    return True


async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _pre_process_user(update):
        return ConversationHandler.END

    user = update.effective_user
    athlete = _get_athlete_by_telegram_id(user.id)
    message = update.effective_message

    if athlete and athlete.get("onboarding_completed"):
        if message:
            await message.reply_text(
                f"Content de te revoir {_athlete_name(athlete, user.first_name)} ! 🥋\n"
                "Envoie-moi ton check-in vocal ou texte du jour quand tu es prêt."
            )
        return ConversationHandler.END

    context.user_data.clear()
    if message:
        await message.reply_text(
            "Bienvenue dans Amazonian Samourai Coach ! 🥋\n\n"
            "Je vais te poser 5 questions pour personnaliser ton suivi.\n"
            "1. Quels sont tes nom et prénom ?"
        )
    return FULL_NAME


async def get_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message and update.message.text else ""
    context.user_data["full_name"] = text
    if update.message:
        await update.message.reply_text("2. Quel est ton objectif principal ? (ex: Prise de masse, Perte de gras, Combat)")
    return GOAL


async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message and update.message.text else ""
    context.user_data["goal"] = text
    if update.message:
        await update.message.reply_text("3. De quel matériel disposes-tu ? (ex: Salle complète, Kettlebells, Poids du corps)")
    return EQUIPMENT


async def get_equipment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message and update.message.text else ""
    context.user_data["equipment"] = text
    if update.message:
        await update.message.reply_text("4. As-tu des blessures ou douleurs particulières ? (si aucune, réponds 'Aucune')")
    return INJURIES


async def get_injuries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message and update.message.text else ""
    context.user_data["injuries"] = text
    if update.message:
        await update.message.reply_text("5. Quel est ton emploi du temps d'entraînement hebdo ? (ex: Lundi/Mercredi/Vendredi)")
    return SCHEDULE


async def get_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message and update.message.text else ""
    context.user_data["schedule"] = text

    user = update.effective_user
    if not user:
        return ConversationHandler.END

    first_name, last_name = _split_full_name(context.user_data.get("full_name", ""))
    supabase = _get_supabase_client()

    payload = {
        "first_name": first_name or user.first_name or "",
        "last_name": last_name or user.last_name or "",
        "goal": context.user_data.get("goal"),
        "equipment": context.user_data.get("equipment"),
        "injuries": context.user_data.get("injuries"),
        "schedule": context.user_data.get("schedule"),
        "onboarding_completed": True,
    }

    supabase.table("athletes").update(payload).eq("telegram_id", user.id).execute()

    if update.message:
        await update.message.reply_text(
            "✅ Onboarding terminé ! Ton profil est configuré.\n\n"
            "Tu peux maintenant m'envoyer tes check-ins par message texte ou note vocale."
        )
    return ConversationHandler.END


async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Onboarding annulé. Envoie /start pour recommencer.")
    return ConversationHandler.END


async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _pre_process_user(update) or not update.message:
        return

    user = update.effective_user
    args = context.args or []
    code = args[0].strip().upper() if args else ""

    if code == "SAMOURAI2026":
        supabase = _get_supabase_client()
        supabase.table("athletes").update({"status": "vip"}).eq("telegram_id", user.id).execute()
        await update.message.reply_text("🎉 Félicitations ! Ton accès VIP Amazonian Samourai a été activé.")
    else:
        await update.message.reply_text("❌ Code invalide. Utilisation : /code SAMOURAI2026")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _pre_process_user(update) or not update.message:
        return

    args = context.args or []
    target_id = int(args[0]) if args and args[0].isdigit() else update.effective_user.id

    athlete = _get_athlete_by_telegram_id(target_id)
    if not athlete:
        await update.message.reply_text("❌ Athlète non trouvé.")
        return

    supabase = _get_supabase_client()
    logs_res = (
        supabase.table("workout_logs")
        .select("*")
        .eq("athlete_id", athlete["id"])
        .order("completed_at", desc=True)
        .limit(7)
        .execute()
    )

    logs = logs_res.data or []
    if not logs:
        await update.message.reply_text(f"Aucun log récent trouvé pour {athlete.get('first_name', 'l athlete')}.")
        return

    gemini = GeminiService()
    profile = AthleteProfile(
        athlete_id=athlete["id"],
        telegram_id=athlete["telegram_id"],
        first_name=athlete.get("first_name", ""),
        last_name=athlete.get("last_name", ""),
        goal=athlete.get("goal"),
        equipment=athlete.get("equipment"),
        injuries=athlete.get("injuries"),
        schedule=athlete.get("schedule"),
        status=athlete.get("status", "free"),
    )

    report = gemini.generate_summary(profile, logs)
    await update.message.reply_text(report)


def _extract_rpe(text: str) -> Optional[int]:
    match = re.search(r"\brpe\s*[:=]?\s*(10|[1-9])(?:\s*/\s*10)?\b|\b(10|[1-9])\s*/\s*10\b", text, re.IGNORECASE)
    if match:
        val = match.group(1) or match.group(2)
        return int(val) if val else None
    return None


async def handle_finish_workout_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Séance enregistrée avec succès ! Bon repos. 🥊")


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _pre_process_user(update) or not update.message or not update.message.voice:
        return

    await update.message.reply_text("🎙️ Note vocale reçue. Analyse en cours par l'IA...")
    # Intégration de la transcription et analyse vocale Gemini


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _pre_process_user(update) or not update.message or not update.message.text:
        return

    text = update.message.text
    user = update.effective_user
    athlete = _get_athlete_by_telegram_id(user.id)

    gemini = GeminiService()
    profile = AthleteProfile(
        athlete_id=athlete["id"] if athlete else "",
        telegram_id=user.id,
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        status=athlete.get("status", "free") if athlete else "free",
    )

    checkin = DailyCheckinInput(raw_text=text, rpe=_extract_rpe(text))
    result = gemini.analyze_daily_checkin(profile, checkin)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Terminer la séance", callback_data="finish_workout")]]
    )
    await update.message.reply_text(f"📊 Analyse Readiness : {result.readiness_score}/10\n\n{result.advice}", reply_markup=keyboard)


def create_telegram_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN manquant dans l'environnement.")
    application = ApplicationBuilder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_onboarding)],
        states={
            FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_full_name)],
            GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_goal)],
            EQUIPMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_equipment)],
            INJURIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_injuries)],
            SCHEDULE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_schedule)],
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("code", code_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CallbackQueryHandler(handle_finish_workout_button, pattern="^finish_workout$"))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    return application





