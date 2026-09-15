"""
Tests unitaires pour les modules de Sagesse IA (Confidence Engine & Règle 'Ne rien changer'),
Reporting Graphique Matplotlib Dark Samourai et Système d'Alertes Intelligentes.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from app.services.gemini import (
    AMAZONIAN_SYSTEM_PROMPT,
    evaluate_wisdom_guidance
)
from app.services.reporting import generate_weekly_report_chart
from app.services.supabase_service import (
    check_and_trigger_coach_alerts,
    save_new_athlete_profile,
    get_athlete_profile
)
from app.services.telegram_bot import create_telegram_application


# ==============================================================================
# 1. TESTS MOTEUR 12 SEMAINES & INTERDICTION STRICTE DE WATER CUT
# ==============================================================================

def test_amazonian_system_prompt_ban_on_water_cut():
    """
    Vérifie l'interdiction absolue de tout protocole de water cut,
    déshydratation, tapering ou reconstitution post-pesée dans le bot automatisé.
    """
    prompt = AMAZONIAN_SYSTEM_PROMPT
    assert "INTERDICTION STRICTE DE WATER CUT" in prompt
    assert "water cut" in prompt.lower()
    assert "Moteur de 12 semaines" in prompt or "12 semaines" in prompt
    assert "Cellule Élite" in prompt
    assert "RÈGLE D'OR DE SAGESSE IA : \"NE RIEN CHANGER\"" in prompt
    assert "CONFIDENCE ENGINE" in prompt


# ==============================================================================
# 2. TESTS MOTEUR DÉCISIONNEL & SAGESSE IA ("NE RIEN CHANGER")
# ==============================================================================

def test_evaluate_wisdom_guidance_healthy_loss():
    """Vérifie que la perte de gras progressive (-0.5 kg) avec bonne énergie valide le statu quo."""
    result = evaluate_wisdom_guidance(
        weight_trend_7d_kg=-0.5,
        avg_energy=7.5,
        current_weight=75.0,
        target_weight=70.0
    )
    assert result["action"] == "keep_course"
    assert result["decision"] == "VALIDER_SANS_MODIFICATION"
    assert "Ne rien changer" in result["rule"]
    assert "continue exactement ainsi" in result["message"].lower()


def test_evaluate_wisdom_guidance_stable_recomposition():
    """Vérifie que le maintien du poids avec bonne énergie conseille la patience."""
    result = evaluate_wisdom_guidance(
        weight_trend_7d_kg=0.1,
        avg_energy=6.5,
        current_weight=75.0,
        target_weight=70.0
    )
    assert result["action"] == "keep_course"
    assert result["decision"] == "VALIDER_SANS_MODIFICATION"
    assert "Recomposition" in result["rule"]


def test_evaluate_wisdom_guidance_abrupt_drop_warning():
    """Vérifie qu'une perte brutale (> 1.5kg sur 7j) déclenche un rappel de sécurité."""
    result = evaluate_wisdom_guidance(
        weight_trend_7d_kg=-1.8,
        avg_energy=5.0,
        current_weight=73.0,
        target_weight=70.0
    )
    assert result["action"] == "alert_drop"
    assert result["decision"] == "ALERTE_PERTE_TROP_RAPIDE"
    assert "Vigilance Perte Brutale" in result["rule"]


def test_evaluate_wisdom_guidance_temporary_retention():
    """Vérifie qu'une hausse rapide rappelle les fluctuations d'eau ou de cycle."""
    result = evaluate_wisdom_guidance(
        weight_trend_7d_kg=1.4,
        avg_energy=6.0,
        current_weight=76.4,
        target_weight=70.0
    )
    assert result["action"] == "check_fluid_or_intake"
    assert result["decision"] == "VERIFIER_RETENTION_OU_CYCLE"


# ==============================================================================
# 3. TESTS GÉNÉRATEUR DE RAPPORT GRAPHIQUE (MATPLOTLIB DARK SAMOURAI)
# ==============================================================================

def test_generate_weekly_report_chart_with_data():
    """Vérifie que generate_weekly_report_chart produit une image PNG valide."""
    athlete_info = {
        "first_name": "Atichat",
        "segment": "elite",
        "weight_kg": 74.5,
        "target_weight_kg": 70.0,
        "target_calories": 2200
    }
    metrics = [
        {"log_date": "2026-09-08", "weight_kg": 75.2, "calories_consumed": 2150, "energy_score": 8, "rpe_real": 7},
        {"log_date": "2026-09-09", "weight_kg": 75.0, "calories_consumed": 2200, "energy_score": 7, "rpe_real": 8},
        {"log_date": "2026-09-10", "weight_kg": 74.8, "calories_consumed": 2180, "energy_score": 8, "rpe_real": 7},
        {"log_date": "2026-09-11", "weight_kg": 74.7, "calories_consumed": 2250, "energy_score": 6, "rpe_real": 8},
        {"log_date": "2026-09-12", "weight_kg": 74.6, "calories_consumed": 2100, "energy_score": 7, "rpe_real": 6},
        {"log_date": "2026-09-13", "weight_kg": 74.5, "calories_consumed": 2200, "energy_score": 8, "rpe_real": 7},
        {"log_date": "2026-09-14", "weight_kg": 74.5, "calories_consumed": 2210, "energy_score": 8, "rpe_real": 7},
    ]

    png_bytes = generate_weekly_report_chart(athlete_info, metrics)
    assert png_bytes is not None
    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 10000
    # Signature PNG officielle : \x89PNG\r\n\x1a\n
    assert png_bytes[:4] == b"\x89PNG"


def test_generate_weekly_report_chart_empty_history_fallback():
    """Vérifie que generate_weekly_report_chart ne crashe jamais sur un historique vide."""
    athlete_info = {
        "first_name": "Nouveau Combattant",
        "segment": "loisir",
        "weight_kg": 80.0,
        "target_weight_kg": None,
        "target_calories": 2400
    }
    png_bytes = generate_weekly_report_chart(athlete_info, [])
    assert png_bytes is not None
    assert png_bytes[:4] == b"\x89PNG"


# ==============================================================================
# 4. TESTS ALERTES INTELLIGENTES HEAD COACH (COACH_TELEGRAM_ID)
# ==============================================================================

def test_coach_alert_on_critical_keyword():
    """Vérifie qu'un mot-clé critique déclenche l'envoi d'une alerte au coach."""
    async def _test():
        athlete = {
            "id": "ath-001",
            "first_name": "Tariq",
            "segment": "elite",
            "telegram_id": 12345
        }
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        alert_sent = await check_and_trigger_coach_alerts(
            athlete_profile=athlete,
            event_type="critical_keyword",
            data={"text": "Coach, grosse blessure au genou droit sur le sparring !"},
            bot_instance=mock_bot
        )

        assert alert_sent is True
        assert mock_bot.send_message.called
        call_args = mock_bot.send_message.call_args[1]
        assert "BLESSURE / DOULEUR AIGUË" in call_args["text"]

    asyncio.run(_test())


def test_coach_alert_on_weight_drift():
    """Vérifie qu'une dérive de poids > 1.5 kg en 48h déclenche une alerte."""
    async def _test():
        athlete = {
            "id": "ath-002",
            "first_name": "Karim",
            "segment": "loisir",
            "telegram_id": 67890
        }
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        history = [
            {"log_date": "2026-09-12", "weight_kg": 76.0},
            {"log_date": "2026-09-14", "weight_kg": 74.2}  # Perte de 1.8 kg en 48h
        ]

        alert_sent = await check_and_trigger_coach_alerts(
            athlete_profile=athlete,
            event_type="weight_log",
            data={"weight_kg": 74.2, "history": history},
            bot_instance=mock_bot
        )

        assert alert_sent is True
        assert mock_bot.send_message.called
        call_args = mock_bot.send_message.call_args[1]
        assert "DÉRIVE DE POIDS ANORMALE" in call_args["text"]

    asyncio.run(_test())


def test_coach_alert_on_chronic_fatigue_elite_only():
    """Vérifie que l'alerte fatigue chronique ne se déclenche que sur le segment Élite."""
    async def _test():
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        # 1. Athlète Loisir avec RPE élevé -> pas d'alerte fatigue coach (géré à 95% par l'IA)
        athlete_loisir = {
            "id": "ath-003",
            "first_name": "Sam",
            "segment": "loisir",
            "telegram_id": 11111
        }
        sent_loisir = await check_and_trigger_coach_alerts(
            athlete_profile=athlete_loisir,
            event_type="debrief",
            data={"rpe_real": 9},
            bot_instance=mock_bot
        )
        assert sent_loisir is False

        # 2. Athlète Élite avec RPE élevé répété (3x) -> alerte coach déclenchée
        athlete_elite = {
            "id": "ath-004",
            "first_name": "Marc Pro",
            "segment": "elite",
            "telegram_id": 22222
        }
        # 2 premiers RPE 9
        await check_and_trigger_coach_alerts(athlete_elite, "debrief", {"rpe_real": 9}, mock_bot)
        await check_and_trigger_coach_alerts(athlete_elite, "debrief", {"rpe_real": 10}, mock_bot)
        # 3ème RPE 9 consécutif
        sent_elite = await check_and_trigger_coach_alerts(athlete_elite, "debrief", {"rpe_real": 9}, mock_bot)
        assert sent_elite is True
        call_args = mock_bot.send_message.call_args[1]
        assert "FATIGUE CHRONIQUE" in call_args["text"]

    asyncio.run(_test())


# ==============================================================================
# 5. TESTS COMMANDES BOT ET SAUVEGARDE PROFIL ENRICHI
# ==============================================================================

def test_telegram_application_registers_bilan_command():
    """Vérifie que create_telegram_application enregistre bien la commande /bilan."""
    app = create_telegram_application()
    assert app is not None
    registered_handlers = app.handlers.get(0, [])
    
    # Recherche du handler /bilan
    command_names = []
    for h in registered_handlers:
        if hasattr(h, "commands"):
            command_names.extend(list(h.commands))

    assert "bilan" in command_names
    assert "tendance" in command_names
    assert "hebdo" in command_names


def test_save_new_athlete_profile_with_segment_and_target_weight():
    """Vérifie la sauvegarde du segment ('elite') et du target_weight_kg."""
    saved = save_new_athlete_profile(
        telegram_id=333333,
        athlete_name="Jason Test",
        goal="Prépa combat MMA cible 70kg",
        default_equipment="Salle complète",
        injuries="Aucune",
        username="jasontest",
        tracking_type="both",
        nutrition_mode="ocr_vision",
        service_tier="hybride",
        weight_kg=74.0,
        activity_level="pro",
        target_calories=2250,
        target_proteins=148,
        target_fats=67,
        target_carbs=260,
        segment="elite",
        target_weight_kg=70.0
    )

    assert saved["segment"] == "elite"
    assert saved["target_weight_kg"] == 70.0
    assert saved["service_tier"] == "hybride"
