"""Create descriptive statistics, correlations, and blog-ready EDA figures."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


DATA_PATH = Path("data/qb_stats_combined.csv")
VALIDATION_PATH = Path("data/validation_report.json")
ANALYSIS_DIR = Path("analysis")
FIGURE_DIR = ANALYSIS_DIR / "figures"

ANALYSIS_VARIABLES = [
    "cmp_pct",
    "passing_yards",
    "yards_per_attempt",
    "passing_tds",
    "interceptions",
    "td_pct",
    "int_pct",
    "passing_yards_per_game",
    "attempts_per_game",
    "rtg",
    "win_pct",
]

LABELS = {
    "cmp_pct": "Completion percentage",
    "passing_yards": "Passing yards",
    "yards_per_attempt": "Yards per attempt",
    "passing_tds": "Passing touchdowns",
    "interceptions": "Interceptions",
    "td_pct": "Touchdown rate (%)",
    "int_pct": "Interception rate (%)",
    "passing_yards_per_game": "Passing yards per game",
    "attempts_per_game": "Attempts per game",
    "rtg": "NCAA passer rating",
    "win_pct": "Team winning percentage",
}


def scatterplot(
    data: pd.DataFrame,
    x: str,
    filename: str,
    title: str,
) -> None:
    figure, axis = plt.subplots(figsize=(8, 5.25))
    sns.regplot(
        data=data,
        x=x,
        y="win_pct",
        scatter_kws={"alpha": 0.58, "s": 38, "edgecolor": "none"},
        line_kws={"color": "#A33B20", "linewidth": 2},
        color="#1F5A8A",
        ax=axis,
    )
    correlation = data[[x, "win_pct"]].corr(method="pearson").iloc[0, 1]
    axis.set_title(title, loc="left", fontsize=14, fontweight="bold")
    axis.set_xlabel(LABELS[x])
    axis.set_ylabel("Team winning percentage")
    axis.set_ylim(-0.03, 1.03)
    axis.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
    axis.text(
        0.02,
        0.96,
        f"Pearson r = {correlation:.2f}\nn = {len(data)}",
        transform=axis.transAxes,
        ha="left",
        va="top",
        fontsize=10,
        color="#333333",
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.88, "pad": 3},
    )
    sns.despine(ax=axis)
    figure.tight_layout()
    figure.savefig(FIGURE_DIR / filename, dpi=200, bbox_inches="tight")
    plt.close(figure)


def correlation_heatmap(data: pd.DataFrame) -> None:
    correlation = data[ANALYSIS_VARIABLES].corr(method="pearson")
    renamed = correlation.rename(index=LABELS, columns=LABELS)
    figure, axis = plt.subplots(figsize=(11, 9))
    sns.heatmap(
        renamed,
        cmap="vlag",
        center=0,
        vmin=-1,
        vmax=1,
        square=True,
        linewidths=0.4,
        cbar_kws={"label": "Pearson correlation"},
        ax=axis,
    )
    axis.set_title(
        "Quarterback statistics and team winning percentage",
        loc="left",
        fontsize=14,
        fontweight="bold",
        pad=14,
    )
    axis.tick_params(axis="x", rotation=45)
    for label in axis.get_xticklabels():
        label.set_horizontalalignment("right")
    figure.tight_layout()
    figure.savefig(
        FIGURE_DIR / "correlation_heatmap.png", dpi=200, bbox_inches="tight"
    )
    plt.close(figure)


def main() -> None:
    if not DATA_PATH.exists() or not VALIDATION_PATH.exists():
        raise SystemExit(
            "Missing CFBD-generated data or validation metadata. Run "
            "`python collect_data.py` before analysis."
        )
    with VALIDATION_PATH.open(encoding="utf-8") as source:
        validation = json.load(source)
    if validation.get("source_domain") != "collegefootballdata.com":
        raise SystemExit(
            "The existing data was not generated from CollegeFootballData. "
            "Run `python collect_data.py` before analysis."
        )
    if validation.get("ties_included") != 0 or validation.get("sack_fields_included") != 0:
        raise SystemExit("Validation metadata reports ties or sack fields in the dataset.")
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", context="notebook")

    data = pd.read_csv(DATA_PATH, dtype={"qb_id": "string", "team_id": "string"})
    missing = data[ANALYSIS_VARIABLES].isna().sum()
    if missing.any():
        raise ValueError(f"Analysis variables contain missing values:\n{missing[missing > 0]}")

    summary = (
        data[ANALYSIS_VARIABLES]
        .agg(["count", "mean", "median", "std", "min", "max"])
        .transpose()
    )
    summary.index.name = "variable"
    summary.to_csv(ANALYSIS_DIR / "summary_statistics.csv", float_format="%.4f")

    pearson = data[ANALYSIS_VARIABLES].corr(method="pearson")["win_pct"].drop(
        "win_pct"
    )
    spearman = data[ANALYSIS_VARIABLES].corr(method="spearman")["win_pct"].drop(
        "win_pct"
    )
    correlations = pd.DataFrame(
        {"pearson_correlation": pearson, "spearman_correlation": spearman}
    ).sort_values("pearson_correlation", ascending=False)
    correlations.index.name = "variable"
    correlations.to_csv(
        ANALYSIS_DIR / "correlations_with_win_pct.csv", float_format="%.4f"
    )

    plots = [
        (
            "rtg",
            "passer_rating_vs_win_pct.png",
            "Passer rating and team winning percentage",
        ),
        (
            "cmp_pct",
            "completion_pct_vs_win_pct.png",
            "Completion percentage and team winning percentage",
        ),
        (
            "passing_yards",
            "passing_yards_vs_win_pct.png",
            "Passing yards and team winning percentage",
        ),
        (
            "yards_per_attempt",
            "yards_per_attempt_vs_win_pct.png",
            "Yards per attempt and team winning percentage",
        ),
        (
            "td_pct",
            "touchdown_rate_vs_win_pct.png",
            "Touchdown rate and team winning percentage",
        ),
        (
            "int_pct",
            "interception_rate_vs_win_pct.png",
            "Interception rate and team winning percentage",
        ),
        (
            "passing_yards_per_game",
            "passing_yards_per_game_vs_win_pct.png",
            "Passing yards per game and team winning percentage",
        ),
    ]
    for x, filename, title in plots:
        scatterplot(data, x, filename, title)
    correlation_heatmap(data)

    print("Summary statistics written to analysis/summary_statistics.csv")
    print("Correlations with team winning percentage:")
    print(correlations.round(3).to_string())
    print(f"Figures written to {FIGURE_DIR}")


if __name__ == "__main__":
    main()
