"""Collect regular-season FBS quarterback and team results from CFBD.

The CollegeFootballData API supplies season-specific FBS membership, game
results, and player box scores. This collector aggregates game-level passing
statistics so conference championships can be removed from both the passer
totals and team records before the primary quarterback is selected.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests


YEARS = (2023, 2024, 2025)
DATA_DIR = Path("data")
CACHE_DIR = Path(".cache/cfbd")
API_BASE_URL = "https://api.collegefootballdata.com"

FINAL_COLUMNS = [
    "year", "qb_id", "qb_name", "team_id", "team", "completions",
    "attempts", "cmp_pct", "passing_yards", "yards_per_attempt",
    "passing_tds", "interceptions", "td_pct", "int_pct",
    "passing_yards_per_game", "attempts_per_game", "rtg", "games_played",
    "wins", "losses", "win_pct",
]

EXCLUDED_GAME_COLUMNS = [
    "year", "game_id", "week", "home_team", "away_team", "reason", "notes"
]
TEAM_EXCLUSION_COLUMNS = ["year", "team_id", "team", "reason", "detail"]


class CollectionError(RuntimeError):
    """Raised when CFBD data cannot be collected or validated safely."""


class CfbdClient:
    """Disk-cached CFBD client with bearer authentication and bounded retries."""

    def __init__(
        self,
        api_key: str,
        cache_dir: Path = CACHE_DIR,
        timeout: float = 45.0,
        retries: int = 3,
        request_delay: float = 0.12,
        refresh: bool = False,
    ) -> None:
        if not api_key.strip():
            raise CollectionError(
                "CFBD_API_KEY is missing. Add it to the local .env file before "
                "running the collector."
            )
        self.api_key = api_key.strip()
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.retries = retries
        self.request_delay = request_delay
        self.refresh = refresh
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "BYU-STAT386-CFB-QB-data-project/2.0",
            }
        )

    def get_json(
        self,
        path: str,
        params: dict[str, Any],
        cache_group: str,
        cache_key: str,
    ) -> Any:
        cache_path = self.cache_dir / cache_group / f"{cache_key}.json"
        if cache_path.exists() and not self.refresh:
            with cache_path.open(encoding="utf-8") as source:
                return json.load(source)

        url = f"{API_BASE_URL}{path}"
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 401:
                    raise CollectionError(
                        "CFBD rejected the API key (HTTP 401). Check CFBD_API_KEY "
                        "in .env and try again."
                    )
                if response.status_code == 429:
                    raise CollectionError(
                        "CFBD rate limit reached (HTTP 429). Reuse cached responses "
                        "or wait until the account limit resets."
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, (list, dict)):
                    raise CollectionError(f"Unexpected JSON payload from {path}")
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache_path.with_suffix(".tmp")
                with temporary.open("w", encoding="utf-8") as target:
                    json.dump(payload, target)
                temporary.replace(cache_path)
                if self.request_delay:
                    time.sleep(self.request_delay)
                return payload
            except CollectionError:
                raise
            except (requests.RequestException, ValueError, OSError) as error:
                last_error = error
                if attempt + 1 < self.retries:
                    time.sleep((2**attempt) + random.random() * 0.25)

        raise CollectionError(
            f"Failed to retrieve {path} after {self.retries} attempts: {last_error}"
        )


@dataclass
class PassingTotals:
    qb_id: str
    qb_name: str
    completions: int = 0
    attempts: int = 0
    passing_yards: int = 0
    passing_tds: int = 0
    interceptions: int = 0
    game_ids: set[str] = field(default_factory=set)


@dataclass
class TeamSeason:
    team_id: str
    team_name: str
    wins: int = 0
    losses: int = 0
    game_ids: set[str] = field(default_factory=set)
    games_with_missing_boxscore: set[str] = field(default_factory=set)
    passers: dict[str, PassingTotals] = field(default_factory=dict)


def _string_id(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value: Any) -> int:
    text = str(value).strip().replace(",", "")
    if text in {"", "--", "-", "None"}:
        return 0
    return int(float(text))


def _normal_stat_name(value: Any) -> str:
    return re.sub(r"[^A-Z0-9/]", "", str(value).upper())


def _load_local_env(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE pairs without logging or exposing secret values."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as source:
        for raw_line in source:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _is_championship_game(game: dict[str, Any]) -> bool:
    """Identify championship games from CFBD's game notes."""
    notes = str(game.get("notes") or "")
    return bool(re.search(r"\bchampionship\b", notes, re.IGNORECASE))


def _excluded_game_reason(game: dict[str, Any]) -> str | None:
    if _is_championship_game(game):
        return "conference_championship"
    if game.get("playoff"):
        return "playoff"
    return None


def get_fbs_teams(client: CfbdClient, year: int) -> dict[str, str]:
    """Return CFBD team IDs and names from the historical FBS endpoint."""
    payload = client.get_json(
        "/teams/fbs", {"year": year}, "teams", f"fbs_{year}"
    )
    if not isinstance(payload, list):
        raise CollectionError(f"Expected a list from /teams/fbs for {year}")
    teams = {
        _string_id(team.get("id")): str(team.get("school") or "").strip()
        for team in payload
        if _string_id(team.get("id")) and str(team.get("school") or "").strip()
    }
    if not 120 <= len(teams) <= 150:
        raise CollectionError(
            f"Unexpected {year} FBS membership count: {len(teams)} teams"
        )
    return teams


def get_regular_season_games(
    client: CfbdClient, year: int, fbs_team_ids: set[str]
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """Return completed, decided regular-season games after exclusions."""
    payload = client.get_json(
        "/games",
        {"year": year, "seasonType": "regular", "classification": "fbs"},
        "games",
        f"{year}_regular_fbs",
    )
    if not isinstance(payload, list):
        raise CollectionError(f"Expected a list from /games for {year}")

    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for game in payload:
        if int(game.get("season", -1)) != year:
            continue
        if str(game.get("seasonType", "")).lower() != "regular":
            continue
        if not game.get("completed"):
            continue
        home_id = _string_id(game.get("homeId"))
        away_id = _string_id(game.get("awayId"))
        if {home_id, away_id}.isdisjoint(fbs_team_ids):
            continue
        if game.get("homePoints") is None or game.get("awayPoints") is None:
            continue

        reason = _excluded_game_reason(game)
        if reason:
            excluded.append(
                {
                    "year": year,
                    "game_id": _string_id(game.get("id")),
                    "week": _as_int(game.get("week")),
                    "home_team": str(game.get("homeTeam") or ""),
                    "away_team": str(game.get("awayTeam") or ""),
                    "reason": reason,
                    "notes": str(game.get("notes") or ""),
                }
            )
            continue
        if _as_int(game.get("homePoints")) == _as_int(game.get("awayPoints")):
            raise CollectionError(
                f"Tie found in completed game {game.get('id')}. The project "
                "requires decided games only."
            )
        included.append(game)

    if len(included) < 700:
        raise CollectionError(f"Only found {len(included)} eligible games for {year}")
    return included, pd.DataFrame(excluded, columns=EXCLUDED_GAME_COLUMNS)


def get_player_box_scores(
    client: CfbdClient, year: int, weeks: Iterable[int]
) -> list[dict[str, Any]]:
    """Get passing box scores one week at a time, as required by CFBD."""
    games: dict[str, dict[str, Any]] = {}
    for week in sorted(set(weeks)):
        payload = client.get_json(
            "/games/players",
            {
                "year": year,
                "week": week,
                "seasonType": "regular",
                "classification": "fbs",
                "category": "passing",
            },
            "player_box_scores",
            f"{year}_regular_week_{week}_passing",
        )
        if not isinstance(payload, list):
            raise CollectionError(
                f"Expected a list from /games/players for {year} week {week}"
            )
        for game in payload:
            game_id = _string_id(game.get("id"))
            if game_id:
                games[game_id] = game
        print(f"  collected passing box scores for week {week}")
    return list(games.values())


def _passing_category(team_box: dict[str, Any]) -> dict[str, Any] | None:
    for category in team_box.get("categories") or []:
        if str(category.get("name") or "").lower() == "passing":
            return category
    return None


def _parse_passing_category(
    category: dict[str, Any]
) -> list[tuple[str, str, int, int, int, int, int]]:
    """Convert a CFBD passing category into one row per listed passer.

    Sacks and every other non-requested box-score field are intentionally
    ignored, even if CFBD includes them in the passing category.
    """
    athletes: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"name": "", "stats": {}}
    )
    for stat_type in category.get("types") or []:
        stat_name = _normal_stat_name(stat_type.get("name"))
        for athlete in stat_type.get("athletes") or []:
            athlete_id = _string_id(athlete.get("id"))
            if not athlete_id:
                continue
            athletes[athlete_id]["name"] = str(athlete.get("name") or "").strip()
            athletes[athlete_id]["stats"][stat_name] = athlete.get("stat")

    rows: list[tuple[str, str, int, int, int, int, int]] = []
    for athlete_id, athlete in athletes.items():
        stats = athlete["stats"]
        completions = attempts = None
        for key in ("C/ATT", "COMP/ATT", "COMPLETIONS/ATTEMPTS"):
            if key in stats:
                pieces = str(stats[key]).split("/")
                if len(pieces) != 2:
                    raise CollectionError(f"Unexpected {key} value: {stats[key]}")
                completions, attempts = _as_int(pieces[0]), _as_int(pieces[1])
                break
        if completions is None:
            completions = _as_int(
                next(
                    (stats[key] for key in ("COMPLETIONS", "COMP", "CMP") if key in stats),
                    0,
                )
            )
            attempts = _as_int(
                next((stats[key] for key in ("ATTEMPTS", "ATT") if key in stats), 0)
            )

        yards = _as_int(
            next(
                (stats[key] for key in ("YDS", "YARDS", "PASSYDS") if key in stats),
                0,
            )
        )
        touchdowns = _as_int(
            next(
                (stats[key] for key in ("TD", "TDS", "PASSTD") if key in stats),
                0,
            )
        )
        interceptions = _as_int(
            next(
                (stats[key] for key in ("INT", "INTERCEPTIONS") if key in stats),
                0,
            )
        )
        if attempts > 0 or completions > 0 or yards != 0:
            rows.append(
                (athlete_id, athlete["name"], completions, attempts, yards,
                 touchdowns, interceptions)
            )
    return rows


def _team_box_by_id(
    game: dict[str, Any], box_score: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Map CFBD team box scores to team IDs using the matching game record."""
    by_name = {
        str(game.get("homeTeam") or ""): _string_id(game.get("homeId")),
        str(game.get("awayTeam") or ""): _string_id(game.get("awayId")),
    }
    by_side = {
        "home": _string_id(game.get("homeId")),
        "away": _string_id(game.get("awayId")),
    }
    result: dict[str, dict[str, Any]] = {}
    for team_box in box_score.get("teams") or []:
        team_id = by_name.get(str(team_box.get("team") or ""))
        if not team_id:
            team_id = by_side.get(str(team_box.get("homeAway") or "").lower())
        if team_id:
            result[team_id] = team_box
    return result


def aggregate_year(
    year: int,
    fbs_teams: dict[str, str],
    games: Iterable[dict[str, Any]],
    box_scores: Iterable[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    team_seasons = {
        team_id: TeamSeason(team_id=team_id, team_name=team_name)
        for team_id, team_name in fbs_teams.items()
    }
    games_by_id = {_string_id(game.get("id")): game for game in games}
    boxes_by_id = {_string_id(box.get("id")): box for box in box_scores}

    for game_id, game in games_by_id.items():
        home_id = _string_id(game.get("homeId"))
        away_id = _string_id(game.get("awayId"))
        home_points = _as_int(game.get("homePoints"))
        away_points = _as_int(game.get("awayPoints"))
        if home_points == away_points:
            raise CollectionError(f"Tie found in included game {game_id}")
        winner_id = home_id if home_points > away_points else away_id
        fbs_participants = {home_id, away_id}.intersection(team_seasons)
        for team_id in fbs_participants:
            team = team_seasons[team_id]
            team.game_ids.add(game_id)
            if team_id == winner_id:
                team.wins += 1
            else:
                team.losses += 1

        box_score = boxes_by_id.get(game_id)
        if box_score is None:
            for team_id in fbs_participants:
                team_seasons[team_id].games_with_missing_boxscore.add(game_id)
            continue
        team_boxes = _team_box_by_id(game, box_score)
        for team_id in fbs_participants:
            team_box = team_boxes.get(team_id)
            category = _passing_category(team_box or {})
            if category is None:
                team_seasons[team_id].games_with_missing_boxscore.add(game_id)
                continue
            passing_rows = _parse_passing_category(category)
            if not passing_rows:
                team_seasons[team_id].games_with_missing_boxscore.add(game_id)
                continue
            for (qb_id, qb_name, completions, attempts, yards,
                 touchdowns, interceptions) in passing_rows:
                totals = team_seasons[team_id].passers.setdefault(
                    qb_id, PassingTotals(qb_id=qb_id, qb_name=qb_name)
                )
                totals.qb_name = qb_name or totals.qb_name
                totals.completions += completions
                totals.attempts += attempts
                totals.passing_yards += yards
                totals.passing_tds += touchdowns
                totals.interceptions += interceptions
                totals.game_ids.add(game_id)

    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    ordered_teams = sorted(team_seasons.values(), key=lambda item: item.team_name)
    for team_season in ordered_teams:
        common = {
            "year": year,
            "team_id": team_season.team_id,
            "team": team_season.team_name,
        }
        if team_season.games_with_missing_boxscore:
            exclusions.append(
                {
                    **common,
                    "reason": "missing_or_incomplete_boxscore",
                    "detail": ";".join(sorted(team_season.games_with_missing_boxscore)),
                }
            )
            continue
        if not team_season.passers:
            exclusions.append({**common, "reason": "no_passing_statistics", "detail": ""})
            continue

        leader = max(
            team_season.passers.values(),
            key=lambda passer: (passer.attempts, passer.completions, passer.qb_id),
        )
        games_played = len(leader.game_ids)
        if games_played < 10:
            exclusions.append(
                {
                    **common,
                    "reason": "primary_qb_fewer_than_10_games",
                    "detail": f"{leader.qb_name}: {games_played} games",
                }
            )
            continue
        if leader.attempts <= 0:
            exclusions.append(
                {**common, "reason": "primary_qb_zero_attempts", "detail": leader.qb_name}
            )
            continue

        decisions = team_season.wins + team_season.losses
        if decisions != len(team_season.game_ids):
            raise CollectionError(
                f"{year} {team_season.team_name}: decisions do not match included games"
            )
        attempts = leader.attempts
        rating = (
            8.4 * leader.passing_yards
            + 330 * leader.passing_tds
            + 100 * leader.completions
            - 200 * leader.interceptions
        ) / attempts
        rows.append(
            {
                **common,
                "qb_id": leader.qb_id,
                "qb_name": leader.qb_name,
                "completions": leader.completions,
                "attempts": attempts,
                "cmp_pct": 100 * leader.completions / attempts,
                "passing_yards": leader.passing_yards,
                "yards_per_attempt": leader.passing_yards / attempts,
                "passing_tds": leader.passing_tds,
                "interceptions": leader.interceptions,
                "td_pct": 100 * leader.passing_tds / attempts,
                "int_pct": 100 * leader.interceptions / attempts,
                "passing_yards_per_game": leader.passing_yards / games_played,
                "attempts_per_game": attempts / games_played,
                "rtg": rating,
                "games_played": games_played,
                "wins": team_season.wins,
                "losses": team_season.losses,
                "win_pct": team_season.wins / decisions,
            }
        )

    return (
        pd.DataFrame(rows).reindex(columns=FINAL_COLUMNS),
        pd.DataFrame(exclusions, columns=TEAM_EXCLUSION_COLUMNS),
    )


def validate_dataset(
    frame: pd.DataFrame, fbs_counts: dict[int, int], years: Iterable[int]
) -> dict[str, Any]:
    years = tuple(sorted(set(years)))
    errors: list[str] = []
    if frame.empty:
        errors.append("Dataset is empty")
    if list(frame.columns) != FINAL_COLUMNS:
        errors.append("Column order does not match the required schema")
    if any("sack" in column.lower() for column in frame.columns):
        errors.append("A sack field is present even though sacks are excluded")
    if frame.duplicated(["team_id", "year"]).any():
        errors.append("Duplicate team_id/year combinations exist")
    if frame.duplicated(["qb_id", "team_id", "year"]).any():
        errors.append("Duplicate qb_id/team_id/year combinations exist")
    if not (frame["games_played"] >= 10).all():
        errors.append("At least one QB has fewer than 10 games played")
    if not frame["year"].isin(years).all():
        errors.append("Dataset contains a year outside the requested seasons")
    if frame[["qb_id", "team_id"]].isna().any().any():
        errors.append("Missing QB or team identifiers")
    blank_ids = frame[["qb_id", "team_id"]].astype(str).apply(lambda col: col.str.len())
    if (blank_ids == 0).any().any():
        errors.append("Blank QB or team identifiers")
    if not ((frame["wins"] + frame["losses"]) > 0).all():
        errors.append("At least one row has no decided games")

    checks = {
        "Winning percentage": frame["wins"] / (frame["wins"] + frame["losses"]),
        "Completion percentage": 100 * frame["completions"] / frame["attempts"],
        "Yards per attempt": frame["passing_yards"] / frame["attempts"],
        "Touchdown rate": 100 * frame["passing_tds"] / frame["attempts"],
        "Interception rate": 100 * frame["interceptions"] / frame["attempts"],
        "Passing yards per game": frame["passing_yards"] / frame["games_played"],
        "Attempts per game": frame["attempts"] / frame["games_played"],
        "NCAA passer rating": (
            8.4 * frame["passing_yards"]
            + 330 * frame["passing_tds"]
            + 100 * frame["completions"]
            - 200 * frame["interceptions"]
        ) / frame["attempts"],
    }
    actual_columns = {
        "Winning percentage": "win_pct",
        "Completion percentage": "cmp_pct",
        "Yards per attempt": "yards_per_attempt",
        "Touchdown rate": "td_pct",
        "Interception rate": "int_pct",
        "Passing yards per game": "passing_yards_per_game",
        "Attempts per game": "attempts_per_game",
        "NCAA passer rating": "rtg",
    }
    for label, expected in checks.items():
        if not (expected - frame[actual_columns[label]]).abs().lt(1e-10).all():
            errors.append(f"{label} is inconsistent")

    numeric_values = frame.select_dtypes(include="number")
    if not numeric_values.map(math.isfinite).all().all():
        errors.append("Non-finite numeric values exist")
    if (frame["completions"] > frame["attempts"]).any():
        errors.append("Completions exceed attempts")
    if not frame["win_pct"].between(0, 1).all():
        errors.append("Winning percentage is outside 0-1")
    if not frame["cmp_pct"].between(0, 100).all():
        errors.append("Completion percentage is outside 0-100")

    full_assignment = set(years) == set(YEARS)
    meets_200 = len(frame) >= 200
    if full_assignment and not meets_200:
        errors.append("Dataset has fewer than 200 observations")
    report = {
        "data_source": "CollegeFootballData API",
        "source_domain": "collegefootballdata.com",
        "api_base_url": API_BASE_URL,
        "seasons": list(years),
        "observations_by_year": {
            str(year): int((frame["year"] == year).sum()) for year in years
        },
        "fbs_teams_by_year": {str(year): count for year, count in fbs_counts.items()},
        "total_observations": int(len(frame)),
        "unique_quarterbacks": int(frame["qb_id"].nunique()),
        "unique_teams": int(frame["team_id"].nunique()),
        "missing_values_by_column": {
            column: int(count) for column, count in frame.isna().sum().items()
        },
        "meets_200_observation_requirement": meets_200,
        "ties_included": 0,
        "sack_fields_included": 0,
        "errors": errors,
    }
    if errors:
        raise CollectionError("Validation failed: " + "; ".join(errors))
    return report


def _write_outputs(
    yearly_frames: dict[int, pd.DataFrame],
    exclusions: pd.DataFrame,
    excluded_games: pd.DataFrame,
    fbs_teams: dict[int, dict[str, str]],
) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for year, frame in yearly_frames.items():
        frame.to_csv(DATA_DIR / f"qb_stats_{year}.csv", index=False, float_format="%.6f")
    combined = pd.concat(yearly_frames.values(), ignore_index=True)
    combined = combined.sort_values(["year", "team"], ignore_index=True)
    combined.to_csv(DATA_DIR / "qb_stats_combined.csv", index=False, float_format="%.6f")
    exclusions.to_csv(DATA_DIR / "exclusions.csv", index=False)
    excluded_games.to_csv(DATA_DIR / "excluded_games.csv", index=False)

    membership_rows = [
        {"year": year, "team_id": team_id, "team": team_name}
        for year, teams in fbs_teams.items()
        for team_id, team_name in teams.items()
    ]
    pd.DataFrame(membership_rows).sort_values(["year", "team"]).to_csv(
        DATA_DIR / "fbs_teams_by_year.csv", index=False
    )
    return combined


def _reason_counts(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty:
        return {}
    return {
        str(reason): int(count)
        for reason, count in frame["reason"].value_counts().items()
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years", nargs="+", type=int, default=list(YEARS), choices=YEARS,
        help="Seasons to collect (default: 2023 2024 2025)",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Ignore cached CFBD JSON and request fresh responses",
    )
    return parser.parse_args()


def main() -> None:
    _load_local_env()
    args = parse_args()
    years = tuple(sorted(set(args.years)))
    client = CfbdClient(os.getenv("CFBD_API_KEY", ""), refresh=args.refresh)
    yearly_frames: dict[int, pd.DataFrame] = {}
    yearly_exclusions: list[pd.DataFrame] = []
    yearly_excluded_games: list[pd.DataFrame] = []
    all_fbs_teams: dict[int, dict[str, str]] = {}

    for year in years:
        print(f"Collecting {year} from CollegeFootballData")
        fbs_teams = get_fbs_teams(client, year)
        all_fbs_teams[year] = fbs_teams
        print(f"  identified {len(fbs_teams)} season-specific FBS teams")
        games, excluded_games = get_regular_season_games(client, year, set(fbs_teams))
        yearly_excluded_games.append(excluded_games)
        print(f"  retained {len(games)} completed non-championship games")
        box_scores = get_player_box_scores(
            client, year, (_as_int(game.get("week")) for game in games)
        )
        frame, exclusions = aggregate_year(year, fbs_teams, games, box_scores)
        yearly_frames[year] = frame
        yearly_exclusions.append(exclusions)
        print(f"  retained {len(frame)} QB team-seasons; excluded {len(exclusions)}")

    exclusions = pd.concat(yearly_exclusions, ignore_index=True)
    excluded_games = pd.concat(yearly_excluded_games, ignore_index=True)
    combined = pd.concat(yearly_frames.values(), ignore_index=True).sort_values(
        ["year", "team"], ignore_index=True
    )
    report = validate_dataset(
        combined, {year: len(teams) for year, teams in all_fbs_teams.items()}, years
    )
    report["excluded_team_seasons"] = int(len(exclusions))
    report["excluded_games"] = int(len(excluded_games))
    report["exclusions_by_reason"] = _reason_counts(exclusions)
    report["excluded_games_by_reason"] = _reason_counts(excluded_games)
    _write_outputs(yearly_frames, exclusions, excluded_games, all_fbs_teams)
    with (DATA_DIR / "validation_report.json").open("w", encoding="utf-8") as target:
        json.dump(report, target, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except CollectionError as error:
        raise SystemExit(str(error)) from error
