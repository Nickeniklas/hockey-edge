"""Elo smoke run (docs/ELO_HARNESS_PLAN.md, phase E1). Sanity only.

    python scripts/elo_smoke.py

One pass with default parameters over data/features.db, the link fitted on
the 2016-2017 regular seasons and frozen. Prints per-season log loss against
base rates, the rank correlation of regular-season-end Elo with points, and
the first-season teams' entries. Read-only; stores nothing. The real
walk-forward numbers come from the harness (phase E2), not from here.
"""

import sqlite3

import numpy as np
from scipy.stats import spearmanr

from hockey_edge.features.db import FEATURES_DB_PATH
from hockey_edge.models import CLASSES, elo, link
from hockey_edge.validation import metrics

LINK_SEASONS = (2016, 2017)
JOKERIT = "238306801:jokerit"


def main() -> None:
    conn = sqlite3.connect(FEATURES_DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    games = elo.load_games(conn)
    names = dict(conn.execute("SELECT team_id, MAX(team_name) FROM team_season GROUP BY team_id"))
    params = elo.EloParams()
    result = elo.run(games, elo.load_team_seasons(conn), params)
    conn.close()

    scored = [(g, r) for g, r in zip(games, result.games) if g.in_eval and g.ended]
    tuning = [(g, r) for g, r in scored if g.season in LINK_SEASONS]
    fitted = link.fit([r.gap for _, r in tuning], [g.result for g, _ in tuning])
    rates = np.array([sum(g.result == c for g, _ in tuning) for c in CLASSES]) / len(tuning)

    print(f"params: {params}")
    print(f"link on {LINK_SEASONS[0]}-{LINK_SEASONS[-1]} ({len(tuning)} games): "
          f"c1={fitted.c1:.4f} c2={fitted.c2:.4f} beta={fitted.beta:.4f}")
    print(f"base rates H/D/A: {rates.round(4)}")
    print(f"at an even gap with home edge: {link.predict(fitted, [params.hfa / 400])[0].round(4)}")

    print("\nseason  games  elo      base     elo-base  se       spearman")
    for season in sorted({g.season for g, _ in scored}):
        rows = [(g, r) for g, r in scored if g.season == season]
        results = [g.result for g, _ in rows]
        elo_loss = metrics.game_log_loss(link.predict(fitted, [r.gap for _, r in rows]), results)
        base_loss = metrics.game_log_loss(np.tile(rates, (len(rows), 1)), results)
        diff = metrics.paired_difference(elo_loss, base_loss)
        note = " (burn-in)" if season == 2015 else " (link fitted here)" if season in LINK_SEASONS else ""
        print(f"{season}    {len(rows):4d}  {elo_loss.mean():.4f}   {base_loss.mean():.4f}   "
              f"{diff.mean:+.4f}   {diff.se:.4f}   {_rank_correlation(rows):.3f}{note}")

    test = [(g, r) for g, r in scored if 2018 <= g.season <= 2026]
    results = [g.result for g, _ in test]
    diff = metrics.paired_difference(
        metrics.game_log_loss(link.predict(fitted, [r.gap for _, r in test]), results),
        metrics.game_log_loss(np.tile(rates, (len(test), 1)), results),
    )
    print(f"pooled 2018-2026: {diff.n} games, elo-base {diff.mean:+.4f} +/- {diff.se:.4f}")

    print("\nfirst-season entries")
    for c in result.cold_starts:
        print(f"  {c.season} {names[c.team_id]}: {c.path}, entry {c.entry:.1f} "
              f"(returning mean {c.returning_mean:.1f}, delta {c.delta:.1f}, {c.n_cases} earlier cases"
              + (f", goal diff {c.mean_goal_diff:+.3f}/game" if c.mean_goal_diff is not None else "")
              + (f", slope {c.slope:.5f} goals/point" if c.slope is not None else "") + ")")

    print("\nJokerit 2027")
    print("  date        opponent   h/a  score  pre      post")
    for g, r in zip(games, result.games):
        if g.season == 2027 and g.ended and JOKERIT in (g.home, g.away):
            home = g.home == JOKERIT
            print(f"  {g.start_date}  {names[g.away if home else g.home]:9s}  {'H' if home else 'A'}    "
                  f"{g.home_goals}-{g.away_goals} {g.result}  {r.home_pre if home else r.away_pre:.1f}   "
                  f"{r.home_post if home else r.away_post:.1f}")
    ranked = sorted(result.ratings.items(), key=lambda item: -item[1])
    print("\ncurrent ratings: " + ", ".join(f"{names[t]} {v:.0f}" for t, v in ranked))


def _rank_correlation(rows) -> float:
    """Elo after each team's last regular-season game against its points."""
    points: dict[str, int] = {}
    rating: dict[str, float] = {}
    for g, r in rows:
        if g.result == "D":
            home = 2 if g.home_goals > g.away_goals else 1
            away = 3 - home
        else:
            home, away = (3, 0) if g.result == "H" else (0, 3)
        points[g.home] = points.get(g.home, 0) + home
        points[g.away] = points.get(g.away, 0) + away
        rating[g.home], rating[g.away] = r.home_post, r.away_post
    teams = sorted(points)
    return float(spearmanr([rating[t] for t in teams], [points[t] for t in teams]).statistic)


if __name__ == "__main__":
    main()
