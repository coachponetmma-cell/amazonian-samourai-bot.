import html
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from app.core.config import settings
from app.services.gemini import analyze_checkin_with_gemini, generate_daily_workout
from app.services.supabase_service import get_available_exercises, supabase

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    telegram_id = str(update.message.from_user.id)
    
    # 1. Analyse du check-in par Gemini
    analysis = analyze_checkin_with_gemini(user_text)
    
    # 2. Récupération du profil
    athlete_profile = {}
    try:
        athlete_response = supabase.table("athlete_profiles").select("*").execute()
        if athlete_response.data:
            athlete_profile = athlete_response.data[0]
    except Exception:
        pass
    
    # 3. Récupération des exercices dans Supabase
    equipment = analysis.equipment_available or athlete_profile.get("default_equipment", "Poids du corps")
    exercises = get_available_exercises(equipment)
    
    # Fallback si la table exercises est vide
    if not exercises:
        exercises = [
            {"name": "Pompes MMA", "category": "Haut du corps", "equipment": "Bodyweight"},
            {"name": "Squats explosifs", "category": "Bas du corps", "equipment": "Bodyweight"},
            {"name": "Burpees Combat", "category": "Cardio", "equipment": "Bodyweight"},
            {"name": "Sprawls", "category": "Conditionnement", "equipment": "Bodyweight"}
        ]
    
    # 4. Génération de la séance
    workout_plan = generate_daily_workout(analysis, exercises, athlete_profile)
    
    # 5. Construction de la réponse formatée
    feedback_clean = html.escape(analysis.feedback_coach)
    workout_clean = html.escape(workout_plan)
    
    response_message = f"<b>{feedback_clean}</b>\n\n-------------------------\n\n📋 <b>TA SÉANCE DU JOUR</b>\n\n{workout_clean}"
    
    await update.message.reply_text(response_message, parse_mode="HTML")

def create_telegram_application():
    application = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_message))
    return application
