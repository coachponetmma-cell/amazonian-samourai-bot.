"""
Module de génération de rapports graphiques pour Amazonian Samourai Performance System.
Utilise Matplotlib en mode headless (Agg) avec un thème Dark Samourai.
"""

import io
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger(__name__)

# Thème Dark Samourai
COLOR_BG = "#121212"
COLOR_CARD = "#1A1A1A"
COLOR_RED = "#E63946"
COLOR_GOLD = "#FFB703"
COLOR_GREEN = "#2A9D8F"
COLOR_CYAN = "#48CAE4"
COLOR_TEXT = "#F1FAEE"
COLOR_MUTED = "#8D99AE"
COLOR_GRID = "#2B2D42"


def generate_weekly_report_chart(
    athlete_info: Dict[str, Any],
    metrics_history: Optional[List[Dict[str, Any]]] = None
) -> Optional[bytes]:
    """
    Génère un graphique PNG complet au format Dark Samourai comprenant :
    1. Évolution du poids réel vs Poids de combat cible
    2. Calories consommées vs Cible calorique quotidienne
    3. Dynamique Énergie & RPE de la semaine

    Retourne les octets PNG (commençant par b'\\x89PNG') ou None en cas d'erreur.
    """
    try:
        athlete_name = athlete_info.get("first_name") or "Combattant"
        segment = (athlete_info.get("segment") or "loisir").upper()
        current_weight = athlete_info.get("weight_kg")
        target_weight = athlete_info.get("target_weight_kg")
        target_calories = athlete_info.get("target_calories") or 2200

        # Normalisation des métriques
        today = datetime.now().date()
        dates_7d = [today - timedelta(days=i) for i in reversed(range(7))]

        # Mapping par date
        metrics_by_date = {}
        for m in (metrics_history or []):
            d_raw = m.get("log_date")
            if d_raw:
                if isinstance(d_raw, str):
                    try:
                        d_obj = datetime.strptime(d_raw[:10], "%Y-%m-%d").date()
                    except Exception:
                        d_obj = None
                else:
                    d_obj = d_raw
                if d_obj:
                    metrics_by_date[d_obj] = m

        # Construction des séries sur 7 jours
        weight_series = []
        calories_series = []
        energy_series = []
        rpe_series = []
        labels_dates = []

        last_known_weight = float(current_weight) if current_weight else 75.0

        for d in dates_7d:
            labels_dates.append(d.strftime("%d/%m"))
            entry = metrics_by_date.get(d)
            if entry:
                w = entry.get("weight_kg")
                if w is not None:
                    last_known_weight = float(w)
                weight_series.append(last_known_weight)

                c = entry.get("calories_consumed")
                calories_series.append(float(c) if c else 0.0)

                e = entry.get("energy_score")
                energy_series.append(float(e) if e is not None else 7.0)

                r = entry.get("rpe_real")
                rpe_series.append(float(r) if r is not None else 6.0)
            else:
                weight_series.append(last_known_weight)
                calories_series.append(0.0)
                energy_series.append(7.0)
                rpe_series.append(6.0)

        # Création de la figure Dark Samourai
        plt.style.use("dark_background")
        fig = plt.figure(figsize=(10, 8), dpi=150, facecolor=COLOR_BG)

        # Super-titre avec badge segment
        segment_badge = "[CELLULE ELITE]" if segment == "ELITE" else "[SUIVI LOISIR]"
        fig.suptitle(
            f"AMAZONIAN SAMOURAI — BILAN DE PERFORMANCE\nAthlète : {athlete_name} | {segment_badge}",
            fontsize=13,
            fontweight="bold",
            color=COLOR_TEXT,
            y=0.98
        )

        # Grille 3 subplots
        gs = fig.add_gridspec(2, 2, hspace=0.38, wspace=0.25, top=0.90, bottom=0.08, left=0.08, right=0.95)
        ax_weight = fig.add_subplot(gs[0, :])
        ax_cal = fig.add_subplot(gs[1, 0])
        ax_perf = fig.add_subplot(gs[1, 1])

        # Style commun pour les axes
        for ax in [ax_weight, ax_cal, ax_perf]:
            ax.set_facecolor(COLOR_CARD)
            ax.grid(True, linestyle="--", alpha=0.3, color=COLOR_GRID)
            for spine in ax.spines.values():
                spine.set_color(COLOR_GRID)
            ax.tick_params(colors=COLOR_MUTED, labelsize=9)

        # -------------------------------------------------------------
        # 1. Graphique Poids vs Cible
        # -------------------------------------------------------------
        ax_weight.plot(
            labels_dates,
            weight_series,
            color=COLOR_RED,
            marker="o",
            linewidth=2.5,
            markersize=6,
            label="Poids réel (kg)"
        )

        min_w = min(weight_series)
        ax_weight.fill_between(labels_dates, weight_series, min_w - 0.5, color=COLOR_RED, alpha=0.15)

        if target_weight:
            tw = float(target_weight)
            ax_weight.axhline(
                y=tw,
                color=COLOR_GOLD,
                linestyle="--",
                linewidth=1.8,
                label=f"Cible Combat : {tw:.1f} kg"
            )

        ax_weight.set_title("Tendance Poids de Combat (7 derniers jours)", color=COLOR_TEXT, fontsize=11, fontweight="bold", pad=8)
        ax_weight.set_ylabel("Poids (kg)", color=COLOR_MUTED, fontsize=9)
        ax_weight.legend(loc="upper right", framealpha=0.4, facecolor=COLOR_CARD, edgecolor=COLOR_GRID, labelcolor=COLOR_TEXT, fontsize=8)

        # Tendance calculée
        trend_kg = weight_series[-1] - weight_series[0]
        trend_str = f"Tendance 7j : {trend_kg:+.2f} kg"
        trend_color = COLOR_GREEN if trend_kg <= 0 else COLOR_GOLD
        ax_weight.text(
            0.02, 0.85,
            trend_str,
            transform=ax_weight.transAxes,
            fontsize=10,
            fontweight="bold",
            color=trend_color,
            bbox=dict(boxstyle="round,pad=0.3", facecolor=COLOR_BG, edgecolor=trend_color, alpha=0.8)
        )

        # -------------------------------------------------------------
        # 2. Graphique Calories vs Cible
        # -------------------------------------------------------------
        bar_colors = [
            COLOR_GREEN if (c > 0 and abs(c - target_calories) <= 250) else (COLOR_GOLD if c > 0 else COLOR_MUTED)
            for c in calories_series
        ]
        ax_cal.bar(labels_dates, calories_series, color=bar_colors, width=0.55, alpha=0.85, label="Calories")
        ax_cal.axhline(y=float(target_calories), color=COLOR_RED, linestyle="--", linewidth=1.5, label=f"Cible : {target_calories} kcal")
        ax_cal.set_title("Apports Caloriques vs Cible", color=COLOR_TEXT, fontsize=10, fontweight="bold", pad=8)
        ax_cal.set_ylabel("Kcal", color=COLOR_MUTED, fontsize=9)
        ax_cal.tick_params(axis="x", rotation=30)
        ax_cal.legend(loc="upper right", framealpha=0.4, facecolor=COLOR_CARD, edgecolor=COLOR_GRID, labelcolor=COLOR_TEXT, fontsize=7)

        # -------------------------------------------------------------
        # 3. Graphique Énergie & RPE
        # -------------------------------------------------------------
        x_indices = np.arange(len(labels_dates))
        width = 0.35
        ax_perf.bar(x_indices - width / 2, energy_series, width, color=COLOR_CYAN, alpha=0.8, label="Energie /10")
        ax_perf.bar(x_indices + width / 2, rpe_series, width, color=COLOR_RED, alpha=0.7, label="RPE Reel /10")
        ax_perf.set_xticks(x_indices)
        ax_perf.set_xticklabels(labels_dates, rotation=30)
        ax_perf.set_ylim(0, 10.5)
        ax_perf.axhline(y=6.0, color=COLOR_GREEN, linestyle=":", alpha=0.6, linewidth=1, label="Seuil Optimal (6)")
        ax_perf.set_title("Dynamique Energie vs RPE Reel", color=COLOR_TEXT, fontsize=10, fontweight="bold", pad=8)
        ax_perf.set_ylabel("Score /10", color=COLOR_MUTED, fontsize=9)
        ax_perf.legend(loc="upper right", framealpha=0.4, facecolor=COLOR_CARD, edgecolor=COLOR_GRID, labelcolor=COLOR_TEXT, fontsize=7)

        # Sauvegarde en mémoire
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Erreur lors de la génération du graphique de rapport hebdomadaire: {e}", exc_info=True)
        return None
