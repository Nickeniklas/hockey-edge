# FEATURE_AUDIT — feature store phase F0

Read-only audits of `data/hockey.db`, run 2026-10-11 (after that day's nightly
sync: 7,331 games, 99 ended RUNKOSARJA games in season 2027). No tables were
created and nothing was written to either database. Plan:
`docs/FEATURE_STORE_PLAN.md`, phase F0.

"Competitive" below means `phase IN ('RUNKOSARJA','PLAYOFFS','PLAYOUT',
'QUALIFICATIONS')`.

## Summary

| # | Item | Result |
|---|---|---|
| 1 | Regulation result | `games.finished_type` is reliable. One PLAYOFFS game has a tied final. |
| 2 | Season-2027 completeness | Clean. `2701323` is not a defect. |
| 3 | Phase inventory | Six values; nothing new since PITSITURNAUS. |
| 4 | Foreign-opponent rule | Works, but wrongly flags the 2025 Jokerit qualification series. |
| 5 | First Liiga seasons | KooKoo 2016, Jukurit 2017, K-Espoo 2025, Jokerit 2027. |
| 6 | Strength state | `shot_events.strength_type` gives it directly. On-ice counts change convention in 2025. |
| 7 | Power-play derivation | Works, **but `game_penalty_events.team_id` is the wrong team in most 2017–2022 and 2024 games.** |
| 8 | xG availability | Game-level from 2021. Player-level only from 2025. |

Three findings change F1–F3 and need a decision; see the last section.

---

## 1. Regulation result

```sql
SELECT finished_type, ABS(home_goals - away_goals) AS diff, COUNT(*)
FROM games WHERE ended = 1 GROUP BY 1, 2;
```

`games.finished_type` is set on every ended game (6,883) and on no unended
game. Three values: `ENDED_DURING_REGULAR_GAME_TIME`,
`ENDED_DURING_EXTENDED_GAME_TIME`, `ENDED_DURING_WINNING_SHOT_COMPETITION`.

**Target rule:** regular time → H or A from the final score; either other
value → D.

Checks on competitive games, none of which use goal-event counts to decide:

- Every overtime and shootout game has a goal difference of exactly 1.
- `game_time_seconds` agrees: RUNKOSARJA regular-time games are 3,600 s
  (4,216 of 4,217; the exception is 2021:480, already known as damaged),
  shootout games are all 3,900 s (559), overtime games are 3,601–3,900 s.
  Playoff overtime runs past 3,900 s, as expected.
- A `VL` (shootout winner) goal event exists in all 559 shootout games and in
  no other game.
- In the games where goal events equal the final score, regulation goals
  (periods 1–3) are tied in all 683 overtime and 411 shootout games and in
  none of the 3,729 regular-time games, apart from the game below.

Sample of labelled games (season 2026 playoffs): 55143 KooKoo–Tappara 2–3
OT, 7,794 s; 55117 Ilves–KalPa 2–1 OT, 4,734 s; 55104 Ässät–SaiPa 2–3 OT,
5,739 s. Shootout example: 2027:2701323 SaiPa–Ässät 1–2, 3,900 s.

**One anomaly: 2022:49294, PLAYOFFS, HPK–Lukko, final 2–2, regular time,
3,600 s.** Four goal events, 2–2 after three periods, no overtime rows. Its
series has only two games (Lukko won the first 3–0), so the game most likely
went to overtime and the stored record stops at 60 minutes. Under the rule it
is a regulation draw, which is right for the 1X2 target whatever happened
after. It is outside the v1 evaluation sample (RUNKOSARJA only). The winner is
unknown from stored data, which matters only if a rating update needs it.

Non-competitive phases have 23 tied regular-time games and four overtime games
with `game_time_seconds = 0`. They are excluded anyway.

## 2. Season-2027 completeness

```sql
SELECT s.endpoint, s.status, COUNT(*)
FROM games g LEFT JOIN sync_state s
  ON s.entity_id = g.season || ':' || g.game_id
 AND s.endpoint IN ('game_detail','game_stats','shotmap')
WHERE g.season = 2027 AND g.ended = 1 GROUP BY 1, 2;
```

All 150 ended games (99 RUNKOSARJA, 41 PRACTICE, 10 PITSITURNAUS) have a
`success` row for all three endpoints.

The 99 RUNKOSARJA games all have rows in rosters, penalty events, team, player
and goalie period stats, and shot events. None has zero time on ice or zero
corsi. Team-period goals equal the final score in all 99 once the shootout
goal is allowed for.

- **`2701323` is fine.** It ended 1–2 in a shootout. Team-period goals sum to
  2 and the third goal is the shootout winner, which is not a period goal. The
  "2 vs 3" was never a defect.
- 7 games have 0 power-play instances: the opening-night games already
  documented.
- 18 games have more goal events than final goals (the known surplus; 2701323
  has 5 events for 3 goals).
- Preseason only: 22 PRACTICE/PITSITURNAUS games have no shot events and one
  PRACTICE game has no penalties.

## 3. Phase inventory

```sql
SELECT season, phase, COUNT(*), SUM(ended) FROM games GROUP BY 1, 2;
```

| Season | RUNKOSARJA | PLAYOFFS | PLAYOUT | QUALIFICATIONS | PRACTICE | PITSITURNAUS |
|---|---|---|---|---|---|---|
| 2015 | 420 | 47 | | | 40 | |
| 2016 | 450 | 48 | | | 56 | |
| 2017 | 450 | 49 | | | 50 | |
| 2018 | 450 | 46 | | | 76 | |
| 2019 | 450 | 45 | | | 78 | |
| 2020 | 443 | 0 | | | 87 | |
| 2021 | 424 | 32 | | | 84 | |
| 2022 | 448 | 48 | | | 73 | |
| 2023 | 450 | 45 | | | 67 | |
| 2024 | 450 | 45 | | | 66 | |
| 2025 | 480 | 55 | 5 | 5 | 69 | |
| 2026 | 480 | 60 | | | 65 | |
| 2027 | 544 (99 ended) | | | | 41 | 10 |

Teams in RUNKOSARJA: 14 in 2015, 15 in 2016–2024, 16 in 2025–2026, 17 in
2027. Games per team: 60 everywhere except 2020 (59–60), 2021 (50–60) and
2022 (58–60), and 64 scheduled in 2027. **`season_format` cannot assume a
balanced schedule in 2020–2022.** Three PRACTICE games never ended (2015,
2017, 2021).

## 4. Foreign-opponent rule

```sql
-- rs = distinct (season, team_id) appearing in RUNKOSARJA
SELECT season, phase, COUNT(*) FROM games g
WHERE home_team_id NOT IN (SELECT tid FROM rs WHERE rs.season = g.season)
   OR away_team_id NOT IN (SELECT tid FROM rs WHERE rs.season = g.season)
GROUP BY 1, 2;
```

The rule "team id not in that season's RUNKOSARJA set" flags 273 PRACTICE
games and nothing in RUNKOSARJA, PLAYOFFS or PLAYOUT. Team ids are stable: no
id has two names and no name has two ids. Names are not stable for foreign
clubs (`Luleå HF` and `Luleå HF, SWE`), so the rule must use ids.

**It also flags all five 2025 QUALIFICATIONS games (Pelicans–Jokerit),**
because Jokerit was not a Liiga team that season. The plan wants
QUALIFICATIONS in rating history, so the two rules collide.

Since PRACTICE and PITSITURNAUS are excluded by phase already, the id rule
adds nothing in the competitive phases except this one series. Proposed:
`is_liiga_competitive` is decided by phase alone, and the id rule becomes a
separate flag (`has_non_liiga_team`) for information. Whether the Jokerit
series feeds ratings is a decision (below).

## 5. First Liiga season per team

```sql
SELECT tid, MIN(season), MAX(season), COUNT(*) FROM rs GROUP BY 1;
```

Thirteen teams are present from 2015, the first season in the data, so their
start is unknown from this database. Blues played 2015–2016 only.

First-season regular-season record of the teams that joined (league average
regulation-win share is 0.384 per team-game):

| Team | First season | Games | Goal diff / game | Reg. win share | Reg. loss share |
|---|---|---|---|---|---|
| KooKoo | 2016 | 60 | −0.50 | 0.267 | 0.400 |
| Jukurit | 2017 | 60 | −0.22 | 0.333 | 0.467 |
| K-Espoo | 2025 | 60 | −0.18 | 0.333 | 0.317 |
| Jokerit | 2027 | 13 so far | −1.31 | 0.154 | 0.615 |

The three completed cases average about −0.30 goals per game, with a spread of
−0.18 to −0.50. Sport's 2015 season (−1.07) may also be a first season, but
that cannot be confirmed from the data. Three cases is a thin prior, as the
plan expected.

## 6. Strength state

```sql
SELECT strength_type, own_team_players_on_ice, other_team_players_on_ice, COUNT(*)
FROM shot_events GROUP BY 1, 2, 3;
```

**`shot_events.strength_type` labels every shot** as `EvenStrengthShot`,
`PowerplayShot` or `ShorthandedShot`; no row is null. Nothing in the period
stats tables gives an even-strength split.

The label was checked against penalty timing (a shot is PP if the opponent has
more active 2- or 5-minute penalties than the shooter, with penalty teams
corrected as in item 7):

| Seasons | Shots | Agreement |
|---|---|---|
| 2015–2016 | 81,356 | 97.2% |
| 2017–2022 | 242,076 | 98.0% |
| 2023–2024 | 81,736 | 99.2% |
| 2025–2027 | 91,306 | 99.0% |

Use `strength_type` for even-strength shot features. A `shotmap` refetch is
not needed.

**Do not use the on-ice counts across seasons.** Through 2024 they count
skaters (5-on-5 is stored as 5/5). From 2025 they include the goalie (6/6).
That is the "+1 on refetch" seen in RESYNC 4a: liiga.fi changed the
convention, and stored values are internally consistent per era. 2026 has 40
games and 2027 has 3 still on the old convention.

Other checks on `shot_events`:

- Shots on goal (`GOAL` + `GOALIE_BLOCKED`) match the goalie tables within 2
  in nearly every game (e.g. 450/450 in 2018, 471/480 in 2026).
- Coverage is complete except the known 2025 gap (401 of 480 games) and five
  single games elsewhere.
- **`shooting_team_id` is the bare number (`933686567`), not the
  `933686567:saipa` form used in every other table.** A direct join on team id
  silently matches nothing.
- **Player-level corsi steps up about 10% in 2023** (sum of `corsi_for` per
  game: ~326 in 2015–2022, ~370 from 2023) while shot attempts in
  `shot_events` stay flat at ~92. The cause is unconfirmed; the size fits
  blocked shots being left out before 2023. Corsi shares are unaffected, but
  per-60 rates are not comparable across that boundary.

## 7. Power-play derivation

```sql
-- stored team of each penalty vs the roster team of the penalised player
SELECT p.season, SUM(r.team_id = p.team_id), SUM(r.team_id <> p.team_id)
FROM game_penalty_events p
JOIN games g USING (season, game_id)
JOIN game_rosters r ON r.season = p.season AND r.game_id = p.game_id
                   AND r.player_id = p.player_id
WHERE g.phase = 'RUNKOSARJA' AND p.player_id > 0 GROUP BY 1;
```

### The team on penalty events is often the opposing team

| Season | Penalties on the player's own team | On the other team |
|---|---|---|
| 2015 | 3,161 | 470 |
| 2016 | 3,223 | 99 |
| 2017 | 4 | 3,165 |
| 2018 | 0 | 3,432 |
| 2019 | 77 | 3,392 |
| 2020 | 49 | 3,220 |
| 2021 | 76 | 3,390 |
| 2022 | 0 | 3,040 |
| 2023 | 3,130 | 0 |
| 2024 | 0 | 2,996 |
| 2025 | 3,465 | 5 |
| 2026 | 3,217 | 4 |
| 2027 | 675 | 0 |

- **It comes from liiga.fi, not the parser.** In the raw 2018 game 1 response,
  the two penalties listed under `homeTeam.penaltyEvents` belong to away
  players and the three under `awayTeam` to home players. The 2023 game 1
  response has them the right way round. The parser stores the array it was
  given.
- **It is per game, not per season.** Almost every game is wholly right or
  wholly swapped. 2023 is right in the regular season and swapped in all 45
  playoff games; 2015 has 80 swapped competitive games; 2017 and 2019–2021
  have 2–8 correct games each. Eleven competitive games have a single
  stray row.
- The other tables are sound: goal-event teams match the scorer's roster team
  (5 mismatches in 31,580), and team-period goals match the final score by
  side in every 2015–2024 and 2027 game tested.
- Goalkeeper events show no swap where the player is in the roster.
- Team penalties (`player_id = 0`, 2,031 rows in competitive games) have no
  roster to check against. They can take the orientation of the other
  penalties in the same game.

Nothing built so far reads penalty teams, so no existing result is wrong.
Special-teams features would have been inverted for about seven seasons.

### Derivation, with the team corrected from the roster

Compared with stored `powerplay_instances` per team-game, regular season:

| Season | Opponent minors: exact / within 1 / corr | Coincident-adjusted: exact / within 1 / corr |
|---|---|---|
| 2015 | 53% / 88% / 0.86 | 69% / 96% / 0.95 |
| 2016 | 51% / 88% / 0.81 | 61% / 93% / 0.88 |
| 2017 | 62% / 95% / 0.89 | 77% / 99% / 0.96 |
| 2018 | 62% / 95% / 0.90 | 74% / 98% / 0.96 |
| 2019 | 59% / 93% / 0.88 | 73% / 98% / 0.96 |
| 2020 | 61% / 93% / 0.90 | 76% / 97% / 0.96 |
| 2021 | 62% / 93% / 0.90 | 76% / 98% / 0.96 |
| 2022 | 62% / 93% / 0.88 | 76% / 98% / 0.95 |
| 2023 | 49% / 86% / 0.79 | 62% / 94% / 0.89 |
| 2024 | 60% / 93% / 0.85 | 78% / 98% / 0.96 |
| 2027 | 59% / 85% / 0.68 | 68% / 91% / 0.76 |

"Coincident-adjusted" counts the opponent's 2- and 5-minute penalties with a
non-zero duration, minus those that start at the same second as one of the
team's own. It is the best of five variants tried and runs about 0.2 below the
stored count on average. Counting advantage intervals from timing was worse
(about −0.45). 2027 is pulled down by the seven opening-night games with a
stored count of 0. Why 2016 and 2023 fit less well was not investigated.

Applied to the stripped seasons it gives 3.14 opportunities per team-game in
2025 and 2.92 in 2026, in line with 3.13 stored for 2024 and 2.98 for 2027.

**The coincident-adjusted count is usable for 2025/2026, on the condition that
the penalised team comes from the roster, not from `team_id`.**

## 8. xG availability

```sql
SELECT season, COUNT(*), SUM(home_expected_goals > 0 AND away_expected_goals > 0)
FROM games WHERE ended = 1 AND phase = 'RUNKOSARJA' GROUP BY 1;
```

| Level | 2015–2020 | 2021 | 2022–2024 | 2025–2027 |
|---|---|---|---|---|
| Game xG (`games.*_expected_goals`) | none | 423/424 | all | all |
| Player xG (`game_player_period_stats`) | none | none | none | all |

- Game-level xG is complete from 2021, in playoffs too. This is better than
  the "~85% in 2021" in `PROJECT_CONTEXT.md`.
- **Player-level xG exists only from 2025.** The plan's "xG columns from 2021"
  holds for team features built from the game totals, not for anything built
  from players.
- Season xG totals sit below goals by 0.1–0.5 per game (2023: 4.68 vs 5.15).
- **Per-goalie xG is not stored.** The goalie table has no xG column, and
  `expected_goals_against` in the player table is a skater on-ice value. A
  goals-saved-above-expected feature can only use the opponent's game xG, from
  2021, and only cleanly when one goalie played the whole game.

---

## Decisions needed before F1

1. **Where to correct the penalty team.** By the project's data rule this
   belongs in the curated layer, not in feature code: either a corrected
   column on `game_penalty_events` filled by the parser from the roster, or a
   derived table. Both touch reviewed ingest code. It blocks F3 (special
   teams) and the penalty side of F2, not F1.
2. **The 2025 Pelicans–Jokerit qualification series.** Keep it in rating
   history (Jokerit then needs a rating in 2025, two seasons before it joins)
   or drop it as a non-Liiga opponent.
3. **Per-60 corsi across 2023.** Use shares only, or build attempts from
   `shot_events` (flat across the boundary, and it carries the strength
   label). The second also gives even-strength corsi for free.

## Changes to the plan implied by F0

- F1 spine: target from `finished_type`; `is_liiga_competitive` from phase.
- F1 `season_format`: games per team is not constant in 2020–2022.
- F2 shot quality: even strength from `shot_events.strength_type`; normalise
  `shooting_team_id` before joining; no on-ice counts.
- F2 goalie: no per-goalie xG; any GSAx column starts in 2021 and is
  team-game based.
- F2 xG: team xG from 2021, player xG from 2025.
- F3 special teams: coincident-adjusted count on roster-corrected teams.
