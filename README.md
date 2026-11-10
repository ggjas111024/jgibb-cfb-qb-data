# College Football Quarterback Performance and Team Success

This repository builds an original dataset for a BYU STAT 386 data-curation
project. It asks:

> How strongly is quarterback passing performance associated with team success
> in FBS college football?

The project uses the [CollegeFootballData API](https://collegefootballdata.com/)
for every source record. The dataset contains one primary quarterback per FBS
team and season for 2023, 2024, and 2025. A primary quarterback is the player
with the most passing attempts for that team during the included games. The
quarterback must have a passing box-score entry in at least 10 included games.

## Collection method

The collector uses three CollegeFootballData endpoints:

1. `/teams/fbs?year=...` provides season-specific FBS membership.
2. `/games?year=...&seasonType=regular&classification=fbs` provides completed
   games and scores.
3. `/games/players` provides weekly passing box scores for those games.

The code keeps completed, decided regular-season games involving at least one
season-specific FBS team. It removes games whose CFBD notes identify them as a
championship and removes any game carrying playoff metadata. It then aggregates
each passer's completions, attempts, yards, touchdowns, and interceptions from
the remaining game box scores. Team wins and losses come from the exact same
set of included games.

Conference championships, bowls, and College Football Playoff games therefore
do not affect either quarterback statistics or team records. A completed tie
causes collection to stop instead of being added to the dataset. Sacks are
ignored during parsing and are not a dataset field.

## Dataset columns

| Column | Description |
| --- | --- |
| `year` | Season |
| `qb_id` | CollegeFootballData athlete identifier |
| `qb_name` | Quarterback name |
| `team_id` | CollegeFootballData team identifier |
| `team` | Team name |
| `completions` | Completed passes |
| `attempts` | Passing attempts |
| `cmp_pct` | `100 * completions / attempts` |
| `passing_yards` | Passing yards |
| `yards_per_attempt` | `passing_yards / attempts` |
| `passing_tds` | Passing touchdowns |
| `interceptions` | Interceptions thrown |
| `td_pct` | `100 * passing_tds / attempts` |
| `int_pct` | `100 * interceptions / attempts` |
| `passing_yards_per_game` | Passing yards divided by QB games played |
| `attempts_per_game` | Passing attempts divided by QB games played |
| `rtg` | NCAA passer efficiency rating calculated from components |
| `games_played` | Included games with a passing box-score entry for the QB |
| `wins` | Team wins in included games |
| `losses` | Team losses in included games |
| `win_pct` | `wins / (wins + losses)` |

NCAA passer efficiency is calculated as:

```text
(8.4 * passing yards + 330 * passing TDs + 100 * completions
 - 200 * interceptions) / passing attempts
```

All percentages, per-attempt measures, per-game measures, passer rating, and
winning percentage are calculated locally from the count statistics. No rate
field is copied from CFBD.

## Set up the API key

Request an API key from CollegeFootballData. The repository includes a local
`.env` file and a safe `.env.example` template. Put the key in `.env`:

```dotenv
CFBD_API_KEY=your_actual_key_here
```

`.env` is ignored by Git. Never commit the key, paste it into source code, or
put it in a request URL.

## Reproduce the project

Create and activate a Python environment, then install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Collect and validate the data:

```bash
python collect_data.py
```

The default run uses about one API request per season-week plus one games and
one FBS-team request per season. Responses are cached in `.cache/cfbd/`, so
later runs normally use no additional API calls. Use `--refresh` only when you
intend to spend API quota on fresh responses.

Create the analysis tables and figures:

```bash
python analyze_data.py
```

The analysis script refuses to run unless `data/validation_report.json`
identifies CollegeFootballData as the source and confirms that zero ties and
zero sack fields were included.

## Outputs

```text
data/
├── qb_stats_2023.csv
├── qb_stats_2024.csv
├── qb_stats_2025.csv
├── qb_stats_combined.csv
├── exclusions.csv
├── excluded_games.csv
├── fbs_teams_by_year.csv
└── validation_report.json

analysis/
├── summary_statistics.csv
├── correlations_with_win_pct.csv
└── figures/
```

`exclusions.csv` records team-seasons omitted because of incomplete box scores,
no passing statistics, or the 10-game rule. `excluded_games.csv` records
championship or playoff games removed before aggregation.

## Validation

The collector stops instead of publishing output when a major validation
fails. Checks include:

- at least 200 observations for a full three-season run;
- one row per team and season;
- one row per quarterback, team, and season;
- at least 10 included games for every selected quarterback;
- nonblank CFBD athlete and team IDs;
- internally consistent rate statistics, passer rating, records, and winning
  percentage;
- zero ties and zero sack fields;
- no missing or non-finite analysis values; and
- explicit reporting of excluded games and team-seasons.

## Data use

The API key is a credential and remains local. Review the current
[CollegeFootballData documentation](https://collegefootballdata.com/), account
limits, and terms before redistributing generated data. The cache exists to
make reruns reproducible and avoid unnecessary use of API quota.

## Interpretation limits

This is an observational analysis. Correlation does not show that quarterback
performance causes wins. Defense, schedule strength, coaching, offensive
system, injuries, and game situation affect both passing statistics and team
results. Requiring 10 passing appearances also excludes some injury-heavy
seasons and quarterback rotations.
