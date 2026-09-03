"""
Script d'importation des exercices depuis un fichier Excel ou CSV (onglet 'EXERCICES')
vers la base de données Supabase.
"""
import sys
import os
import argparse
import logging
from typing import List

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ajout du dossier racine
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.core.supabase import get_supabase_client
from app.models.schemas import Exercise

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("exercise_importer")


def import_from_csv(file_path: str):
    """
    Importe les exercices depuis un fichier CSV.
    Format attendu des colonnes :
    Nom, Categorie, Muscles, Materiel, ContreIndications, VideoURL, Consignes
    """
    import csv

    if not os.path.exists(file_path):
        logger.error(f"Fichier introuvable : {file_path}")
        return

    client = get_supabase_client()
    if not client:
        logger.error("Supabase non configuré dans .env")
        return

    exercises_to_insert = []
    with open(file_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Nom") or row.get("name") or row.get("Exercice")
            if not name:
                continue

            category = row.get("Categorie") or row.get("category") or "Strikers Conditioning"
            muscles_raw = row.get("Muscles") or row.get("primary_muscles") or ""
            muscles = [m.strip() for m in muscles_raw.split(",") if m.strip()]

            equipment_raw = row.get("Materiel") or row.get("required_equipment") or "bodyweight"
            equipment = [eq.strip() for eq in equipment_raw.split(",") if eq.strip()]

            contra_raw = row.get("ContreIndications") or row.get("contraindicated_for") or ""
            contra = [c.strip() for c in contra_raw.split(",") if c.strip()]

            video_url = row.get("VideoURL") or row.get("video_url") or row.get("Lien") or row.get("YouTube") or ""
            cues = row.get("Consignes") or row.get("cues_and_instructions") or row.get("Description") or ""

            ex = Exercise(
                name=name.strip(),
                category=category.strip(),
                primary_muscles=muscles,
                required_equipment=equipment,
                contraindicated_for=contra,
                video_url=video_url.strip() if video_url else None,
                cues_and_instructions=cues.strip() if cues else None
            )
            exercises_to_insert.append(ex)

    logger.info(f"📊 {len(exercises_to_insert)} exercices lus depuis {file_path}.")

    # Insertion dans Supabase
    success = 0
    for ex in exercises_to_insert:
        try:
            data = ex.model_dump(exclude_none=True)
            client.table("exercises").upsert(data, on_conflict="name").execute()
            success += 1
        except Exception as e:
            logger.warning(f"Erreur insertion '{ex.name}': {e}")

    logger.info(f"✅ {success}/{len(exercises_to_insert)} exercices synchronisés dans Supabase !")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Importer les exercices vers Supabase")
    parser.add_argument("--file", type=str, default="exercices.csv", help="Chemin vers le fichier CSV ou Excel")
    args = parser.parse_args()

    import_from_csv(args.file)
