from telegram import Update
from telegram.ext import ContextTypes
from app.services.gemini import analyze_checkin_with_gemini, generate_daily_workout
from app.services.supabase_service import get_available_exercises, supabase

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    telegram_id = str(update.message.from_user.id)
    
    # 1. Analyse du check-in par Gemini
    analysis = analyze_checkin_with_gemini(user_text)
    
    # 2. Récupération du profil de l'athlète dans Supabase
    athlete_response = supabase.table("athlete_profiles").select("*").eq("telegram_id", telegram_id).execute()
    athlete_profile = athlete_response.data[0] if athlete_response.data else {}
    
    # 3. Récupération des exercices éligibles dans Supabase
    equipment = analysis.equipment_available or athlete_profile.get("default_equipment", "Poids du corps")
    exercises = get_available_exercises(equipment)
    
    # 4. Génération de la séance personnalisée
    workout_plan = generate_daily_workout(analysis, exercises, athlete_profile)
    
    # 5. Envoi du retour coach + la séance complète
    response_message = f"{analysis.feedback_coach}\n\n---\n\n📋 **TA SÉANCE DU JOUR**\n\n{workout_plan}"
    await update.message.reply_text(response_message, parse_mode="Markdown")
