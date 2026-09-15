import pytest
from app.services.gemini import (
    calculate_target_macros,
    AMAZONIAN_SYSTEM_PROMPT,
    analyze_nutrition_entry
)
from app.services.supabase_service import (
    save_new_athlete_profile,
    get_athlete_profile,
    log_nutrition_entry
)
from app.services.telegram_bot import (
    ASK_NAME,
    ASK_TRACKING_TYPE,
    ASK_NUTRITION_MODE,
    ASK_WEIGHT_AND_ACTIVITY,
    ASK_SERVICE_TIER,
    ASK_GOAL,
    ASK_EQUIPMENT,
    ASK_INJURIES,
    create_telegram_application
)


def test_calculate_target_macros_75kg():
    """Vérifie les règles du Poids de combat : ~2g/kg protéines, min 0.8-1g/kg lipides."""
    macros = calculate_target_macros(weight_kg=75.0, activity_level="actif")
    
    assert macros["proteins"] == 150  # 75 * 2 = 150g
    assert macros["fats"] >= int(75 * 0.8)  # Minimum 60g de lipides
    assert macros["fats"] >= 50
    assert macros["calories"] >= 1500
    assert macros["carbs"] > 100


def test_calculate_target_macros_various_weights():
    """Teste différents poids et profils d'activité."""
    macros_60 = calculate_target_macros(weight_kg=60.0, activity_level="sedentaire")
    assert macros_60["proteins"] == 120
    assert macros_60["fats"] >= 50

    macros_90 = calculate_target_macros(weight_kg=90.0, activity_level="combattant MMA")
    assert macros_90["proteins"] == 180
    assert macros_90["fats"] >= 72
    assert macros_90["calories"] > macros_60["calories"]


def test_amazonian_system_prompt_contains_mandatory_rules():
    """Vérifie que le prompt système officiel contient tous les mots d'ordre et règles strictes."""
    prompt = AMAZONIAN_SYSTEM_PROMPT
    assert "Amazonian Samourai Performance Coach" in prompt
    assert "Jason Ponet" in prompt
    assert "PILIER 1 : LE SUIVI SPORTIF" in prompt
    assert "PILIER 2 : LE SUIVI NUTRITIONNEL" in prompt
    assert "Poids de combat" in prompt
    assert "règle des 3 zones" in prompt
    assert "Déficit calorique intelligent et maîtrisé" in prompt
    assert "Autour de 2 g par kilo" in prompt
    assert "0,8 à 1 g par kilo" in prompt
    assert "phase lutéale" in prompt
    assert "faim émotionnelle" in prompt


def test_onboarding_conversation_states():
    """Vérifie que les 8 états de la machine d'onboarding sont correctement ordonnés."""
    assert ASK_NAME == 0
    assert ASK_TRACKING_TYPE == 1
    assert ASK_NUTRITION_MODE == 2
    assert ASK_WEIGHT_AND_ACTIVITY == 3
    assert ASK_SERVICE_TIER == 4
    assert ASK_GOAL == 5
    assert ASK_EQUIPMENT == 6
    assert ASK_INJURIES == 7


def test_save_new_athlete_profile_with_nutrition_fields():
    """Vérifie que save_new_athlete_profile gère sans régression les nouveaux champs."""
    profile_data = save_new_athlete_profile(
        telegram_id=987654321,
        athlete_name="Marc Warrior",
        goal="Prépa combat MMA",
        default_equipment="Kettlebell 16kg + élastiques",
        injuries="Genou droit fragile",
        username="marc_warrior",
        tracking_type="both",
        nutrition_mode="ocr_vision",
        service_tier="hybride",
        weight_kg=78.5,
        activity_level="très actif",
        target_calories=2300,
        target_proteins=157,
        target_fats=71,
        target_carbs=250
    )

    assert profile_data["telegram_id"] == 987654321
    assert profile_data["first_name"] == "Marc"
    assert profile_data["last_name"] == "Warrior"
    assert profile_data["tracking_type"] == "both"
    assert profile_data["nutrition_mode"] == "ocr_vision"
    assert profile_data["service_tier"] == "hybride"
    assert profile_data["weight_kg"] == 78.5
    assert profile_data["target_calories"] == 2300
    assert profile_data["target_proteins"] == 157


def test_create_telegram_application_builds_successfully():
    """Vérifie que create_telegram_application s'initialise correctement avec les nouveaux handlers."""
    app = create_telegram_application()
    assert app is not None
    # Vérifie la présence des handlers principaux (onboarding, hebdo, checkin callbacks, photo, text, voice)
    handlers = app.handlers.get(0, [])
    assert len(handlers) >= 6
