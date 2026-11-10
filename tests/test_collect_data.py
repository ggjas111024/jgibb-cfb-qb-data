"""Focused unit tests for the CFBD collection rules."""

import unittest

from collect_data import (
    FINAL_COLUMNS,
    CollectionError,
    _is_championship_game,
    _parse_passing_category,
    _team_box_by_id,
    aggregate_year,
)


class CollectionRuleTests(unittest.TestCase):
    def test_championship_note_is_excluded(self) -> None:
        self.assertTrue(_is_championship_game({"notes": "Big Ten Championship"}))

    def test_ordinary_regular_season_game_is_kept(self) -> None:
        self.assertFalse(_is_championship_game({"notes": None}))

    def test_cfbd_passing_category_parser_ignores_sacks(self) -> None:
        category = {
            "name": "passing",
            "types": [
                {
                    "name": "C/ATT",
                    "athletes": [{"id": "101", "name": "Test QB", "stat": "26/30"}],
                },
                {
                    "name": "YDS",
                    "athletes": [{"id": "101", "name": "Test QB", "stat": "280"}],
                },
                {
                    "name": "TD",
                    "athletes": [{"id": "101", "name": "Test QB", "stat": "3"}],
                },
                {
                    "name": "INT",
                    "athletes": [{"id": "101", "name": "Test QB", "stat": "1"}],
                },
                {
                    "name": "SACKS",
                    "athletes": [{"id": "101", "name": "Test QB", "stat": "4"}],
                },
            ],
        }
        self.assertEqual(
            _parse_passing_category(category),
            [("101", "Test QB", 26, 30, 280, 3, 1)],
        )

    def test_bad_completion_attempt_value_fails(self) -> None:
        category = {
            "name": "passing",
            "types": [
                {
                    "name": "C/ATT",
                    "athletes": [{"id": "101", "name": "Test QB", "stat": "26"}],
                }
            ],
        }
        with self.assertRaises(CollectionError):
            _parse_passing_category(category)

    def test_box_score_maps_to_cfbd_team_ids(self) -> None:
        game = {
            "homeId": 1,
            "homeTeam": "Home State",
            "awayId": 2,
            "awayTeam": "Away Tech",
        }
        box = {
            "teams": [
                {"team": "Home State", "homeAway": "home", "categories": []},
                {"team": "Away Tech", "homeAway": "away", "categories": []},
            ]
        }
        self.assertEqual(set(_team_box_by_id(game, box)), {"1", "2"})

    def test_dataset_schema_has_no_sacks(self) -> None:
        self.assertFalse(any("sack" in column.lower() for column in FINAL_COLUMNS))

    def test_game_aggregation_calculates_rates_and_record(self) -> None:
        games = [
            {
                "id": game_id,
                "homeId": 1,
                "homeTeam": "Home State",
                "homePoints": 30,
                "awayId": 2,
                "awayTeam": "Away Tech",
                "awayPoints": 20,
            }
            for game_id in range(1, 11)
        ]
        types = [
            {"name": "C/ATT", "athletes": [{"id": "101", "name": "Test QB", "stat": "20/30"}]},
            {"name": "YDS", "athletes": [{"id": "101", "name": "Test QB", "stat": "250"}]},
            {"name": "TD", "athletes": [{"id": "101", "name": "Test QB", "stat": "2"}]},
            {"name": "INT", "athletes": [{"id": "101", "name": "Test QB", "stat": "1"}]},
        ]
        boxes = [
            {
                "id": game_id,
                "teams": [
                    {
                        "team": "Home State",
                        "homeAway": "home",
                        "categories": [{"name": "passing", "types": types}],
                    }
                ],
            }
            for game_id in range(1, 11)
        ]
        frame, exclusions = aggregate_year(2023, {"1": "Home State"}, games, boxes)
        self.assertTrue(exclusions.empty)
        row = frame.iloc[0]
        self.assertEqual(row["attempts"], 300)
        self.assertEqual(row["passing_yards"], 2500)
        self.assertAlmostEqual(row["cmp_pct"], 100 * 200 / 300)
        self.assertAlmostEqual(row["td_pct"], 100 * 20 / 300)
        self.assertAlmostEqual(row["int_pct"], 100 * 10 / 300)
        self.assertEqual(row["wins"], 10)
        self.assertEqual(row["losses"], 0)
        self.assertEqual(row["win_pct"], 1.0)

    def test_tie_is_rejected(self) -> None:
        game = {
            "id": 1,
            "homeId": 1,
            "homeTeam": "Home State",
            "homePoints": 20,
            "awayId": 2,
            "awayTeam": "Away Tech",
            "awayPoints": 20,
        }
        with self.assertRaises(CollectionError):
            aggregate_year(2023, {"1": "Home State"}, [game], [])


if __name__ == "__main__":
    unittest.main()
