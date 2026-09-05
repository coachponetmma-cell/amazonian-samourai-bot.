import logging
import re
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from app.core.config import settings
from app.services.gemini import (
    analyze_checkin_with_gemini,
    generate_daily_workout,
    generate_weekly_coach_summary,
    clean_telegram_html
)
from app.services.supabase_service import (
    supabase,
    get_available_exercises,
    get_athlete_by_telegram_id,
    get_athlete_by_id,
    get_all_athletes,
    log_workout_generation,
    log_workout_completion,
    log_checkin,
    get_last_7_days_workout_logs,
    get_last_7_days_checkins
)

logger = logging.getLogger(__name__)


def is_head_coach(user_id: int, username: str = None) -> bool:
    """
    Vérifie si l'utilisateur est le Head Coach (Jason Ponet).
    """
    if settings.COACH_TELEGRAM_ID and user_id == int(settings.COACH_TELEGRAM_ID):
        return True
    if settings.ADMIN_TELEGRAM_IDS:
        admin_ids = [int(i.strip()) for i in str(settings.ADMIN_TELEGRAM_IDS).split(",") if i.strip().isdigit()]
        if user_id in admin_ids:
            return True
    if username and username.lower() in ["coachponet", "jasonponet"]:
        return True
    return False


async def send_safe_html_message(message, text: str):
    """
    Envoie un message formaté en HTML sur Telegram de manière sécurisée.
    En cas d'erreur de parsing Telegram, retente en texte nettoyé.
    """
    cleaned = clean_telegram_html(text)
    try:
        await message.reply_text(cleaned, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"Échec envoi Telegram HTML ({e}), fallback en texte brut")
        # Suppression des balises HTML en cas de rejet par Telegram
        plain_text = re.sub(r"<[^>]+>", "", cleaned)
        await message.reply_text(plain_text, disable_web_page_preview=True)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Message d'accueil et présentation du bot.
    """
    user_id = update.effective_user.id
    athlete = get_athlete_by_telegram_id(user_id)
    athlete_name = athlete.get("first_name", "Combattant")

    welcome_text = (
        f"🥋 <b>SAMOURAI PERFORMANCE SYSTEM</b>\n\n"
        f"Bienvenue <b>{athlete_name}</b> sur ton bot de coaching haute performance.\n\n"
        f"📌 <b>Comment l'utiliser :</b>\n"
        f"1. <b>Check-in quotidien :</b> Envoie ton état de forme (sommeil, énergie, courbatures) et ton lieu/matériel du jour (ex: <i>'Dans ma chambre d\\'hôtel sans matériel, énergie 7/10'</i>).\n"
        f"2. <b>Séance sur-mesure :</b> Reçois immédiatement ta séance structurée avec liens vidéos YouTube.\n"
        f"3. <b>Débriefing :</b> Après la séance, envoie ton RPE et ton ressenti (ex: <i>'Séance terminée, RPE 8, super sensations'</i>).\n\n"
        f"🔥 <i>Libertad & Performance.</i>"
    )
    await send_safe_html_message(update.message, welcome_text)


async def handle_hebdo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Commande /hebdo réservée au Head Coach pour préparer son appel téléphonique hebdomadaire.
    Analyse les 7 derniers jours de workout_logs et checkins dans Supabase via Gemini.
    """
    user_id = update.effective_user.id
    username = update.effective_user.username or ""

    # 1. Contrôle d'accès Head Coach
    if not is_head_coach(user_id, username):
        await send_safe_html_message(
            update.message,
            "⛔ <b>Accès restreint</b> : La commande <code>/hebdo</code> est réservée au Head Coach Jason Ponet."
        )
        return

    # Notification de traitement
    status_msg = await update.message.reply_text(
        "⏳ <i>Analyse des 7 derniers jours et synthèse de l'athlète en cours...</i>",
        parse_mode="HTML"
    )

    try:
        # 2. Détermination de l'athlète cible
        all_athletes = get_all_athletes()
        target_athlete = None

        # Si le coach a passé un argument (ex: /hebdo Atichat)
        if context.args:
            search_query = " ".join(context.args).lower().strip()
            for a in all_athletes:
                fn = (a.get("first_name") or "").lower()
                ln = (a.get("last_name") or "").lower()
                un = (a.get("username") or "").lower()
                if search_query in fn or search_query in ln or search_query in un:
                    target_athlete = get_athlete_by_id(a.get("id"))
                    break

        # Sinon, déterminer l'athlète ayant eu de l'activité récente dans workout_logs ou checkins
        if not target_athlete:
            recent_logs = get_last_7_days_workout_logs(days=7)
            if recent_logs:
                active_ath_id = recent_logs[0].get("athlete_id")
                target_athlete = get_athlete_by_id(active_ath_id)

        # Si toujours non trouvé, fallback sur le premier athlète enregistré
        if not target_athlete and all_athletes:
            target_athlete = get_athlete_by_id(all_athletes[0].get("id"))

        if not target_athlete:
            target_athlete = {"first_name": "Athlète", "id": None, "goal": "MMA / Combat"}

        target_id = target_athlete.get("id")

        # 3. Récupération des données des 7 derniers jours
        logs = get_last_7_days_workout_logs(athlete_id=target_id, days=7)
        checkins = get_last_7_days_checkins(athlete_id=target_id, days=7)

        # 4. Génération de la synthèse par Gemini
        summary_html = generate_weekly_coach_summary(logs=logs, athlete_info=target_athlete, checkins=checkins)

        # 5. Envoi du rapport
        await status_msg.delete()
        await send_safe_html_message(update.message, summary_html)

    except Exception as e:
        logger.error(f"Erreur commande /hebdo: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Erreur lors de la génération du bilan hebdo : {e}")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestion des messages texte de l'athlète (Check-in quotidien ou débriefing post-séance).
    """
    user_text = update.message.text
    if not user_text:
        return

    user_id = update.effective_user.id
    athlete = get_athlete_by_telegram_id(user_id)
    athlete_id = athlete.get("id")
    athlete_name = athlete.get("first_name", "Combattant")

    lower_text = user_text.lower()

    # --- DÉTECTION DÉBRIEFING DE SÉANCE ---
    # Si le message contient des signaux de fin de séance (RPE, sensations, séance terminée)
    is_debrief = any(w in lower_text for w in ["séance terminée", "seance terminee", "séance faite", "seance faite", "débrief", "debrief", "fini la séance", "rpe"]) and not any(w in lower_text for w in ["programme", "quelle séance", "donne-moi"])

    if is_debrief:
        # Extraction du RPE si présent
        rpe_match = re.search(r"rpe\s*[:=]?\s*(\d{1,2})", lower_text)
        rpe_val = int(rpe_match.group(1)) if rpe_match else 7

        if athlete_id:
            log_workout_completion(athlete_id=athlete_id, rpe_score=rpe_val, feedback_text=user_text)

        debrief_reply = (
            f"<b>🥋 DÉBRIEFING ENREGISTRÉ !</b>\n\n"
            f"Bien reçu <b>{athlete_name}</b>. Séance validée avec un RPE ressenti de <b>{rpe_val}/10</b>.\n\n"
            f"💡 <i>Ton Head Coach verra ces données lors du bilan hebdo. Hydrate-toi bien et focus sur la récupération !</i>\n\n"
            f"🔥 Libertad & Performance."
        )
        await send_safe_html_message(update.message, debrief_reply)
        return

    # --- TRAITEMENT CHECK-IN QUOTIDIEN & GÉNÉRATION DE SÉANCE ---
    # Notification d'analyse
    typing_msg = await update.message.reply_text("⏳ <i>Analyse de ton check-in et préparation de ta séance...</i>", parse_mode="HTML")

    try:
        # 1. Analyse du check-in par Gemini
        analysis = analyze_checkin_with_gemini(user_text)

        # 2. Enregistrement du check-in dans Supabase
        if athlete_id:
            log_checkin(athlete_id=athlete_id, analysis=analysis, raw_text=user_text)

        # 3. Filtrage 100 % étanche des exercices selon le matériel et l'environnement réel
        equipment = analysis.equipment_available or athlete.get("default_equipment", "Poids du corps")
        exercises = get_available_exercises(equipment)

        # 4. Génération de la séance au format HTML Telegram VIP
        workout_plan = generate_daily_workout(analysis, exercises, athlete)

        # 5. Enregistrement de la séance prescrite dans workout_logs
        if athlete_id:
            prescribed_rpe = getattr(analysis, "rpe", None) or 7
            log_workout_generation(
                athlete_id=athlete_id,
                prescribed_rpe=prescribed_rpe,
                summary_text=f"Séance {equipment} - Énergie {analysis.energy_score}/10"
            )

        # 6. Envoi de la réponse structurée finale
        response_message = (
            f"<b>{analysis.feedback_coach}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 <b>TA SÉANCE DU JOUR</b>\n\n"
            f"{workout_plan}"
        )

        await typing_msg.delete()
        await send_safe_html_message(update.message, response_message)

    except Exception as e:
        logger.error(f"Erreur traitement checkin : {e}", exc_info=True)
        await typing_msg.edit_text(f"❌ Erreur lors de la génération de la séance : {e}")


def create_telegram_application():
    """
    Initialise l'application Telegram avec ses handlers.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN manquant.")
        return None

    application = ApplicationBuilder().token(token).build()

    # Commandes
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("hebdo", handle_hebdo_command))

    # Messages texte (Check-ins et Débriefings)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_message))

    return application
