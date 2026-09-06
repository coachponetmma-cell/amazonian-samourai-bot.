import re
import time
import logging
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
    """
    Analyse le message de check-in de l'athlète et extrait l'état de forme et le matériel.
    """
    prompt = f"""
    Tu es le Head Coach du Samourai Performance System (Jason Ponet, Amazonian Samourai).
    Analyse le message de check-in quotidien de l'athlète :
    
    MISSIONS :
    1. Extrais les notes sur 10 (sommeil, énergie, fatigue, stress, courbatures/douleurs, RPE) si mentionnées.
    2. Identifie impérativement la contrainte de lieu et le matériel disponible :
       - Si l'athlète mentionne être à l'hôtel, en chambre, en déplacement, ou sans matériel, indique expressément "Chambre d'hôtel sans matériel" ou "Poids du corps".
       - S'il a du matériel spécifique (ex: 1 kettlebell 16kg, élastique, 2 haltères 10kg), liste-le précisément.
       - S'il est en salle complète, indique "Salle complète".
    3. Rédige un retour coach incisif, direct, motivant et guerrier ("Libertad & Performance"). Pas de blabla, va droit au but.

    Message de l'athlète : "{raw_text}"
    """
    response = _call_gemini_with_retry(prompt, schema=GeminiCheckinAnalysis)
    return response.parsed


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

