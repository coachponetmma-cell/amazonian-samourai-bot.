# Coaching IA Base V2 - Master Architecture & Agent Specifications

## 1. Vue d'ensemble & Philosophie
**Coaching IA V2** est une infrastructure de coaching hybride (IA + Suivi Asynchrone / Visio) optimisée pour le sport de combat, la préparation physique et le lifestyle performance (MMA, Muay Thai, Musculation, Gestion du poids de combat).

- **Philosophie :** Approche scientifique, direct, warrior mindset, gestion asynchrone ("Libertad").
- **Objectif :** Automatiser 100% de la collecte de données, de l'analyse d'état de forme et de l'adaptation de séances, tout en fournissant des fiches de synthèse pré-visio pour les coachings premium.

---

## 2. Stack Technique & Intégrations

| Composant | Technologie | Rôle |
|---|---|---|
| **Backend** | Python 3.11+ (FastAPI) | API REST, gestion des Webhooks Telegram, calculs métier |
| **Base de Données** | Supabase (PostgreSQL) | Stockage relationnel, RLS, Auth, Dashboard d'administration |
| **Interface Athlète** | Bot Telegram (python-telegram-bot / aiogram) | Interactions fluides, saisie vocale, Inline Keyboards |
| **Moteur IA** | Google Gemini API (Flash / Pro) | Transcription & analyse vocale, génération de séances, synthèses |
| **Admin & Visio** | Google Workspace (Meet + Calendar API) | Planification des visios, prise de note automatique ("Take Notes with Gemini") |

---

## 3. Modèle Métier & Niveaux d'Accès (Tiers)

- **Tier 1 (29€/mois) - 100% Autonome & IA :**
  - Check-in quotidien (vocal ou texte).
  - Calcul du score de Readiness ($R$) et adaptation instantanée des séances.
  - Débriefings post-séance et historique des perfs.
- **Tier 2 (99€/mois) - Hybride Suivi :**
  - Inclus toutes les fonctionnalités du **Tier 1**.
  - **2 Visios Google Meet (30 min) / mois** avec le Head Coach.
  - Génération automatique de la **Fiche Pré-Visio IA** (points clés, anomalies de fatigue, progression).
- **Tier 3 (249€/mois) - Suivi Élite / Athlète Pro :**
  - Inclus toutes les fonctionnalités du **Tier 1**.
  - **1 Visio Google Meet (30 min) / semaine**.
  - DM prioritaires & alertes rouges directes au Head Coach en cas de blessure/surmenage.

---

## 4. Moteur de Sécurité & Règles (RULES Engine)

### 4.1 Formule de Readiness ($R$)
L'athlète renseigne lors de son check-in (échelle de 1 à 5) :
- **Sommeil** (1 = Catastrophique, 5 = Excellent)
- **Énergie** (1 = À plat, 5 = Explosif)
- **Fatigue** (1 = Aucune, 5 = Épuisement total) $\rightarrow$ Facteur inversé : $(6 - \text{Fatigue})$
- **Stress** (1 = Zen, 5 = Extrême) $\rightarrow$ Facteur inversé : $(6 - \text{Stress})$
- **Douleur / Courbature** (1 = Aucune, 5 = Douleur aiguë) $\rightarrow$ Facteur inversé : $(6 - \text{Douleur})$

$$\mathbf{R} = (0.25 \times \text{Sommeil}) + (0.25 \times \text{Énergie}) + (0.20 \times (6 - \text{Fatigue})) + (0.15 \times (6 - \text{Stress})) + (0.15 \times (6 - \text{Douleur}))$$

### 4.2 Niveaux d'Ajustement
- 🟢 **VERT ($R \ge 3.8$) :** Feu vert complet. Séance programmée à 100% d'intensité / charge cible.
- 🟡 **ORANGE ($2.5 \le R < 3.8$) :** Mode Maintien / Régulation. Réduction automatique du volume (-20% de séries/répétitions) et intensité modérée.
- 🔴 **ROUGE ($R < 2.5$ OU Douleur $\ge 4$) :** Mode Récupération / Alerte. Remplacement de la séance par mobilité/récupération active. Notification immédiate dans le rapport coach.

### 4.3 HARD Rules (Règles Inviolables)
1. **Équipement disponible :** Aucun exercice nécessitant du matériel non possédé par l'athlète ne doit être proposé.
2. **Blessures & Limitations :** Exclusion stricte des groupes musculaires ou mouvements blacklistés (ex: `NO_JUMP` si problème genou/cheville, `NO_OVERHEAD_PRESS` si instabilité épaule).
3. **Plafond RPE :** Si $R < 3.0$, interdiction de programmer des charges au-delà de RPE 7.

---

## 5. Architecture Multi-Agents

```
                 [ ATHLÈTE (Telegram Bot) ]
                             │
                  (Vocal / Texte / Boutons)
                             ▼
               ┌───────────────────────────┐
               │    FastAPI Orchestrator   │
               └─────────────┬─────────────┘
                             │
    ┌────────────────────────┼────────────────────────┐
    ▼                        ▼                        ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  Agent Check-In  │  │  Agent Workout   │  │ Agent Pré-Visio  │
│  & Readiness     │  │  Generator       │  │ & Reporting      │
└────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
         │                     │                     │
         │ (Gemini Flash)      │ (Rules + Gemini)    │ (Gemini Pro)
         ▼                     ▼                     ▼
┌──────────────────────────────────────────────────────────────┐
│                    SUPABASE (PostgreSQL)                     │
│  • users  • athlete_profiles  • checkins  • exercises  • logs│
└──────────────────────────────────────────────────────────────┘
```

### 5.1 Agent 1 : Check-in & Vocal Parser
- **Rôle :** Traite les messages vocaux (audio Telegram `.ogg`) et textes libres via Gemini Multimodal.
- **Sortie :** Données structurées JSON (Sommeil, Énergie, Fatigue, Stress, Douleurs, Localisation douleur, Notes).
- **Calcul :** Calcule le score $R$ et déclenche l'état (VERT/ORANGE/ROUGE).

### 5.2 Agent 2 : Workout Generator & Safety Adapter
- **Rôle :** Sélectionne les exercices dans la table `exercises` selon le profil, le matériel et le statut $R$.
- **Formatage :** Génère un message clair sur Telegram avec description, consignes RPE, tempo, et liens vidéo (YouTube Shorts / Vidéos démo).

### 5.3 Agent 3 : Pré-Visio & Debrief Analyst (Tier 2 & 3)
- **Rôle :** Avant chaque rendez-vous Google Meet, extrait les données des 14/30 derniers jours (tendances $R$, séances sautées, progression des charges, plaintes récurrentes).
- **Sortie :** Fiche synthétique d'1 page pour Jason Ponet ("Take Notes with Gemini" ready) pour attaquer la visio avec un maximum d'efficacité.

---

## 6. Schéma de Base de Données Relationnelle (Supabase)

```sql
-- 1. Athlètes & Profils
CREATE TABLE athletes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id BIGINT UNIQUE NOT NULL,
    first_name TEXT,
    last_name TEXT,
    tier INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE athlete_profiles (
    athlete_id UUID PRIMARY KEY REFERENCES athletes(id) ON DELETE CASCADE,
    height_cm NUMERIC,
    target_weight_kg NUMERIC,
    current_weight_kg NUMERIC,
    available_equipment TEXT[] DEFAULT '{}',
    injuries_and_constraints TEXT[] DEFAULT '{}',
    goal TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Exercices & Liens Vidéos
CREATE TABLE exercises (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    category TEXT NOT NULL, -- e.g. 'Strikers Conditioning', 'Explosivity', 'Hypertrophy', 'Mobility'
    primary_muscles TEXT[] NOT NULL,
    required_equipment TEXT[] DEFAULT '{}',
    contraindicated_for TEXT[] DEFAULT '{}', -- e.g. ['knee_pain', 'NO_JUMP']
    video_url TEXT, -- YouTube Shorts ou lien privé
    cues_and_instructions TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. Check-ins Quotidiens & Readiness
CREATE TABLE daily_checkins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    athlete_id UUID REFERENCES athletes(id) ON DELETE CASCADE,
    sleep_score INTEGER CHECK (sleep_score BETWEEN 1 AND 5),
    energy_score INTEGER CHECK (energy_score BETWEEN 1 AND 5),
    fatigue_score INTEGER CHECK (fatigue_score BETWEEN 1 AND 5),
    stress_score INTEGER CHECK (stress_score BETWEEN 1 AND 5),
    soreness_score INTEGER CHECK (soreness_score BETWEEN 1 AND 5),
    soreness_locations TEXT[],
    readiness_score NUMERIC(3, 2),
    readiness_status TEXT CHECK (readiness_status IN ('GREEN', 'ORANGE', 'RED')),
    raw_audio_url TEXT,
    ai_transcript TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 4. Séances Générées & Logs d'Exécution
CREATE TABLE workouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    athlete_id UUID REFERENCES athletes(id) ON DELETE CASCADE,
    checkin_id UUID REFERENCES daily_checkins(id),
    workout_plan JSONB NOT NULL,
    status TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'COMPLETED', 'SKIPPED', 'MODIFIED')),
    athlete_feedback TEXT,
    perceived_rpe INTEGER CHECK (perceived_rpe BETWEEN 1 AND 10),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 5. Rapports Pré-Visio
CREATE TABLE previsio_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    athlete_id UUID REFERENCES athletes(id) ON DELETE CASCADE,
    meeting_date TIMESTAMP WITH TIME ZONE,
    summary_markdown TEXT NOT NULL,
    flagged_alerts TEXT[],
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## 7. Directives de Développement & Prochaines Étapes

1. **Initialisation du projet :**
   - Mettre en place la structure du projet FastAPI (`app/api`, `app/core`, `app/services`, `app/models`).
   - Configurer les variables d'environnement (`.env.example` : Supabase URL/Key, Telegram Bot Token, Gemini API Key).
2. **Implémentation du Rules Engine :**
   - Module Python pur avec tests unitaires pour la formule $R$ et les filtres de sécurité.
3. **Intégration Supabase & Migration :**
   - Déploiement des scripts SQL pour la création des tables et un jeu de données initial d'exercices MMA / Conditioning.
4. **Intégration Telegram + Gemini :**
   - Webhook FastAPI et traitement multimodal des messages vocaux.
