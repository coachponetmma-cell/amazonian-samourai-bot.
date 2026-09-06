import logging
import re
from typing import Optional, List, Dict, Any
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Bot
)
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from app.core.config import settings
from app.services.gemini import (
    analyze_checkin_with_gemini,
    generate_daily_workout,
    generate_weekly_coach_summary,
    clean_telegram_html,
    transcribe_audio_with_gemini,
    parse_debrief_with_gemini
)
from app.models.schemas import DailyCheckinInput, ReadinessResult, ReadinessStatus
from app.services.supabase_service import (
    supabase,
    get_available_exercises,
    get_athlete_profile,
    get_athlete_by_telegram_id,
    get_athlete_by_id,
    get_all_athletes,
    athlete_profile_exists,
    save_new_athlete_profile,
    log_workout_generation,
    log_workout_completion,
    log_checkin,
    get_last_7_days_workout_logs,
    get_last_7_days_checkins
)

logger = logging.getLogger(__name__)

# États pour le tunnel d'onboarding ConversationHandler
ASK_NAME, ASK_GOAL, ASK_EQUIPMENT, ASK_INJURIES = range(4)

_global_telegram_application = None


def _split_full_name(value: str):
    """Sépare un nom saisi en prénom/nom selon le contrat historique du bot."""
    parts = (value or "").strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _athlete_name(profile: dict, fallback: str = "Combattant") -> str:
    """Retourne le nom quelle que soit la forme de profil renvoyée par Supabase."""
    if profile.get("full_name"):
        return str(profile["full_name"])
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    return " ".join(part for part in (first, last) if part) or fallback


def _is_debrief(text: str) -> bool:
    """Détecte un retour de séance sans confondre une demande de programme."""
    lowered = (text or "").lower()
    return any(token in lowered for token in (
        "séance validée", "séance terminée", "séance faite", "débrief", "debrief", "rpe"
    )) and not any(token in lowered for token in ("programme", "quelle séance", "donne-moi", "prépare"))


def _extract_rpe(text: str):
    match = re.search(r"\brpe\s*[:=]?\s*(10|[1-9])(?:\s*/\s*10)?\b", text or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def calculate_module2_readiness(checkin: DailyCheckinInput):
    """Calcule la readiness historique sur une échelle de 1 à 5."""
    score = round(
        0.25 * checkin.sleep_score
        + 0.25 * checkin.energy_score
        + 0.20 * (6 - checkin.fatigue_score)
        + 0.15 * (6 - checkin.stress_score)
        + 0.15 * (6 - checkin.soreness_score),
        1,
    )
    if score >= 4.0:
        label, status, cap, volume = "FORME_OPTIMALE", ReadinessStatus.GREEN, None, 1.0
        recommendation = "Séance complète selon le plan."
    elif score >= 2.5:
        label, status, cap, volume = "CHARGE_MODEREE", ReadinessStatus.ORANGE, 7, 0.8
        recommendation = "Réduire l'intensité et le volume, sans forcer."
    else:
        label, status, cap, volume = "RECUPERATION_ACTIVE", ReadinessStatus.RED, 5, 0.5
        recommendation = "Priorité à la récupération active et à la mobilité."
    result = ReadinessResult(
        score=score,
        status=status,
        details={"sleep": checkin.sleep_score, "energy": checkin.energy_score,
                 "fatigue": checkin.fatigue_score, "stress": checkin.stress_score,
                 "soreness": checkin.soreness_score},
        intensity_cap_rpe=cap,
        volume_multiplier=volume,
        recommendation=recommendation,
    )
    return score, label, result


def get_telegram_bot_instance() -> Optional[Bot]:
    global _global_telegram_application
    if _global_telegram_application and _global_telegram_application.bot:
        return _global_telegram_application.bot
    if settings.TELEGRAM_BOT_TOKEN:
        req = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
        return Bot(token=settings.TELEGRAM_BOT_TOKEN, request=req)
    return None


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


async def send_safe_html_message(message_or_bot, text: str, reply_markup: InlineKeyboardMarkup = None, chat_id: int = None):
    """
    Envoie un message formaté en HTML sur Telegram de manière sécurisée.
    Accepte soit un objet message Telegram, soit une instance Bot avec chat_id.
    """
    cleaned = clean_telegram_html(text)
    try:
        if hasattr(message_or_bot, "reply_text"):
            await message_or_bot.reply_text(
                cleaned,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=reply_markup
            )
        elif chat_id and hasattr(message_or_bot, "send_message"):
            await message_or_bot.send_message(
                chat_id=chat_id,
                text=cleaned,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.warning(f"Échec envoi Telegram HTML ({e}), fallback en texte brut")
        plain_text = re.sub(r"<[^>]+>", "", cleaned)
        if hasattr(message_or_bot, "reply_text"):
            await message_or_bot.reply_text(
                plain_text,
                disable_web_page_preview=True,
                reply_markup=reply_markup
            )
        elif chat_id and hasattr(message_or_bot, "send_message"):
            await message_or_bot.send_message(
                chat_id=chat_id,
                text=plain_text,
                disable_web_page_preview=True,
                reply_markup=reply_markup
            )


# ==============================================================================
# 1. TUNNEL D'ONBOARDING INTERACTIF (/start)
# ==============================================================================

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Point d'entrée de la commande /start.
    Vérifie si l'athlète existe déjà dans athlete_profiles :
      - Si oui : Message d'accueil "Bon retour guerrier !"
      - Si non : Déclenche le tunnel d'onboarding en 4 étapes.
    """
    user = update.effective_user
    user_id = user.id

    # 1. Vérification d'existence en BDD
    if athlete_profile_exists(user_id):
        welcome_back_text = (
            "🥋 <b>Bon retour guerrier !</b>\n\n"
            "Envoie ton check-in du jour pour recevoir ta séance.\n\n"
            "🔥 <i>Libertad & Performance.</i>"
        )
        await send_safe_html_message(update.message, welcome_back_text)
        return ConversationHandler.END

    # 2. Nouvel athlète : initialisation du tunnel d'onboarding
    context.user_data.clear()
    context.user_data["telegram_id"] = user_id
    context.user_data["username"] = user.username or ""
    context.user_data["raw_first_name"] = user.first_name or ""
    context.user_data["raw_last_name"] = user.last_name or ""

    intro_text = (
        "🥋 <b>BIENVENUE DANS LE SAMOURAI PERFORMANCE SYSTEM !</b>\n\n"
        "Je suis ton coach IA haute performance, conçu pour les combattants de MMA et athlètes exigeants.\n\n"
        "Avant de concevoir ta première séance sur-mesure, nous allons configurer ton profil athlète en 4 étapes rapides.\n\n"
        "👉 <b>Étape 1/4 :</b> Quel est ton <b>Nom et Prénom</b> (ou nom de combattant) ?"
    )
    await send_safe_html_message(update.message, intro_text)
    return ASK_NAME


async def handle_onboarding_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 1/4 : Récupère le nom/prénom et demande l'objectif.
    """
    name = update.message.text.strip()
    context.user_data["athlete_name"] = name

    step2_text = (
        f"Enchanté <b>{name}</b> ! 👊\n\n"
        "🎯 <b>Étape 2/4 :</b> Quel est ton <b>objectif principal</b> ?\n\n"
        "<i>(Exemples : MMA / Combat, Prépa Physique, Cardio & Perte de poids, Force / Explosivité...)</i>"
    )
    await send_safe_html_message(update.message, step2_text)
    return ASK_GOAL


async def handle_onboarding_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 2/4 : Récupère l'objectif et demande le lieu / matériel par défaut.
    """
    goal = update.message.text.strip()
    context.user_data["goal"] = goal

    step3_text = (
        "C'est noté ! 🎯\n\n"
        "🏋️ <b>Étape 3/4 :</b> Quel est ton <b>lieu d'entraînement habituel et ton matériel disponible par défaut</b> ?\n\n"
        "<i>(Exemples : Poids du corps / Chambre d'hôtel, Salle complète (Gym), 1 Kettlebell 16kg + élastiques...)</i>"
    )
    await send_safe_html_message(update.message, step3_text)
    return ASK_EQUIPMENT


async def handle_onboarding_equipment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 3/4 : Récupère le matériel et demande les blessures/contraintes physiques.
    """
    equipment = update.message.text.strip()
    context.user_data["default_equipment"] = equipment

    step4_text = (
        "Parfait pour l'équipement ! ⚙️\n\n"
        "🩹 <b>Étape 4/4 :</b> As-tu des <b>blessures récentes, douleurs ou contraintes physiques</b> à prendre en compte ?\n\n"
        "<i>(Réponds 'Aucune' si tout est opérationnel, ou précise : ex. genou droit sensible, épaule gauche fragile...)</i>"
    )
    await send_safe_html_message(update.message, step4_text)
    return ASK_INJURIES


async def handle_onboarding_injuries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 4/4 : Récupère les blessures, enregistre le profil dans Supabase et clôture l'onboarding.
    Notifie également automatiquement le Head Coach.
    """
    injuries = update.message.text.strip()
    context.user_data["injuries"] = injuries

    telegram_id = context.user_data.get("telegram_id") or update.effective_user.id
    name = context.user_data.get("athlete_name", update.effective_user.first_name)
    goal = context.user_data.get("goal", "MMA / Combat")
    equipment = context.user_data.get("default_equipment", "Poids du corps")
    username = context.user_data.get("username") or update.effective_user.username or ""
    raw_first = context.user_data.get("raw_first_name") or update.effective_user.first_name or ""
    raw_last = context.user_data.get("raw_last_name") or update.effective_user.last_name or ""

    # Sauvegarde complète dans Supabase (athletes + athlete_profiles)
    save_new_athlete_profile(
        telegram_id=telegram_id,
        athlete_name=name,
        goal=goal,
        default_equipment=equipment,
        injuries=injuries,
        username=username,
        raw_user_first_name=raw_first,
        raw_user_last_name=raw_last
    )

    # Notification automatique vers le COACH_TELEGRAM_ID
    coach_id = getattr(settings, "COACH_TELEGRAM_ID", None)
    if coach_id:
        try:
            coach_msg = (
                "<b>🔔 NOUVEL ATHLÈTE INSCRIT SUR LE BOT</b>\n\n"
                f"<b>Nom :</b> {name}\n"
                f"<b>Telegram ID :</b> <code>{telegram_id}</code> (@{username or 'N/A'})\n"
                f"<b>Objectif :</b> {goal}\n"
                f"<b>Matériel par défaut :</b> {equipment}\n"
                f"<b>Blessures / Contraintes :</b> {injuries}\n\n"
                "🔥 <i>Prêt pour le combat.</i>"
            )
            await send_safe_html_message(context.bot, coach_msg, chat_id=int(coach_id))
        except Exception as e:
            logger.error(f"Erreur notification nouvel athlète au coach: {e}")

    final_text = (
        "✅ <b>PROFIL ATHLÈTE ENREGISTRÉ AVEC SUCCÈS !</b>\n\n"
        f"🥋 <b>Guerrier :</b> {name}\n"
        f"🎯 <b>Objectif :</b> {goal}\n"
        f"🏋️ <b>Matériel par défaut :</b> {equipment}\n"
        f"🩹 <b>Contraintes :</b> {injuries}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👊 Tu es paré. Pour recevoir ta séance personnalisée, <b>envoie dès maintenant ton premier check-in quotidien</b> (texte ou vocal) décrivant ton sommeil, ton énergie (sur 10) et ton environnement du jour.\n\n"
        "🔥 <i>Libertad & Performance.</i>"
    )
    await send_safe_html_message(update.message, final_text)
    return ConversationHandler.END


async def handle_cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Annulation du tunnel d'onboarding.
    """
    context.user_data.clear()
    await send_safe_html_message(
        update.message,
        "❌ <i>Onboarding annulé. Tape /start quand tu seras prêt à configurer ton profil.</i>"
    )
    return ConversationHandler.END


# ==============================================================================
# 2. BOUTON FINIR LA SÉANCE & GESTION DU DÉBRIEFING
# ==============================================================================

async def handle_finish_workout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Déclenché lorsque l'athlète clique sur '✅ J'ai terminé ma séance !'.
    Félicite l'athlète et demande le RPE réel et un rapide retour.
    """
    query = update.callback_query
    await query.answer()

    context.user_data["awaiting_workout_feedback"] = True

    congrats_text = (
        "🔥 <b>BIEN JOUÉ GUERRIER ! SÉANCE TERMINÉE !</b>\n\n"
        "Pour clôturer et enregistrer ta performance en base de données :\n"
        "1️⃣ Quel est ton <b>RPE réel</b> (effort ressenti de 1 à 10) ?\n"
        "2️⃣ Donne un <b>rapide retour</b> sur tes sensations (texte ou vocal).\n\n"
        "<i>👉 Réponds directement à ce message (ex: 'RPE 8, super explosivité sur le circuit, un peu cuit sur la fin').</i>"
    )
    await send_safe_html_message(query.message, congrats_text)


# ==============================================================================
# 3. COMMANDE /HEBDO POUR LE HEAD COACH & SCHEDULER AUTOMATIQUE
# ==============================================================================

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

    status_msg = await update.message.reply_text(
        "⏳ <i>Analyse des 7 derniers jours et synthèse de l'athlète en cours...</i>",
        parse_mode="HTML"
    )

    try:
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

        # Sinon, déterminer l'athlète ayant eu de l'activité récente
        if not target_athlete:
            recent_logs = get_last_7_days_workout_logs(days=7)
            if recent_logs:
                active_ath_id = recent_logs[0].get("athlete_id")
                target_athlete = get_athlete_by_id(active_ath_id)

        # Fallback sur le premier athlète si disponible
        if not target_athlete and all_athletes:
            target_athlete = get_athlete_by_id(all_athletes[0].get("id"))

        if not target_athlete:
            target_athlete = {"first_name": "Athlète", "id": None, "goal": "MMA / Combat"}

        target_id = target_athlete.get("id")

        logs = get_last_7_days_workout_logs(athlete_id=target_id, days=7)
        checkins = get_last_7_days_checkins(athlete_id=target_id, days=7)

        summary_html = generate_weekly_coach_summary(logs=logs, athlete_info=target_athlete, checkins=checkins)

        await status_msg.delete()
        await send_safe_html_message(update.message, summary_html)

    except Exception as e:
        logger.error(f"Erreur commande /hebdo: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Erreur lors de la génération du bilan hebdo : {e}")


async def run_automatic_hebdo_summary(bot_instance: Bot):
    """
    Tâche automatique exécutée chaque dimanche à 08:00 par le scheduler.
    Génère et envoie le bilan /hebdo pour chaque athlète actif au Head Coach.
    """
    coach_id = getattr(settings, "COACH_TELEGRAM_ID", None)
    if not coach_id or not bot_instance:
        logger.warning("Scheduler hebdo ignoré : COACH_TELEGRAM_ID ou bot manquant.")
        return

    logger.info("🤖 Exécution de la tâche automatique /hebdo du dimanche...")
    try:
        all_athletes = get_all_athletes()
        if not all_athletes:
            logger.info("Aucun athlète trouvé pour le bilan hebdo automatique.")
            return

        for athlete in all_athletes:
            ath_id = athlete.get("id")
            if not ath_id:
                continue

            target_athlete = get_athlete_by_id(ath_id)
            logs = get_last_7_days_workout_logs(athlete_id=ath_id, days=7)
            checkins = get_last_7_days_checkins(athlete_id=ath_id, days=7)

            # Ne générer que s'il y a un minimum d'activité ou de profil
            summary_html = generate_weekly_coach_summary(logs=logs, athlete_info=target_athlete, checkins=checkins)
            await send_safe_html_message(bot_instance, summary_html, chat_id=int(coach_id))

        logger.info("✅ Bilans hebdo automatiques envoyés au Head Coach avec succès.")
    except Exception as e:
        logger.error(f"Erreur lors de l'exécution automatique des bilans hebdo: {e}", exc_info=True)


# ==============================================================================
# 4. TRAITEMENT DES MESSAGES TEXTE & VOCAUX
# ==============================================================================

async def _send_missing_checkin_prompt(target, context: ContextTypes.DEFAULT_TYPE, missing_info: str):
    """Présente uniquement les boutons nécessaires pour compléter le check-in."""
    if missing_info == "energy":
        text = "Salut ! Comment te sens-tu aujourd'hui au niveau énergie ? 💪"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Fatigué (1-4)", callback_data="checkin_energy_3")],
            [InlineKeyboardButton("🟡 En forme (5-7)", callback_data="checkin_energy_6")],
            [InlineKeyboardButton("🟢 Au top (8-10)", callback_data="checkin_energy_9")],
        ])
    else:
        text = "Où te trouves-tu pour la séance du jour ?"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Chambre / Poids du corps", callback_data="checkin_equipment_bodyweight")],
            [InlineKeyboardButton("🏋️ Salle / Matériel complet", callback_data="checkin_equipment_gym")],
        ])
    await send_safe_html_message(target, text, reply_markup=keyboard)


async def _generate_workout_from_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                           user_text: str, analysis: Any, target,
                                           status_message=None):
    """Enregistre le check-in et génère la séance à partir d'une analyse validée."""
    user_id = update.effective_user.id
    athlete = get_athlete_profile(user_id)
    athlete_id = athlete.get("id")
    if athlete_id:
        log_checkin(athlete_id=athlete_id, analysis=analysis, raw_text=user_text)

    equipment = analysis.equipment_available or athlete.get("default_equipment", "Poids du corps")
    exercises = get_available_exercises(equipment)
    workout_plan = generate_daily_workout(analysis, exercises, athlete)

    if athlete_id:
        prescribed_rpe = getattr(analysis, "rpe", None) or 7
        log_workout_generation(
            athlete_id=athlete_id,
            prescribed_rpe=prescribed_rpe,
            summary_text=f"Séance {equipment} - Énergie {analysis.energy_score}/10"
        )

    finish_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ J'ai terminé ma séance !", callback_data="finish_workout")]
    ])
    response_message = (
        f"<b>{analysis.feedback_coach}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📋 <b>TA SÉANCE DU JOUR</b>\n\n"
        f"{workout_plan}"
    )
    if status_message:
        await status_message.delete()
    await send_safe_html_message(target, response_message, reply_markup=finish_keyboard)


async def _process_athlete_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    """Traite un check-in texte ou la transcription d'un vocal."""
    user_id = update.effective_user.id
    athlete = get_athlete_profile(user_id)
    athlete_name = athlete.get("first_name", "Combattant")
    lower_text = user_text.lower()

    awaiting_feedback = context.user_data.get("awaiting_workout_feedback", False)
    is_debrief_keywords = any(w in lower_text for w in [
        "séance terminée", "seance terminee", "séance faite", "seance faite",
        "débrief", "debrief", "fini la séance", "fini ma séance", "rpe"
    ]) and not any(w in lower_text for w in ["programme", "quelle séance", "donne-moi", "prépare"])
    if awaiting_feedback or is_debrief_keywords:
        debrief_data = parse_debrief_with_gemini(user_text)
        rpe_val = debrief_data.get("rpe_real", 7)
        coach_msg = debrief_data.get("coach_reply", "Séance validée guerrier !")
        log_workout_completion(athlete_id=athlete.get("id"), telegram_id=user_id,
                               rpe_real=rpe_val, feedback_text=user_text, completed=True)
        context.user_data["awaiting_workout_feedback"] = False
        await send_safe_html_message(update.message, (
            "<b>🥋 DÉBRIEFING ENREGISTRÉ EN BDD !</b>\n\n"
            f"Bien reçu <b>{athlete_name}</b>. Séance validée avec un RPE réel de <b>{rpe_val}/10</b>.\n\n"
            f"💬 <i>{coach_msg}</i>\n\n"
            "💡 <i>Tes données ont été transmises au Head Coach. Place à la récupération !</i>\n\n"
            "🔥 <b>Libertad & Performance.</b>"
        ))
        return

    try:
        analysis = analyze_checkin_with_gemini(user_text)
        if not analysis.is_valid_checkin:
            context.user_data["pending_checkin"] = {"raw_text": user_text}
            await _send_missing_checkin_prompt(update.message, context, analysis.missing_info or "energy")
            return

        status_message = await update.message.reply_text(
            "⏳ <i>Analyse de ton check-in et préparation de ta séance en cours...</i>",
            parse_mode="HTML"
        )
        await _generate_workout_from_analysis(update, context, user_text, analysis,
                                              update.message, status_message)
    except Exception as e:
        logger.error(f"Erreur traitement checkin : {e}", exc_info=True)
        await send_safe_html_message(update.message, f"❌ Erreur lors du traitement du check-in : {e}")


async def handle_checkin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Complète le check-in avec le choix inline puis relance le flux smart."""
    query = update.callback_query
    await query.answer()
    pending = context.user_data.get("pending_checkin") or {}
    raw_text = pending.get("raw_text", "")
    data = query.data or ""
    if data.startswith("checkin_energy_"):
        value = data.rsplit("_", 1)[-1]
        addition = f"Énergie du jour : {value}/10."
    elif data == "checkin_equipment_bodyweight":
        addition = "Lieu et matériel du jour : chambre, poids du corps, sans matériel."
    elif data == "checkin_equipment_gym":
        addition = "Lieu et matériel du jour : salle complète, matériel de musculation disponible."
    else:
        return

    combined_text = f"{raw_text}\n{addition}".strip()
    try:
        analysis = analyze_checkin_with_gemini(combined_text)
        if not analysis.is_valid_checkin:
            context.user_data["pending_checkin"] = {"raw_text": combined_text}
            await _send_missing_checkin_prompt(query.message, context, analysis.missing_info or "energy")
            return
        context.user_data.pop("pending_checkin", None)
        status_message = await query.message.reply_text(
            "⏳ <i>Analyse de ton check-in et préparation de ta séance en cours...</i>",
            parse_mode="HTML"
        )
        await _generate_workout_from_analysis(update, context, combined_text, analysis,
                                              query.message, status_message)
    except Exception as e:
        logger.error(f"Erreur callback check-in : {e}", exc_info=True)
        await send_safe_html_message(query.message, f"❌ Erreur lors de la génération de la séance : {e}")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestion des messages texte de l'athlète.
    """
    if not update.message or not update.message.text:
        return
    await _process_athlete_input(update, context, update.message.text)


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestion des messages vocaux de l'athlète (transcription Gemini puis traitement).
    """
    if not update.message or not update.message.voice:
        return

    transcribe_msg = await update.message.reply_text("🎙️ <i>Écoute et transcription de ton vocal...</i>", parse_mode="HTML")
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        audio_bytes = await voice_file.download_as_bytearray()
        transcription = transcribe_audio_with_gemini(bytes(audio_bytes), mime_type="audio/ogg")
        await transcribe_msg.delete()

        if not transcription:
            await send_safe_html_message(update.message, "⚠️ <i>Impossible de retranscrire le message vocal. Merci d'envoyer un message texte.</i>")
            return

        logger.info(f"Vocal retranscrit de {update.effective_user.id} : {transcription}")
        await _process_athlete_input(update, context, transcription)
    except Exception as e:
        logger.error(f"Erreur traitement vocal: {e}", exc_info=True)
        await transcribe_msg.edit_text(f"❌ Erreur de traitement audio : {e}")


# ==============================================================================
# 5. INITIALISATION DE L'APPLICATION TELEGRAM
# ==============================================================================

def create_telegram_application():
    """
    Initialise l'application Telegram avec ses handlers et son ConversationHandler.
    """
    global _global_telegram_application
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN manquant.")
        return None

    request = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
    application = ApplicationBuilder().token(token).request(request).build()

    # 1. Tunnel d'Onboarding Interactif (/start)
    onboarding_conv = ConversationHandler(
        entry_points=[CommandHandler("start", handle_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_name)],
            ASK_GOAL: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_goal)],
            ASK_EQUIPMENT: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_equipment)],
            ASK_INJURIES: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_injuries)],
        },
        fallbacks=[CommandHandler("cancel", handle_cancel_onboarding)],
        allow_reentry=True
    )
    application.add_handler(onboarding_conv)

    # 2. Commandes Coach & Utilitaires
    application.add_handler(CommandHandler("hebdo", handle_hebdo_command))

    # 3. Callback Query : check-in smart puis bouton de fin de séance
    application.add_handler(CallbackQueryHandler(handle_checkin_callback, pattern="^checkin_(energy|equipment)_"))
    application.add_handler(CallbackQueryHandler(handle_finish_workout_callback, pattern="^finish_workout$"))

    # 4. Messages Texte (Check-ins et Débriefings hors onboarding)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_message))

    # 5. Messages Vocaux
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    _global_telegram_application = application
    return application
