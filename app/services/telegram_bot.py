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
    parse_debrief_with_gemini,
    analyze_nutrition_entry,
    calculate_target_macros,
    evaluate_wisdom_guidance
)
from app.services.reporting import generate_weekly_report_chart
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
    log_nutrition_entry,
    get_last_7_days_workout_logs,
    get_last_7_days_checkins,
    log_daily_metric,
    get_athlete_metrics_history,
    check_and_trigger_coach_alerts
)

logger = logging.getLogger(__name__)

# États pour le tunnel d'onboarding ConversationHandler (5 étapes structurées)
(
    ASK_NAME,
    ASK_TRACKING_TYPE,
    ASK_NUTRITION_MODE,
    ASK_WEIGHT_AND_ACTIVITY,
    ASK_SERVICE_TIER,
    ASK_GOAL,
    ASK_EQUIPMENT,
    ASK_INJURIES
) = range(8)

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
# 1. TUNNEL D'ONBOARDING INTERACTIF (/start) — 5 ÉTAPES FLUIDES
# ==============================================================================

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Point d'entrée de la commande /start.
    Vérifie si l'athlète existe déjà dans athlete_profiles :
      - Si oui : Message d'accueil "Bon retour guerrier !"
      - Si non : Déclenche le tunnel d'onboarding en 5 étapes.
    """
    user = update.effective_user
    user_id = user.id

    # 1. Vérification d'existence en BDD
    if athlete_profile_exists(user_id):
        welcome_back_text = (
            "🥋 <b>Bon retour guerrier !</b>\n\n"
            "Envoie ton check-in du jour pour recevoir ta séance, ou la photo de ton plat pour analyser tes macros.\n\n"
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
        "🥋 <b>BIENVENUE DANS L'AMAZONIAN SAMOURAI PERFORMANCE SYSTEM !</b>\n\n"
        "Je suis ton coach IA haute performance, fondé sur la méthode de Jason Ponet pour les combattants de MMA et athlètes exigeants.\n\n"
        "Avant de concevoir tes séances et calibrer ta nutrition 'Poids de combat', nous allons configurer ton profil athlète en 5 étapes rapides.\n\n"
        "👉 <b>Étape 1/5 :</b> Quel est ton <b>Nom et Prénom</b> (ou nom de combattant) ?"
    )
    await send_safe_html_message(update.message, intro_text)
    return ASK_NAME


async def handle_onboarding_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 1/5 : Récupère le nom/prénom et propose le choix du type de suivi.
    """
    name = update.message.text.strip()
    context.user_data["athlete_name"] = name

    tracking_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏋️ Suivi Sportif seul", callback_data="track_sport")],
        [InlineKeyboardButton("🥗 Suivi Nutritionnel ('Poids de combat')", callback_data="track_nutrition")],
        [InlineKeyboardButton("⚡ Les Deux (Sport & Nutrition)", callback_data="track_both")],
    ])

    step2_text = (
        f"Enchanté <b>{name}</b> ! 👊\n\n"
        "🎯 <b>Étape 2/5 : Quel type de suivi souhaites-tu activer ?</b>\n\n"
        "• <b>Suivi Sportif :</b> Séances sur-mesure (Cross-training, Calisthenics, Kettlebells, French Contrast), gestion de la fatigue et débriefings RPE.\n"
        "• <b>Suivi Nutritionnel :</b> Méthode 'Poids de combat' (déficit intelligent, ~2g/kg protéines, seuil lipides de sécurité).\n"
        "• <b>Les Deux :</b> L'écosystème complet pour maximiser ta puissance et affûter ton poids."
    )
    await send_safe_html_message(update.message, step2_text, reply_markup=tracking_keyboard)
    return ASK_TRACKING_TYPE


async def handle_onboarding_tracking_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 2/5 : Récupère le choix de suivi (sport, nutrition, both) via bouton ou texte.
    """
    tracking_type = "both"
    target_msg = update.message

    if update.callback_query:
        await update.callback_query.answer()
        data = update.callback_query.data or ""
        target_msg = update.callback_query.message
        if "sport" in data:
            tracking_type = "sport"
        elif "nutrition" in data:
            tracking_type = "nutrition"
        else:
            tracking_type = "both"
    elif update.message and update.message.text:
        text = update.message.text.lower()
        if "sport" in text and "nutrition" not in text:
            tracking_type = "sport"
        elif "nutrition" in text and "sport" not in text:
            tracking_type = "nutrition"
        else:
            tracking_type = "both"

    context.user_data["tracking_type"] = tracking_type

    # Si l'athlète choisit uniquement le sport, on passe directement au niveau de service (Étape 4)
    if tracking_type == "sport":
        context.user_data["nutrition_mode"] = None
        tier_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🥋 Loisir & Déterminé (95% IA)", callback_data="segment_loisir")],
            [InlineKeyboardButton("⚡ Cellule Élite (Suivi Pro)", callback_data="segment_elite")],
        ])
        step4_text = (
            "C'est noté pour le <b>Suivi Sportif</b> ! 🏋️\n\n"
            "🥋 <b>Étape 4/5 : Quel est ton segment de coaching ?</b>\n\n"
            "• <b>Loisir & Déterminé (95% IA) :</b> Autonomie complète, programmation personnalisée et réactivité 24/7.\n"
            "• <b>Cellule Élite (Suivi Pro) :</b> Suivi haute performance réservé aux combattants, supervisé avec alertes intelligentes transmises au Head Coach Jason Ponet."
        )
        await send_safe_html_message(target_msg, step4_text, reply_markup=tier_keyboard)
        return ASK_SERVICE_TIER

    # Sinon (Nutrition ou Les Deux), on propose le choix du mode nutritionnel (Étape 3)
    nutri_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Mode A : Pratique / Visuel (OCR Assiette)", callback_data="nutri_ocr")],
        [InlineKeyboardButton("📊 Mode B : Rigoureux (App tierce / Saisie)", callback_data="nutri_app")],
    ])

    step3_text = (
        "C'est noté pour le <b>Suivi Nutritionnel ('Poids de combat')</b> ! 🥗\n\n"
        "👉 <b>Étape 3/5 : Quel est ton mode de suivi préféré pour tes repas ?</b>\n\n"
        "• <b>Mode A : Pratique / Visuel (OCR Assiette)</b>\n"
        "Prends simplement une photo de ton plat : l'IA analyse visuellement la règle des 3 zones (1/2 légumes, 1/4 protéines, 1/4 glucides) et te fait un retour instantané.\n\n"
        "• <b>Mode B : Rigoureux (App tierce / Saisie)</b>\n"
        "Envoie une capture d'écran de ton application (type MyFitnessPal) ou tes macros textuelles au gramme près pour validation."
    )
    await send_safe_html_message(target_msg, step3_text, reply_markup=nutri_keyboard)
    return ASK_NUTRITION_MODE


async def handle_onboarding_nutrition_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 3/5 (a) : Enregistre le mode nutritionnel choisi puis demande le poids et l'activité.
    """
    nutrition_mode = "ocr_vision"
    target_msg = update.message

    if update.callback_query:
        await update.callback_query.answer()
        data = update.callback_query.data or ""
        target_msg = update.callback_query.message
        if "app" in data:
            nutrition_mode = "app_tierce"
        else:
            nutrition_mode = "ocr_vision"
    elif update.message and update.message.text:
        text = update.message.text.lower()
        if any(k in text for k in ["app", "tierce", "myfitnesspal", "mfp", "rigoureux", "saisie"]):
            nutrition_mode = "app_tierce"
        else:
            nutrition_mode = "ocr_vision"

    context.user_data["nutrition_mode"] = nutrition_mode
    mode_label = "Mode A (Pratique / Visuel — OCR Assiette)" if nutrition_mode == "ocr_vision" else "Mode B (Rigoureux — App tierce / Saisie)"

    prompt_weight_text = (
        f"✅ <b>{mode_label} activé !</b>\n\n"
        "⚖️ Pour calibrer scientifiquement tes macros cibles selon la méthode de Jason Ponet (~2g/kg de protéines, minimum 0.8 à 1g/kg de lipides, déficit modéré sans fonte musculaire) :\n\n"
        "👉 <b>Quel est ton poids de corps actuel (en kg)</b> et ton <b>niveau d'activité habituel</b> ?\n\n"
        "<i>(Exemples : '76 kg, très actif MMA' ou '82 kg, modéré 3 entraînements/semaine')</i>"
    )
    await send_safe_html_message(target_msg, prompt_weight_text)
    return ASK_WEIGHT_AND_ACTIVITY


async def handle_onboarding_weight_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 3/5 (b) : Parse le poids et l'activité, calcule les macros cibles et enchaîne sur l'Étape 4 (Niveau de service).
    """
    user_text = update.message.text.strip()
    
    # Extraction du poids (en kg)
    weight_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:kg|kilos?)?", user_text, re.IGNORECASE)
    if weight_match:
        weight_kg = float(weight_match.group(1).replace(",", "."))
    else:
        weight_kg = 75.0

    context.user_data["weight_kg"] = weight_kg
    context.user_data["activity_level"] = user_text

    # Calcul scientifique déterministe des macros cibles
    macros = calculate_target_macros(weight_kg, user_text)
    context.user_data["target_calories"] = macros["calories"]
    context.user_data["target_proteins"] = macros["proteins"]
    context.user_data["target_fats"] = macros["fats"]
    context.user_data["target_carbs"] = macros["carbs"]

    tier_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🥋 Loisir & Déterminé (95% IA)", callback_data="segment_loisir")],
        [InlineKeyboardButton("⚡ Cellule Élite (Suivi Pro)", callback_data="segment_elite")],
    ])

    summary_macros_text = (
        "🔥 <b>CIBLES NUTRITIONNELLES INITIALISÉES ('POIDS DE COMBAT') :</b>\n\n"
        f"• <b>Poids de référence :</b> {weight_kg} kg\n"
        f"• <b>Calories cibles :</b> ~<b>{macros['calories']} kcal/jour</b> (déficit maîtrisé pour sécher sans perdre de muscle)\n"
        f"• <b>Protéines :</b> ~<b>{macros['proteins']} g/jour</b> (~2g/kg pour blinder la masse musculaire)\n"
        f"• <b>Lipides :</b> ~<b>{macros['fats']} g/jour</b> (sécurité hormonale absolue, min 0.8-1g/kg)\n"
        f"• <b>Glucides :</b> ~<b>{macros['carbs']} g/jour</b> (carburant stratégique pour tes séances)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🥋 <b>Étape 4/5 : Quel est ton segment de coaching ?</b>\n\n"
        "• <b>Loisir & Déterminé (95% IA) :</b> Programmation et analyse de repas instantanés 24/7.\n"
        "• <b>Cellule Élite (Suivi Pro) :</b> Suivi haute performance réservé aux combattants, supervisé avec alertes intelligentes transmises au Head Coach Jason Ponet."
    )
    await send_safe_html_message(update.message, summary_macros_text, reply_markup=tier_keyboard)
    return ASK_SERVICE_TIER


async def handle_onboarding_service_tier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 4/5 : Récupère le segment (loisir ou elite) et le niveau de service (100%_ia ou hybride).
    """
    segment = "loisir"
    service_tier = "100%_ia"
    target_msg = update.message

    if update.callback_query:
        await update.callback_query.answer()
        data = (update.callback_query.data or "").lower()
        target_msg = update.callback_query.message
        if "elite" in data or "hybride" in data:
            segment = "elite"
            service_tier = "hybride"
        else:
            segment = "loisir"
            service_tier = "100%_ia"
    elif update.message and update.message.text:
        text = update.message.text.lower()
        if any(k in text for k in ["elite", "élite", "hybride", "coach", "jason", "2", "pro"]):
            segment = "elite"
            service_tier = "hybride"
        else:
            segment = "loisir"
            service_tier = "100%_ia"

    context.user_data["segment"] = segment
    context.user_data["service_tier"] = service_tier
    tier_label = "⚡ Cellule Élite (Suivi Pro supervisé par Jason Ponet)" if segment == "elite" else "🥋 Loisir & Déterminé (95% IA)"

    tracking_type = context.user_data.get("tracking_type", "both")

    if tracking_type in ["sport", "both"]:
        step5_text = (
            f"✅ <b>{tier_label} sélectionné !</b>\n\n"
            "🎯 <b>Étape 5/5 : Paramètres d'entraînement</b>\n\n"
            "Quel est ton <b>objectif principal</b> et éventuellement ton <b>poids de combat cible</b> ?\n\n"
            "<i>(Exemples : 'Prépa combat MMA, cible 70 kg', 'Explosivité & Force', 'Cardio & Sèche...')</i>"
        )
    else:
        step5_text = (
            f"✅ <b>{tier_label} sélectionné !</b>\n\n"
            "🎯 <b>Étape 5/5 : Objectif silhouette & combat</b>\n\n"
            "Quel est ton <b>objectif principal</b> et ton <b>poids de combat cible</b> (en kg) ?\n\n"
            "<i>(Exemples : 'Perte de gras, cible 68 kg', 'Sèche musculaire', 'Maintien et énergie...')</i>"
        )

    await send_safe_html_message(target_msg, step5_text)
    return ASK_GOAL


async def handle_onboarding_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 5/5 (a) : Récupère l'objectif et demande le matériel ou les contraintes.
    """
    goal = update.message.text.strip()
    context.user_data["goal"] = goal

    target_match = re.search(r"(?:cible|objectif|vis[ée]|viser)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(?:kg)?", goal, re.IGNORECASE)
    if target_match:
        context.user_data["target_weight_kg"] = float(target_match.group(1).replace(",", "."))
    tracking_type = context.user_data.get("tracking_type", "both")

    if tracking_type in ["sport", "both"]:
        step_eq_text = (
            "C'est noté ! 🎯\n\n"
            "🏋️ Quel est ton <b>lieu d'entraînement habituel et ton matériel disponible par défaut</b> ?\n\n"
            "<i>(Exemples : Poids du corps / Chambre d'hôtel, Salle complète (Gym), 1 Kettlebell 16kg + élastiques...)</i>"
        )
        await send_safe_html_message(update.message, step_eq_text)
        return ASK_EQUIPMENT
    else:
        # Suivi nutritionnel pur : pas besoin de matériel sportif
        context.user_data["default_equipment"] = "Aucun (Suivi Nutritionnel)"
        step_inj_text = (
            "Parfait ! 🎯\n\n"
            "🩹 As-tu des <b>allergies, intolérances alimentaires ou contraintes médicales</b> à prendre en compte ?\n\n"
            "<i>(Réponds 'Aucune' si tout est OK, ou précise)</i>"
        )
        await send_safe_html_message(update.message, step_inj_text)
        return ASK_INJURIES


async def handle_onboarding_equipment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 5/5 (b) : Récupère le matériel et demande les blessures/contraintes physiques.
    """
    equipment = update.message.text.strip()
    context.user_data["default_equipment"] = equipment

    step_inj_text = (
        "Parfait pour l'équipement ! ⚙️\n\n"
        "🩹 As-tu des <b>blessures récentes, douleurs ou contraintes physiques</b> à prendre en compte ?\n\n"
        "<i>(Réponds 'Aucune' si tout est opérationnel, ou précise : ex. genou droit sensible, épaule gauche fragile...)</i>"
    )
    await send_safe_html_message(update.message, step_inj_text)
    return ASK_INJURIES


async def handle_onboarding_injuries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Étape 5/5 (c) : Récupère les contraintes/blessures, enregistre le profil complet dans Supabase
    et notifie le Head Coach Jason Ponet.
    """
    injuries = update.message.text.strip()
    context.user_data["injuries"] = injuries

    telegram_id = context.user_data.get("telegram_id") or update.effective_user.id
    name = context.user_data.get("athlete_name", update.effective_user.first_name)
    goal = context.user_data.get("goal", "MMA / Performance")
    equipment = context.user_data.get("default_equipment", "Poids du corps")
    username = context.user_data.get("username") or update.effective_user.username or ""
    raw_first = context.user_data.get("raw_first_name") or update.effective_user.first_name or ""
    raw_last = context.user_data.get("raw_last_name") or update.effective_user.last_name or ""
    
    tracking_type = context.user_data.get("tracking_type", "both")
    nutrition_mode = context.user_data.get("nutrition_mode")
    service_tier = context.user_data.get("service_tier", "100%_ia")
    segment = context.user_data.get("segment", "loisir")
    weight_kg = context.user_data.get("weight_kg")
    target_weight_kg = context.user_data.get("target_weight_kg")
    activity_level = context.user_data.get("activity_level")
    target_calories = context.user_data.get("target_calories")
    target_proteins = context.user_data.get("target_proteins")
    target_fats = context.user_data.get("target_fats")
    target_carbs = context.user_data.get("target_carbs")

    # Sauvegarde complète dans Supabase (athletes + athlete_profiles)
    save_new_athlete_profile(
        telegram_id=telegram_id,
        athlete_name=name,
        goal=goal,
        default_equipment=equipment,
        injuries=injuries,
        username=username,
        raw_user_first_name=raw_first,
        raw_user_last_name=raw_last,
        tracking_type=tracking_type,
        nutrition_mode=nutrition_mode,
        service_tier=service_tier,
        weight_kg=weight_kg,
        activity_level=activity_level,
        target_calories=target_calories,
        target_proteins=target_proteins,
        target_fats=target_fats,
        target_carbs=target_carbs,
        segment=segment,
        target_weight_kg=target_weight_kg
    )

    # Notification automatique vers le COACH_TELEGRAM_ID
    coach_id = getattr(settings, "COACH_TELEGRAM_ID", None)
    if coach_id:
        try:
            coach_msg = (
                "<b>🔔 NOUVEL ATHLÈTE INSCRIT SUR LE BOT</b>\n\n"
                f"<b>Nom :</b> {name}\n"
                f"<b>Telegram ID :</b> <code>{telegram_id}</code> (@{username or 'N/A'})\n"
                f"<b>Segment :</b> {segment.upper()}\n"
                f"<b>Suivi activé :</b> {tracking_type.upper()}\n"
                f"<b>Mode Nutrition :</b> {nutrition_mode or 'N/A'}\n"
                f"<b>Niveau de service :</b> {service_tier}\n"
                f"<b>Poids actuel :</b> {weight_kg or 'N/A'} kg | <b>Cible :</b> {target_weight_kg or 'N/A'} kg\n"
                f"<b>Objectif :</b> {goal}\n"
                f"<b>Matériel :</b> {equipment}\n"
                f"<b>Blessures / Contraintes :</b> {injuries}\n\n"
                "🔥 <i>Prêt pour le combat.</i>"
            )
            await send_safe_html_message(context.bot, coach_msg, chat_id=int(coach_id))
        except Exception as e:
            logger.error(f"Erreur notification nouvel athlète au coach: {e}")

    # Instructions de démarrage personnalisées selon le suivi activé
    if tracking_type == "sport":
        start_instruction = (
            "🏋️ <b>POUR DÉMARRER TON ENTRAÎNEMENT :</b>\n"
            "Envoie dès maintenant ton <b>check-in quotidien</b> (texte ou vocal) décrivant ton sommeil, ton énergie (sur 10) et ton lieu du jour pour recevoir ta première séance sur-mesure !"
        )
    elif tracking_type == "nutrition":
        start_instruction = (
            "🥗 <b>POUR DÉMARRER TA NUTRITION 'POIDS DE COMBAT' :</b>\n"
            "Dès ton prochain repas, envoie une <b>photo de ton assiette</b> (Mode Visuel) ou une <b>capture MyFitnessPal / tes macros</b> (Mode Rigoureux) pour analyse instantanée !"
        )
    else:
        start_instruction = (
            "⚡ <b>POUR DÉMARRER :</b>\n"
            "• <b>Entraînement :</b> Envoie ton check-in (énergie/sommeil/lieu) pour recevoir ta séance sur-mesure.\n"
            "• <b>Nutrition :</b> Envoie la photo de ton plat ou tes macros dès ton prochain repas pour validation !"
        )

    final_text = (
        "✅ <b>PROFIL ATHLÈTE ENREGISTRÉ AVEC SUCCÈS !</b>\n\n"
        f"🥋 <b>Guerrier :</b> {name}\n"
        f"⚡ <b>Suivi :</b> {tracking_type.upper()} | <b>Service :</b> {service_tier}\n"
        f"🎯 <b>Objectif :</b> {goal}\n"
        f"🏋️ <b>Matériel :</b> {equipment}\n"
        f"🩹 <b>Contraintes :</b> {injuries}\n"
        + (f"🥗 <b>Cibles :</b> {target_calories} kcal | {target_proteins}g P | {target_fats}g L | {target_carbs}g G\n" if target_calories else "") +
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{start_instruction}\n\n"
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

        # Génération du graphique Matplotlib Dark Samourai
        metrics_history = get_athlete_metrics_history(athlete_id=target_id, days=7)
        chart_bytes = generate_weekly_report_chart(target_athlete, metrics_history)

        await status_msg.delete()
        if chart_bytes:
            try:
                await update.message.reply_photo(
                    photo=chart_bytes,
                    caption=f"📈 <b>Bilan Visuel Samourai — {target_athlete.get('first_name', 'Athlète')}</b>",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Impossible d'envoyer le graphique hebdo: {e}")

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
            metrics_history = get_athlete_metrics_history(athlete_id=ath_id, days=7)
            chart_bytes = generate_weekly_report_chart(target_athlete, metrics_history)

            if chart_bytes:
                try:
                    await bot_instance.send_photo(
                        chat_id=int(coach_id),
                        photo=chart_bytes,
                        caption=f"📈 <b>Bilan Hebdo — {target_athlete.get('first_name', 'Athlète')}</b>",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Impossible d'envoyer le graphique hebdo auto: {e}")

            await send_safe_html_message(bot_instance, summary_html, chat_id=int(coach_id))

        logger.info("✅ Bilans hebdo automatiques envoyés au Head Coach avec succès.")
    except Exception as e:
        logger.error(f"Erreur lors de l'exécution automatique des bilans hebdo: {e}", exc_info=True)


async def handle_bilan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Commande /bilan ou /tendance pour l'athlète :
    Génère et renvoie son graphique Dark Samourai avec l'analyse de tendance et les conseils de sagesse IA ("Ne rien changer").
    """
    user_id = update.effective_user.id
    athlete = get_athlete_profile(user_id)
    athlete_id = athlete.get("id")
    if not athlete_id:
        await send_safe_html_message(
            update.message,
            "⚠️ <b>Profil non configuré</b> : Tape <code>/start</code> pour initialiser ton profil athlète."
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ <i>Génération de ton bilan graphique et analyse de ta tendance en cours...</i>",
        parse_mode="HTML"
    )

    try:
        metrics_history = get_athlete_metrics_history(athlete_id=athlete_id, days=7)
        chart_bytes = generate_weekly_report_chart(athlete, metrics_history)

        # Calcul de la tendance 7j et sagesse IA
        weights = [m["weight_kg"] for m in metrics_history if m.get("weight_kg") is not None]
        trend_7d = (weights[-1] - weights[0]) if len(weights) >= 2 else 0.0
        energies = [m["energy_score"] for m in metrics_history if m.get("energy_score") is not None]
        avg_energy = (sum(energies) / len(energies)) if energies else 7.0

        wisdom = evaluate_wisdom_guidance(
            weight_trend_7d_kg=trend_7d,
            avg_energy=avg_energy,
            current_weight=athlete.get("weight_kg"),
            target_weight=athlete.get("target_weight_kg")
        )

        caption = (
            f"<b>📊 BILAN DE PERFORMANCE — {athlete.get('first_name', 'Guerrier').upper()}</b>\n\n"
            f"🥋 <b>Segment :</b> {(athlete.get('segment') or 'loisir').upper()}\n"
            f"⚖️ <b>Poids actuel :</b> {athlete.get('weight_kg', 'N/A')} kg | <b>Cible :</b> {athlete.get('target_weight_kg', 'N/A')} kg\n"
            f"📈 <b>Tendance 7j :</b> {trend_7d:+.2f} kg | <b>Énergie moy :</b> {avg_energy:.1f}/10\n\n"
            f"💡 <b>{wisdom['rule']} :</b>\n<i>{wisdom['message']}</i>\n\n"
            "🔥 <b>Libertad & Performance.</b>"
        )

        await status_msg.delete()
        if chart_bytes:
            try:
                await update.message.reply_photo(
                    photo=chart_bytes,
                    caption=clean_telegram_html(caption),
                    parse_mode="HTML"
                )
                return
            except Exception as e:
                logger.warning(f"Erreur envoi photo bilan: {e}")

        await send_safe_html_message(update.message, caption)

    except Exception as e:
        logger.error(f"Erreur commande /bilan: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Erreur lors de la génération du bilan : {e}")


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
        log_daily_metric(
            athlete_id=athlete_id,
            telegram_id=user_id,
            energy_score=analysis.energy_score,
            fatigue_score=getattr(analysis, "fatigue_score", None),
            sleep_score=getattr(analysis, "sleep_score", None),
            notes=user_text
        )
        await check_and_trigger_coach_alerts(
            athlete_profile=athlete,
            event_type="checkin",
            data={
                "analysis": analysis,
                "energy_score": analysis.energy_score,
                "fatigue_score": getattr(analysis, "fatigue_score", None)
            },
            bot_instance=context.bot
        )

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
    """
    Traite un message texte ou transcrit :
    0. Détection prioritaire des mots-clés critiques (blessure, malaise, douleur aiguë).
    1. Détection des pesées quotidiennes (poids, tendance 7j, Sagesse IA 'Ne rien changer').
    2. Débriefing de séance terminée (RPE réel et enregistrement daily_metrics).
    3. Suivi nutritionnel / repas / questions sur le poids de combat.
    4. Check-in d'entraînement quotidien & génération de séance.
    """
    user_id = update.effective_user.id
    athlete = get_athlete_profile(user_id)
    athlete_name = athlete.get("first_name", "Combattant")
    lower_text = user_text.lower()

    # 0. Détection prioritaire des signaux d'alerte critiques
    critical_keywords = [
        "blessure", "douleur aiguë", "douleur aigue", "vertige", "vertiges",
        "malaise", "malaises", "craqué", "craque", "claquage", "claqué",
        "déchirure", "dechirure", "fracture", "bloqué", "bloque le dos"
    ]
    if any(cw in lower_text for cw in critical_keywords):
        await check_and_trigger_coach_alerts(
            athlete_profile=athlete,
            event_type="critical_keyword",
            data={"text": user_text},
            bot_instance=context.bot
        )
        alert_reply = (
            f"⚠️ <b>ALERTE DE SÉCURITÉ — {athlete_name.upper()} !</b>\n\n"
            "Tu as mentionné une douleur aiguë ou un signal corporel critique.\n\n"
            "🛑 <b>CONSIGNE IMMÉDIATE DU COACH :</b>\n"
            "• Arrête tout entraînement immédiatement.\n"
            "• Ne force absolument pas sur la douleur.\n"
            "• Ton Head Coach Jason Ponet a été prévenu en priorité.\n\n"
            "💡 <i>Hydrate-toi et consulte un professionnel de santé si la douleur persiste.</i>\n\n"
            "🔥 <b>Libertad & Sécurité.</b>"
        )
        await send_safe_html_message(update.message, alert_reply)
        return

    # 1. Détection explicite de pesée (ex: "74.5 kg", "poids: 76 kg", "pesée 75.2")
    weight_match = re.search(r"\b(?:pes[ée]e?|poids)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(?:kg)?\b", lower_text)
    if not weight_match:
        weight_match = re.match(r"^\s*(\d{2}(?:[.,]\d+)?)\s*kg\s*$", lower_text)

    if weight_match:
        logged_weight = float(weight_match.group(1).replace(",", "."))
        if 40.0 <= logged_weight <= 200.0:
            log_daily_metric(
                athlete_id=athlete.get("id"),
                telegram_id=user_id,
                weight_kg=logged_weight,
                notes=user_text
            )

            history = get_athlete_metrics_history(athlete_id=athlete.get("id"), days=7)
            weights = [m["weight_kg"] for m in history if m.get("weight_kg") is not None]
            trend_7d = (weights[-1] - weights[0]) if len(weights) >= 2 else 0.0
            energies = [m["energy_score"] for m in history if m.get("energy_score") is not None]
            avg_energy = (sum(energies) / len(energies)) if energies else 7.0

            wisdom = evaluate_wisdom_guidance(
                weight_trend_7d_kg=trend_7d,
                avg_energy=avg_energy,
                current_weight=logged_weight,
                target_weight=athlete.get("target_weight_kg")
            )

            await check_and_trigger_coach_alerts(
                athlete_profile=athlete,
                event_type="weight_log",
                data={"weight_kg": logged_weight, "history": history},
                bot_instance=context.bot
            )

            tw = athlete.get("target_weight_kg")
            target_str = f"{float(tw):.1f} kg" if tw else "Non définie (/start)"
            reply_text = (
                f"⚖️ <b>PESÉE ENREGISTRÉE : {logged_weight:.1f} kg</b>\n\n"
                f"🎯 <b>Cible Combat :</b> {target_str}\n"
                f"📈 <b>Tendance 7j :</b> {trend_7d:+.2f} kg | <b>Énergie moy :</b> {avg_energy:.1f}/10\n\n"
                f"💡 <b>{wisdom['rule']} :</b>\n<i>{wisdom['message']}</i>\n\n"
                "🔥 <b>Libertad & Performance.</b>"
            )
            await send_safe_html_message(update.message, reply_text)
            return

    # 2. Détection débriefing de fin de séance
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
        log_daily_metric(
            athlete_id=athlete.get("id"),
            telegram_id=user_id,
            rpe_real=rpe_val,
            notes=user_text
        )
        await check_and_trigger_coach_alerts(
            athlete_profile=athlete,
            event_type="debrief",
            data={"rpe_real": rpe_val},
            bot_instance=context.bot
        )
        context.user_data["awaiting_workout_feedback"] = False
        await send_safe_html_message(update.message, (
            "<b>🥋 DÉBRIEFING ENREGISTRÉ EN BDD !</b>\n\n"
            f"Bien reçu <b>{athlete_name}</b>. Séance validée avec un RPE réel de <b>{rpe_val}/10</b>.\n\n"
            f"💬 <i>{coach_msg}</i>\n\n"
            "💡 <i>Tes données ont été transmises au Head Coach. Place à la récupération !</i>\n\n"
            "🔥 <b>Libertad & Performance.</b>"
        ))
        return

    # 3. Détection suivi nutritionnel & méthode "Poids de combat"
    is_nutrition_keywords = any(k in lower_text for k in [
        "repas", "mangé", "mange", "déjeuner", "dejeuner", "dîner", "diner",
        "collation", "calories", "calorie", "kcal", "protéines", "proteines",
        "prot", "glucides", "lipides", "macros", "myfitnesspal", "mfp",
        "assiette", "balance", "pesée", "pesee", "poids de corps",
        "lutéale", "luteale", "faim", "craquage", "fringale"
    ])
    is_nutrition_only = athlete.get("tracking_type") == "nutrition"

    if is_nutrition_keywords or is_nutrition_only:
        status_msg = await update.message.reply_text(
            "🥗 <i>Analyse nutritionnelle en cours via l'IA Samourai...</i>",
            parse_mode="HTML"
        )
        try:
            nutri_reply = analyze_nutrition_entry(
                photo_bytes=None,
                text_content=user_text,
                athlete_profile=athlete
            )
            log_nutrition_entry(
                athlete_id=athlete.get("id"),
                telegram_id=user_id,
                meal_type="texte",
                analysis_text=nutri_reply,
                raw_user_input=user_text
            )
            await status_msg.delete()
            await send_safe_html_message(update.message, nutri_reply)
            return
        except Exception as e:
            logger.error(f"Erreur traitement nutrition texte: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ Erreur lors de l'analyse nutritionnelle : {e}")
            return

    # 3. Traitement Check-in d'entraînement standard
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


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestion des photos envoyées par l'athlète :
    - Mode A : OCR Visuel de l'assiette (règle des 3 zones : 1/2 légumes, 1/4 protéines, 1/4 glucides).
    - Mode B : Capture d'écran d'application tierce (MyFitnessPal, macros au gramme près).
    """
    if not update.message or not update.message.photo:
        return

    user = update.effective_user
    user_id = user.id
    athlete = get_athlete_profile(user_id)
    caption = update.message.caption or ""

    status_msg = await update.message.reply_text(
        "🔍 <i>Analyse de ton repas en cours via l'IA Samourai...</i>",
        parse_mode="HTML"
    )

    try:
        photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
        photo_bytes = await photo_file.download_as_bytearray()

        analysis_html = analyze_nutrition_entry(
            photo_bytes=bytes(photo_bytes),
            text_content=caption,
            athlete_profile=athlete,
            mime_type="image/jpeg"
        )

        meal_type = "assiette_ocr" if athlete.get("nutrition_mode") == "ocr_vision" else "macros_app"
        log_nutrition_entry(
            athlete_id=athlete.get("id"),
            telegram_id=user_id,
            meal_type=meal_type,
            analysis_text=analysis_html,
            raw_user_input=caption
        )

        await status_msg.delete()
        await send_safe_html_message(update.message, analysis_html)

    except Exception as e:
        logger.error(f"Erreur traitement photo nutrition : {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Erreur lors de l'analyse de la photo : {e}")


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

    # 1. Tunnel d'Onboarding Interactif (/start) — 5 étapes fluides
    onboarding_conv = ConversationHandler(
        entry_points=[CommandHandler("start", handle_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_name)],
            ASK_TRACKING_TYPE: [
                CallbackQueryHandler(handle_onboarding_tracking_type, pattern="^track_"),
                MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_tracking_type),
            ],
            ASK_NUTRITION_MODE: [
                CallbackQueryHandler(handle_onboarding_nutrition_mode, pattern="^nutri_"),
                MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_nutrition_mode),
            ],
            ASK_WEIGHT_AND_ACTIVITY: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_weight_activity),
            ],
            ASK_SERVICE_TIER: [
                CallbackQueryHandler(handle_onboarding_service_tier, pattern="^(tier_|segment_)"),
                MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_service_tier),
            ],
            ASK_GOAL: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_goal)],
            ASK_EQUIPMENT: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_equipment)],
            ASK_INJURIES: [MessageHandler(filters.TEXT & (~filters.COMMAND), handle_onboarding_injuries)],
        },
        fallbacks=[CommandHandler("cancel", handle_cancel_onboarding)],
        allow_reentry=True,
        per_message=False
    )
    application.add_handler(onboarding_conv)

    # 2. Commandes Coach, Athlète & Utilitaires
    application.add_handler(CommandHandler("hebdo", handle_hebdo_command))
    application.add_handler(CommandHandler("bilan", handle_bilan_command))
    application.add_handler(CommandHandler("tendance", handle_bilan_command))

    # 3. Callback Query : check-in smart puis bouton de fin de séance
    application.add_handler(CallbackQueryHandler(handle_checkin_callback, pattern="^checkin_(energy|equipment)_"))
    application.add_handler(CallbackQueryHandler(handle_finish_workout_callback, pattern="^finish_workout$"))

    # 4. Messages Photos (OCR assiette 3 zones & MyFitnessPal)
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))

    # 5. Messages Texte (Check-ins sportifs, Nutrition Poids de combat, Débriefings RPE)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_message))

    # 6. Messages Vocaux (Transcription Gemini puis traitement)
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    _global_telegram_application = application
    return application
