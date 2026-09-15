import logging
import re
import time
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types
from google.genai.errors import ServerError, APIError
from app.core.config import settings
from app.schemas.checkin import GeminiCheckinAnalysis

logger = logging.getLogger(__name__)

client = genai.Client(api_key=settings.GEMINI_API_KEY)


def clean_telegram_html(text: str) -> str:
    """
    Nettoie et convertit tout résidu de syntaxe Markdown en HTML valide pour Telegram.
    Garantit que le message ne plantera jamais lors de l'envoi avec parse_mode='HTML'.
    """
    if not text:
        return ""

    # Suppression des balises de bloc de code markdown (ex: ```html ... ```)
    cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\n?```$", "", cleaned, flags=re.MULTILINE)

    # Titres Markdown (###, ##, #) -> Balises <b>...</b>
    cleaned = re.sub(r"^(?:#{1,6})\s+(.+)$", r"<b>\1</b>", cleaned, flags=re.MULTILINE)

    # Gras Markdown (**texte**) -> <b>texte</b>
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", cleaned)

    # Liens Markdown [texte](url) -> <a href="url">texte</a>
    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', cleaned)

    return cleaned.strip()


def _call_gemini_with_retry(prompt: str, schema=None):
    """
    Exécute l'appel à Gemini avec retry et fallback automatique entre gemini-3.6-flash et gemini-2.5-flash.
    """
    models_to_try = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]

    for model in models_to_try:
        for attempt in range(3):
            try:
                config_args = {"temperature": 0.2}
                if schema:
                    config_args["response_mime_type"] = "application/json"
                    config_args["response_schema"] = schema

                config = types.GenerateContentConfig(**config_args)
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config
                )
                return response
            except (ServerError, APIError) as e:
                logger.warning(f"Erreur API Gemini sur {model} (essai {attempt + 1}/3): {e}")
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                else:
                    break
            except Exception as e:
                logger.error(f"Erreur inattendue Gemini sur {model}: {e}")
                break

    raise Exception("L'API Gemini est temporairement indisponible après plusieurs tentatives.")


def analyze_checkin_with_gemini(raw_text: str) -> GeminiCheckinAnalysis:
    """Analyse un check-in et indique explicitement les informations bloquantes."""
    prompt = f"""
Tu es le Head Coach du Samourai Performance System.
Analyse le message de l'athlète et réponds uniquement avec le schéma JSON fourni.

RÈGLES DE VALIDITÉ :
- is_valid_checkin vaut true uniquement si le message contient une indication exploitable
  du niveau d'énergie (échelle 1 à 10, ou qualificatif clairement convertible) ET du lieu
  ou matériel disponible aujourd'hui.
- Si l'énergie manque, missing_info vaut "energy".
- Sinon, si le lieu/matériel manque, missing_info vaut "location_equipment".
- Si les deux informations sont présentes, missing_info vaut null.
- Ne déduis pas le lieu ou le matériel à partir du profil habituel : il faut une indication
  dans le message du jour.
- energy_level doit être compris entre 1 et 10. Les qualificatifs "fatigué", "en forme"
  et "au top" correspondent respectivement à 3, 6 et 9.
- equipment doit décrire précisément le lieu et le matériel, ou rester null s'il est absent.
- notes regroupe sommeil, fatigue, douleurs, stress, RPE et toute contrainte utile.
- Conserve aussi les champs historiques quand l'information est disponible : energy_score,
  equipment_available, sleep_score, fatigue_score, stress_score, soreness_score, rpe et
  feedback_coach.

Message de l'athlète :
{raw_text}
"""
    response = _call_gemini_with_retry(prompt, schema=GeminiCheckinAnalysis)
    analysis = response.parsed

    # Certains modèles remplissent la structure imbriquée sans recopier les champs historiques.
    # Cette normalisation maintient la compatibilité avec la génération et Supabase.
    data = analysis.extracted_data
    if analysis.energy_score is None:
        analysis.energy_score = data.energy_level
    if analysis.equipment_available is None:
        analysis.equipment_available = data.equipment
    if analysis.energy_score is not None and data.energy_level is None:
        data.energy_level = analysis.energy_score
    if analysis.equipment_available and not data.equipment:
        data.equipment = analysis.equipment_available

    missing = []
    if analysis.energy_score is None:
        missing.append("energy")
    if not analysis.equipment_available:
        missing.append("location_equipment")
    analysis.missing_fields = missing
    if missing:
        analysis.is_valid_checkin = False
        analysis.missing_info = missing[0]
    else:
        analysis.is_valid_checkin = True
        analysis.missing_info = None
    return analysis


def generate_daily_workout(analysis: Any, exercises_list: list, athlete_profile: dict) -> str:
    """
    Génère la fiche de séance au format HTML Telegram VIP avec strict respect du matériel et des liens YouTube.
    """
    athlete_name = athlete_profile.get("first_name") or "Combattant"
    raw_equipment = getattr(analysis, "equipment_available", None) or athlete_profile.get("default_equipment") or "Poids du corps"
    energy_score = getattr(analysis, "energy_score", None) or 7

    # Préparation simplifiée de la liste d'exercices autorisés pour le prompt
    allowed_exercises_summary = []
    for ex in exercises_list:
        allowed_exercises_summary.append({
            "name": ex.get("name"),
            "material": ex.get("material") or ex.get("equipment") or "Aucun",
            "video_url": ex.get("video_url") or "",
            "instructions": ex.get("instructions") or ex.get("cues_and_instructions") or ""
        })

    prompt = f"""
    Tu es Jason Ponet (Amazonian Samourai), Head Coach international de MMA et Préparateur Physique.
    Tu génères la fiche de séance de préparation physique / MMA du jour pour ton athlète.

    CONTEXTE ATHLÈTE :
    - Athlète : {athlete_name}
    - Énergie du jour : {energy_score}/10
    - Environnement / Matériel déclaré : {raw_equipment}
    - Objectif : {athlete_profile.get('goal', 'MMA / Combat')}

    ══════════════════════════════════════════════════════════════
    RÈGLE N°1 : RESPECT ABSOLU DU MATÉRIEL & ENVIRONNEMENT (INVIOLABLE)
    ══════════════════════════════════════════════════════════════
    1. Si l'athlète est en "Chambre d'hôtel", "Poids du corps", ou sans matériel :
       INTERDICTION STRICTE ET ABSOLUE d'inclure des exercices nécessitant une barre, du landmine, une barre de traction, des haltères, une box, un banc ou des machines.
       Tous les mouvements doivent être réalisables à 100% au sol ou contre un mur sans matériel externe.
    2. Utilise UNIQUEMENT la liste d'exercices autorisés ci-dessous :
       {allowed_exercises_summary}

    ══════════════════════════════════════════════════════════════
    RÈGLE N°2 : FORMATAGE HTML VIP POUR TELEGRAM (AUCUN MARKDOWN)
    ══════════════════════════════════════════════════════════════
    1. N'utilise JAMAIS de Markdown (INTERDIT : '###', '##', '#', '**', '__', '[texte](url)').
    2. Utilise EXCLUSIVEMENT du HTML Telegram valide :
       <b>Texte en gras</b>, <i>Texte en italique</i>, et <a href="URL">Lien</a>.
    3. RÈGLE DES LIENS VIDÉOS (Exercices issus de la liste Supabase) :
       Pour chaque exercice tiré de la liste, affiche sous l'exercice :
       🔗 <a href="URL_VIDEO">🎬 Voir la démonstration</a>
    4. RÈGLE DU FALLBACK HYBRIDE (Exercice créé par l'IA si matériel hors BDD, ex: élastique, 1 KB spécifique) :
       Si tu ajoutes un exercice adapté pour du matériel non référencé dans la liste, écris UNIQUEMENT son nom en <b>Gras</b>, SANS insérer de lien vidéo et SANS inventer de fausse URL.

    ══════════════════════════════════════════════════════════════
    STRUCTURE EXIGÉE DU MESSAGE TELEGRAM (Design VIP Samourai Performance) :
    ══════════════════════════════════════════════════════════════

    <b>🥋 COACHING AMAZONIAN SAMOURAI</b>
    <b>Athlète :</b> {athlete_name} | <b>Statut :</b> {energy_score}/10 Énergie

    <b>📋 CONDITIONING COMBAT & ADAPTATION</b>
    🎯 <b>Focus :</b> Transfert MMA & Explosivité
    ⏱️ <b>Durée estimée :</b> ~40 min

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🔥 <b>BLOC 1 : ÉCHAUFFEMENT & MOBILITÉ DYNAMIQUE</b>

    1.1 - <b>Nom Exercice 1</b>
       📊 Volume : X séries × Y reps
       ⚡️ Intensité : RPE X/10 | Tempo : Fluide | Repos : Xs
       💡 Consigne : Consigne technique courte et précise
       🔗 <a href="URL">🎬 Voir la démonstration</a>

    1.2 - <b>Nom Exercice 2</b>
       📊 Volume : X séries × Y reps
       ⚡️ Intensité : RPE X/10 | Tempo : Contrôlé | Repos : Xs
       💡 Consigne : Consigne technique
       🔗 <a href="URL">🎬 Voir la démonstration</a>

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🔥 <b>BLOC 2 : CORPS DE SÉANCE & CONDITIONING</b>

    2.1 - <b>Nom Exercice 3</b>
       📊 Volume : X séries × Y reps
       ⚡️ Intensité : RPE X/10 | Repos : Xs
       💡 Consigne : Consigne technique
       🔗 <a href="URL">🎬 Voir la démonstration</a>

    2.2 - <b>Nom Exercice 4</b>
       📊 Volume : X séries × Y reps
       ⚡️ Intensité : RPE X/10 | Repos : Xs
       💡 Consigne : Consigne technique
       🔗 <a href="URL">🎬 Voir la démonstration</a>

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🔥 <b>BLOC 3 : FINISSEUR CONDITIONNEMENT MMA</b>

    3.1 - <b>Format Circuit / AMRAP / Intervallaire</b>
       📊 Consignes précises du circuit (exercices, tours, temps d'effort / repos)...
       💡 Objectif : Tenir le rythme de combat.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    📊 <b>CONSIGNES D'INTENSITÉ (RPE VISÉ)</b>
    • RPE cible global et conseil d'engagement mental.

    👊 Après la séance, envoie ton débriefing vocal ou texte !
    🔥 Libertad & Performance.
    """
    response = _call_gemini_with_retry(prompt)
    raw_output = response.text if response else ""
    return clean_telegram_html(raw_output)


def generate_weekly_coach_summary(logs: List[Dict[str, Any]], athlete_info: Dict[str, Any], checkins: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Génère le Bilan Hebdomadaire synthétique pour le Head Coach (Jason) afin de préparer son appel téléphonique.
    """
    athlete_name = athlete_info.get("first_name", "Combattant")
    athlete_goal = athlete_info.get("goal", "MMA / Performance")
    default_eq = athlete_info.get("default_equipment", "Poids du corps")
    injuries = athlete_info.get("injuries_history", "aucune")

    prompt = f"""
    Tu es l'Adjoint Analyste de Haute Performance du Head Coach Jason Ponet (Amazonian Samourai).
    Génère un Bilan Hebdomadaire ultra-synthétique, direct et opérationnel pour préparer l'appel téléphonique de suivi avec l'athlète {athlete_name}.

    DONNÉES DISPONIBLES (7 derniers jours) :
    - Athlète : {athlete_name} (Objectif : {athlete_goal} | Matériel habituel : {default_eq} | Antécédents/Blessures : {injuries})
    - Historique des séances (workout_logs) : {logs}
    - Check-ins quotidiens récents (checkins) : {checkins or []}

    EXIGENCES STRICTES DE FORMATAGE (TELEGRAM HTML) :
    - N'utilise JAMAIS de Markdown ('#', '##', '**', '__'). Utilise uniquement du HTML valide : <b>Gras</b>, <i>Italique</i>.
    - Ton : Direct, scientifique, axé combat, franc, sans superflu.

    STRUCTURE OBLIGATOIRE DU BILAN COACH :

    <b>📊 BILAN HEBDO COACH — SAMOURAI PERFORMANCE</b>
    <b>Athlète :</b> {athlete_name} | <b>Période :</b> 7 derniers jours

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    <b>1. 📈 ASSIDUITÉ & VOLUME</b>
    • Séances réalisées vs prescrites : analyse chiffrée.
    • Constat sur la régularité et l'engagement.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    <b>2. ⚡️ TENDANCES PHYSIOLOGIQUES</b>
    • Énergie moyenne constatée et dynamique sur la semaine.
    • Qualité du sommeil moyenne et capacité de récupération.
    • Niveau de fatigue accumulée.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    <b>3. 🎯 CHARGE & INTENSITÉ (RPE)</b>
    • RPE réel moyen rapporté vs intensité prescrite.
    • Respect des allures et sensation d'effort.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    <b>4. ⚠️ POINTS DE VIGILANCE & ALERTES</b>
    • Douleurs, raideurs ou zones à risque mentionnées.
    • Baisse d'énergie ou signal de surentraînement.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    <b>5. 🎯 3 AXES STRATÉGIQUES POUR L'APPEL COACH</b>
    1. [Axe 1 concret à aborder durant l'appel téléphonique]
    2. [Axe 2 concret]
    3. [Axe 3 concret]

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🥋 <i>Fiche prête pour ton call. Focus Libertad & Performance.</i>
    """
    response = _call_gemini_with_retry(prompt)
    raw_output = response.text if response else ""
    return clean_telegram_html(raw_output)


def transcribe_audio_with_gemini(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """
    Transcrit fidèlement un message audio Telegram (.ogg) en texte via Gemini.
    """
    models_to_try = ["gemini-3.6-flash", "gemini-2.5-flash"]
    prompt = (
        "Transcris fidèlement et mot à mot ce message audio envoyé par un athlète MMA à son coach. "
        "Ne résume pas, n'invente rien, retranscris simplement tout ce qui est dit en français."
    )
    for model in models_to_try:
        try:
            content_parts = [
                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                prompt
            ]
            response = client.models.generate_content(
                model=model,
                contents=content_parts
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            logger.warning(f"Erreur transcription audio sur {model}: {e}")
            continue
    return ""


def parse_debrief_with_gemini(raw_text: str) -> Dict[str, Any]:
    """
    Analyse un message de débriefing de fin de séance (texte ou vocal transcrit)
    pour extraire le RPE réel et formuler un retour guerrier du coach.
    """
    prompt = f"""
    Tu es Jason Ponet (Amazonian Samourai), Head Coach MMA.
    L'athlète vient d'envoyer son feedback de fin de séance :
    "{raw_text}"

    TÂCHES :
    1. Détermine le RPE réel ressenti (entier de 1 à 10). Si non mentionné explicitement, déduis-le des sensations (par défaut 7).
    2. Rédige un retour motivant et direct du coach Jason Ponet (court, martial, axé récupération : 'Libertad & Performance').

    Réponds EXCLUSIVEMENT sous la forme d'un objet JSON avec cette structure :
    {{
        "rpe_real": 8,
        "coach_reply": "Message d'encouragement du coach..."
    }}
    """
    try:
        import json
        config = types.GenerateContentConfig(temperature=0.2, response_mime_type="application/json")
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=config
        )
        if response and response.text:
            data = json.loads(response.text)
            return {
                "rpe_real": int(data.get("rpe_real", 7)),
                "coach_reply": str(data.get("coach_reply", "Séance validée guerrier ! Récupère bien."))
            }
    except Exception as e:
        logger.warning(f"Erreur analyse débriefing Gemini: {e}")

    # Fallback regex
    rpe_match = re.search(r"\b([1-9]|10)\b", raw_text)
    rpe_val = int(rpe_match.group(1)) if rpe_match else 7
    return {
        "rpe_real": rpe_val,
        "coach_reply": f"Bien reçu guerrier ! Séance validée à RPE {rpe_val}/10. Hydrate-toi et focus sur la récupération ! 🔥 Libertad & Performance."
    }


# ==============================================================================
# CERVEAU NUTRITIONNEL & SUIVI "POIDS DE COMBAT" (JASON PONET)
# ==============================================================================

AMAZONIAN_SYSTEM_PROMPT = """Tu es l'"Amazonian Samourai Performance Coach", l'intelligence artificielle officielle et exclusive du système d'entraînement et de nutrition fondé par Jason Ponet, athlète professionnel de MMA et coach avec 20 ans d'expérience.

=== TON IDENTITÉ ET TON TON ===
- Tu es direct, bienveillant, orienté performance, pragmatique et sans bullshit.
- Tu t'adresses à des combattants, des athlètes exigeants ou des sportifs déterminés.
- Signature constante : "🔥 Libertad & Performance".

=== RÈGLE DE SÉCURITÉ INVIOLABLE (INTERDICTION STRICTE DE WATER CUT) ===
- TU AS L'INTERDICTION TOTALE ET ABSOLUE de conseiller, suggérer ou détailler tout protocole de déshydratation aiguë ("water cut", sudation par sauna, bains chauds, combinaison de sudation, manipulation violente du sodium/eau), de jeûne extrême, de "tapering" pré-combat agressif ou de protocole de reconstitution post-pesée dans tes réponses automatisées.
- Ces protocoles extrêmes sont FORMELLEMENT RÉSERVÉS aux entretiens humains personnalisés de la Cellule Élite directement supervisés par le Head Coach Jason Ponet.
- Si un athlète t'interroge sur un water cut ou une coupe d'eau d'urgence, réponds fermement : "En tant qu'IA, je n'interviens jamais sur la déshydratation ou les coupes d'eau extrêmes : cela relève exclusivement d'un suivi médical et humain avec le Head Coach. Nous travaillons ici sur le Moteur de 12 semaines pour bâtir un vrai physique de combat durable."

=== PILIER 1 : LE SUIVI SPORTIF ===
- Tu conçois et ajustes des séances de cross-training, calisthenics, kettlebells, rowing, adaptées au matériel disponible et à l'environnement de l'athlète.
- Tu intègres la logique de gestion de la fatigue (Readiness, méthode French Contrast, adaptation du volume/intensité selon le système nerveux).
- Tu valides les séances terminées, récupères le RPE réel (de 1 à 10) et ajustes la charge.

=== PILIER 2 : LE SUIVI NUTRITIONNEL (Basé sur "Poids de combat") ===
- Moteur de 12 semaines (Engine A — Perte de gras progressive) : Toute la programmation repose sur une recomposition corporelle saine et progressive sur 12 semaines.
- Tu appliques la règle des 3 zones : 1/2 de légumes, 1/4 de protéines de qualité, 1/4 de glucides complexes autour de l'entraînement.
- Règles nutritionnelles absolues :
  * Déficit calorique intelligent et maîtrisé (-300 à -500 kcal max par jour) pour perdre du gras sans détruire la masse musculaire.
  * Autour de 2 g par kilo de poids de corps en protéines cibles pour blinder la masse musculaire.
  * 0,8 à 1 g par kilo de lipides de sécurité minimum (ne jamais descendre en dessous, vital pour le système hormonal).
  * Glucides modulés stratégiquement autour des séances.

=== RÈGLE D'OR DE SAGESSE IA : "NE RIEN CHANGER" ===
- La balance fluctue chaque jour (eau, glycogène, digestion, stress). NE RÉAGIS JAMAIS à une variation ponctuelle sur 24 ou 48h.
- Analyse toujours la TENDANCE SUR 7 JOURS.
- Si la tendance sur 7 jours est favorable (perte de gras progressive ou poids stable selon l'objectif) et que le niveau d'énergie de l'athlète est solide (>= 6/10), ta consigne absolue est : NE RIEN CHANGER. Pas de coupe de calories précipitée, pas d'augmentation de volume inutile. On laisse le moteur tourner.

=== CONFIDENCE ENGINE (ANTI-HALLUCINATION VISUELLE) ===
- Si une photo d'assiette ou de repas est floue, mal cadrée, trop sombre, ou si le contenu est ambigu (sauce opaque recouvrant le plat, bol mélangé indiscernable, portion impossible à estimer) :
  INTERDICTION FORMELLE D'INVENTER DES MACROS OU DES CALORIES PRÉCISES.
- Fais preuve d'humilité et de franchise : indique à l'athlète ce que tu distingues et demande-lui une clarification simple (ex: "Je vois bien les féculents, mais quelle est la protéine sous la sauce et la portion approximative ?") ou suggère-lui de reprendre une photo plus nette.

=== GESTION DES CAS PARTICULIERS ===
- Gestion de la phase lutéale : Si une athlète féminine observe une stagnation ou une prise temporaire sur la balance en phase lutéale, rappelle-lui calmement que la balance ment à cause des hormones et de la rétention d'eau, et qu'il ne faut pas paniquer ni couper les calories.
- Gestion de la faim émotionnelle liée au stress : En cas d'envie impulsive de manger liée au stress, aide l'athlète à dissocier le stress de la nourriture (conseille de boire un grand verre d'eau ou un thé, d'attendre 15-20 minutes, et de pratiquer une hygiène nerveuse).
- Douleurs / Signaux d'alerte : Face à une douleur aiguë, vertige ou malaise, arrêter immédiatement l'effort et orienter vers le Head Coach / avis médical.

Tu incarnes l'autorité, la rigueur et la fraternité martiale de Jason Ponet."""


def calculate_target_macros(weight_kg: float, activity_level: str = "modere") -> Dict[str, int]:
    """
    Calcule les macronutriments cibles selon la méthode 'Poids de combat' de Jason Ponet :
    - Protéines : ~2g/kg de poids de corps
    - Lipides : min 0.8 à 1g/kg (sécurisé à 0.9g/kg, minimum 50g)
    - Déficit calorique maîtrisé : -300 à -400 kcal
    - Glucides : carburant restant pour l'énergie d'entraînement
    """
    w = float(weight_kg)
    act = (activity_level or "").lower().strip()

    # Facteur multiplicateur calorique estimé
    if any(k in act for k in ["combattant", "athlete", "intense", "tres_actif", "très actif", "pro"]):
        factor = 37.0
    elif any(k in act for k in ["actif", "sportif", "elevé", "eleve"]):
        factor = 34.0
    elif any(k in act for k in ["sedentaire", "sédentaire", "faible", "bureau"]):
        factor = 29.0
    else:  # Modéré par défaut
        factor = 32.0

    tdee = w * factor
    # Déficit maîtrisé de -350 kcal pour perdre du gras sans détruire la masse musculaire
    target_calories = max(int(tdee - 350), 1500)

    # Protéines cibles : ~2g/kg
    target_proteins = round(w * 2.0)
    # Lipides de sécurité : min 0.8 à 1g/kg (jamais en-dessous)
    target_fats = max(round(w * 0.9), 50)
    # Glucides : solde calorique divisé par 4 kcal
    calories_from_prot_fat = (target_proteins * 4) + (target_fats * 9)
    remaining_calories = max(target_calories - calories_from_prot_fat, 400)
    target_carbs = round(remaining_calories / 4)

    return {
        "calories": target_calories,
        "proteins": target_proteins,
        "fats": target_fats,
        "carbs": target_carbs
    }


def analyze_nutrition_entry(
    photo_bytes: Optional[bytes] = None,
    text_content: Optional[str] = None,
    athlete_profile: Optional[Dict[str, Any]] = None,
    mime_type: str = "image/jpeg"
) -> str:
    """
    Analyse un repas, une assiette (OCR vision 3 zones) ou une capture d'application tierce (MyFitnessPal),
    ou une saisie textuelle de macros/ressentis selon les règles nutritionnelles de Jason Ponet.
    """
    prof = athlete_profile or {}
    athlete_name = prof.get("first_name") or "Guerrier"
    weight_kg = prof.get("weight_kg") or "Non spécifié"
    nutrition_mode = prof.get("nutrition_mode") or "ocr_vision"
    target_cals = prof.get("target_calories") or "Calculé selon poids"
    target_prots = prof.get("target_proteins") or "2g/kg"
    target_fats = prof.get("target_fats") or "0.8-1g/kg"
    target_carbs = prof.get("target_carbs") or "Carburant entraînement"

    mode_instructions = ""
    if nutrition_mode == "ocr_vision":
        mode_instructions = (
            "L'athlète a choisi le Mode A : Pratique / Visuel (OCR Assiette).\n"
            "Si une photo d'assiette est fournie, applique scrupuleusement la règle des 3 zones :\n"
            "- 1/2 de légumes (fibres, micronutriments, satiété)\n"
            "- 1/4 de protéines de qualité (~30-40g selon la cible)\n"
            "- 1/4 de glucides complexes (carburant autour du training)\n"
            "Donne un feedback visuel direct, clair et constructif, en indiquant si les proportions sont respectées et les ajustements recommandés."
        )
    else:
        mode_instructions = (
            "L'athlète a choisi le Mode B : Rigoureux / Application tierce (MyFitnessPal, etc.).\n"
            "Si une capture d'écran ou des macros textuelles sont fournies, extrait ou analyse les totaux (calories, protéines, lipides, glucides).\n"
            "Valide les totaux par rapport à ses cibles personnelles, rappelle la sécurité absolue des lipides (>= 0.8-1g/kg) et la cible protéique (~2g/kg)."
        )

    context_prompt = f"""
{AMAZONIAN_SYSTEM_PROMPT}

=== CONTEXTE ATHLÈTE ===
- Athlète : {athlete_name}
- Poids de corps : {weight_kg} kg
- Mode nutritionnel configuré : {nutrition_mode}
- Cibles personnelles : {target_cals} kcal | Protéines : {target_prots}g | Lipides : {target_fats}g | Glucides : {target_carbs}g

=== DIRECTIVE SPÉCIFIQUE DU MODE ===
{mode_instructions}

=== RÈGLE STRICTE DU CONFIDENCE ENGINE (ANTI-HALLUCINATION) ===
Si la photo fournie est floue, coupée, trop sombre, ou si le contenu de l'assiette/de la capture est impossible à identifier avec certitude (sauce opaque masquant l'aliment, préparation mélangée indiscernable) :
INTERDICTION FORMELLE d'inventer des chiffres, calories ou grammes fictifs !
Indique honnêtement ce qui est identifiable et pose une question de clarification directe et bienveillante pour demander :
1. Quels sont les ingrédients exacts présents ?
2. Quelle est la portion approximative ?
(ou invite l'athlète à renvoyer une photo plus nette avec un bon angle).

=== FORMATAGE DE SORTIE (TELEGRAM HTML STRICT) ===
- RÈGLE ABSOLUE : N'utilise AUCUN Markdown ('**', '##', '#', '__').
- Utilise EXCLUSIVEMENT du HTML Telegram valide : <b>Gras</b>, <i>Italique</i>, <code>code</code>.
- Structure ta réponse avec autorité et bienveillance :
  1. 🥗 <b>ANALYSE NUTRITIONNELLE — POIDS DE COMBAT</b>
  2. Diagnostic du plat ou des macros (points forts et ajustements immédiats).
  3. Conseil concret pour le prochain repas ou autour de l'entraînement.
  4. Signature : 🔥 <i>Libertad & Performance.</i>

Message / Légende de l'athlète :
"{text_content or 'Photo transmise pour analyse'}"
"""

    models_to_try = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
    for model in models_to_try:
        try:
            content_parts = []
            if photo_bytes:
                content_parts.append(types.Part.from_bytes(data=photo_bytes, mime_type=mime_type))
            content_parts.append(context_prompt)

            config = types.GenerateContentConfig(temperature=0.2)
            response = client.models.generate_content(
                model=model,
                contents=content_parts,
                config=config
            )
            if response and response.text:
                return clean_telegram_html(response.text)
        except Exception as e:
            logger.warning(f"Erreur analyse nutrition Gemini sur {model}: {e}")
            continue

    # Fallback si l'IA est temporairement indisponible
    return clean_telegram_html(
        f"<b>🥗 ANALYSE NUTRITIONNELLE REÇUE</b>\n\n"
        f"Bien reçu <b>{athlete_name}</b> ! Tes données de repas ont bien été transmises au système.\n\n"
        "💡 <i>Rappel Poids de combat : Vise 1/2 légumes, 1/4 protéines (~2g/kg/j) et 1/4 glucides complexes. Garde toujours un minimum de 0.8 à 1g de lipides par kilo.</i>\n\n"
        "🔥 <b>Libertad & Performance.</b>"
    )


def evaluate_wisdom_guidance(
    weight_trend_7d_kg: float,
    avg_energy: float,
    current_weight: Optional[float] = None,
    target_weight: Optional[float] = None
) -> Dict[str, Any]:
    """
    Applique le moteur de Sagesse IA et la règle du 'Ne rien changer'.
    Si la perte de gras est saine ou le poids stable avec une bonne énergie,
    on sanctuarise le plan sans ajustement impulsif.
    """
    if -1.2 <= weight_trend_7d_kg <= -0.1 and avg_energy >= 6.0:
        return {
            "action": "keep_course",
            "rule": "Règle d'or : Ne rien changer",
            "decision": "VALIDER_SANS_MODIFICATION",
            "message": (
                f"Tendance 7j idéale ({weight_trend_7d_kg:+.1f} kg) avec un niveau d'énergie solide ({avg_energy:.1f}/10). "
                "Le moteur de 12 semaines fonctionne à plein régime. Règle d'or du coach : On ne touche à rien, continue exactement ainsi !"
            )
        }
    elif -0.1 < weight_trend_7d_kg <= 0.3 and avg_energy >= 6.0:
        return {
            "action": "keep_course",
            "rule": "Règle de stabilité : Recomposition & Patience",
            "decision": "VALIDER_SANS_MODIFICATION",
            "message": (
                f"Poids stabilisé sur 7 jours ({weight_trend_7d_kg:+.1f} kg) avec une énergie excellente ({avg_energy:.1f}/10). "
                "Le corps se recompose en profondeur. Règle du coach : Pas d'ajustement intempestif, on maintient le cap !"
            )
        }
    elif weight_trend_7d_kg < -1.5:
        return {
            "action": "alert_drop",
            "rule": "Vigilance Perte Brutale",
            "decision": "ALERTE_PERTE_TROP_RAPIDE",
            "message": (
                f"Perte de poids trop rapide ({weight_trend_7d_kg:+.1f} kg sur 7j). "
                "Attention au risque de fonte musculaire ou de déshydratation. Maintiens bien tes apports caloriques et tes glucides de combat."
            )
        }
    elif weight_trend_7d_kg > 1.2:
        return {
            "action": "check_fluid_or_intake",
            "rule": "Analyse Fluctuations",
            "decision": "VERIFIER_RETENTION_OU_CYCLE",
            "message": (
                f"Hausse temporaire notée ({weight_trend_7d_kg:+.1f} kg). "
                "Rappelle-toi : la balance reflète l'eau, le sel et le glycogène (ou la phase lutéale). Pas de panique, reste strict sur tes 3 zones sans couper les calories."
            )
        }
    else:
        return {
            "action": "standard",
            "rule": "Suivi Régulier",
            "decision": "STANDARD",
            "message": "Continue d'enregistrer tes pesées et tes séances avec discipline. Libertad & Performance."
        }


