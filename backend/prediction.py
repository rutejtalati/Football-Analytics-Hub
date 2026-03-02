from __future__ import annotations

import math
import random
from typing import Any, Dict, List

try:
    import numpy as np
except Exception:
    np = None

DEFAULT_MU = 1.35


def _poisson_sample_knuth(lam: float) -> int:
    lam = max(0.0, float(lam))
    if lam == 0:
        return 0
    l = math.exp(-lam)
    k = 0
    p = 1.0
    while p > l:
        k += 1
        p *= random.random()
    return k - 1


def _mc_simulate(lambda_home: float, lambda_away: float, mc_runs: int) -> tuple[list[int], list[int]]:
    if np is not None:
        rng = np.random.default_rng()
        home_sim = rng.poisson(lambda_home, size=mc_runs).tolist()
        away_sim = rng.poisson(lambda_away, size=mc_runs).tolist()
        return home_sim, away_sim

    home_sim = [_poisson_sample_knuth(lambda_home) for _ in range(mc_runs)]
    away_sim = [_poisson_sample_knuth(lambda_away) for _ in range(mc_runs)]
    return home_sim, away_sim


def poisson_pmf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    lam = max(0.0, float(lam))
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def score_matrix(lambda_home: float, lambda_away: float, max_goals: int = 5) -> List[List[float]]:
    home_probs = [poisson_pmf(i, lambda_home) for i in range(max_goals + 1)]
    away_probs = [poisson_pmf(j, lambda_away) for j in range(max_goals + 1)]
    return [[home_probs[i] * away_probs[j] for j in range(max_goals + 1)] for i in range(max_goals + 1)]


def estimate_team_strengths(standings_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    teams = standings_rows or []
    gfpg_vals = []
    for r in teams:
        played = max(1, int(r.get("played", 0) or 0))
        gf = float(r.get("gf", 0) or 0.0)
        gfpg_vals.append(gf / played)
    mu = sum(gfpg_vals) / len(gfpg_vals) if gfpg_vals else DEFAULT_MU
    if mu <= 0:
        mu = DEFAULT_MU

    strengths: Dict[int, Dict[str, float]] = {}
    for r in teams:
        team_id = int(r.get("team_id", 0) or 0)
        if team_id <= 0:
            continue
        played = max(1, int(r.get("played", 0) or 0))
        gfpg = float(r.get("gf", 0) or 0.0) / played
        gapg = float(r.get("ga", 0) or 0.0) / played
        attack = gfpg / mu if mu > 0 else 1.0
        defense_weakness = gapg / mu if mu > 0 else 1.0
        strengths[team_id] = {
            "attack": attack if attack > 0 else 1.0,
            "defense_weakness": defense_weakness if defense_weakness > 0 else 1.0,
        }

    return {"mu": mu, "teams": strengths}


def predict_fixture(home_team_id: int, away_team_id: int, strengths: Dict[str, Any]) -> Dict[str, Any]:
    mu = float(strengths.get("mu", DEFAULT_MU) or DEFAULT_MU)
    team_strengths = strengths.get("teams", {}) or {}
    home = team_strengths.get(int(home_team_id), {"attack": 1.0, "defense_weakness": 1.0})
    away = team_strengths.get(int(away_team_id), {"attack": 1.0, "defense_weakness": 1.0})

    a_home = float(home.get("attack", 1.0) or 1.0)
    d_home = float(home.get("defense_weakness", 1.0) or 1.0)
    a_away = float(away.get("attack", 1.0) or 1.0)
    d_away = float(away.get("defense_weakness", 1.0) or 1.0)

    lambda_home = max(0.05, mu * a_home * d_away * 1.10)
    lambda_away = max(0.05, mu * a_away * d_home)

    mat = score_matrix(lambda_home, lambda_away, max_goals=5)
    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    best = (0, 0, -1.0)
    for i in range(6):
        for j in range(6):
            v = mat[i][j]
            if i > j:
                p_home += v
            elif i == j:
                p_draw += v
            else:
                p_away += v
            if v > best[2]:
                best = (i, j, v)

    mc_runs = 20000
    home_sim, away_sim = _mc_simulate(lambda_home, lambda_away, mc_runs)
    total_sim = [h + a for h, a in zip(home_sim, away_sim)]

    p_home_win = sum(1 for h, a in zip(home_sim, away_sim) if h > a) / mc_runs
    p_draw = sum(1 for h, a in zip(home_sim, away_sim) if h == a) / mc_runs
    p_away_win = sum(1 for h, a in zip(home_sim, away_sim) if h < a) / mc_runs

    exp_home_goals = sum(home_sim) / mc_runs
    exp_away_goals = sum(away_sim) / mc_runs
    exp_goals_total = exp_home_goals + exp_away_goals

    p_over_0_5 = sum(1 for t in total_sim if t > 0) / mc_runs
    p_over_1_5 = sum(1 for t in total_sim if t > 1) / mc_runs
    p_over_2_5 = sum(1 for t in total_sim if t > 2) / mc_runs
    p_over_3_5 = sum(1 for t in total_sim if t > 3) / mc_runs
    p_btts = sum(1 for h, a in zip(home_sim, away_sim) if h >= 1 and a >= 1) / mc_runs
    p_home_cs = sum(1 for a in away_sim if a == 0) / mc_runs
    p_away_cs = sum(1 for h in home_sim if h == 0) / mc_runs
    p_home_2plus = sum(1 for h in home_sim if h >= 2) / mc_runs
    p_away_2plus = sum(1 for a in away_sim if a >= 2) / mc_runs

    score_counts: Dict[tuple[int, int], int] = {}
    for h, a in zip(home_sim, away_sim):
        score_counts[(h, a)] = score_counts.get((h, a), 0) + 1
    ordered = sorted(score_counts.items(), key=lambda kv: kv[1], reverse=True)
    top_scores = []
    for (hs, as_), cnt in ordered[:3]:
        top_scores.append({"score": f"{hs}-{as_}", "p": float(cnt / mc_runs)})
    most_likely_score = top_scores[0]["score"] if top_scores else f"{best[0]}-{best[1]}"
    p_most_likely_score = top_scores[0]["p"] if top_scores else float(best[2])

    outcome = [p_home_win, p_draw, p_away_win]
    outcome_entropy = -sum(p * math.log2(p) for p in outcome if p > 0)

    return {
        "xgH": round(lambda_home, 4),
        "xgA": round(lambda_away, 4),
        "mc_runs": mc_runs,
        "exp_home_goals": round(exp_home_goals, 4),
        "exp_away_goals": round(exp_away_goals, 4),
        "exp_goals_total": round(exp_goals_total, 4),
        "p_home_win": round(p_home_win, 4),
        "p_draw": round(p_draw, 4),
        "p_away_win": round(p_away_win, 4),
        "p_over_0_5": round(p_over_0_5, 4),
        "p_over_1_5": round(p_over_1_5, 4),
        "p_over_2_5": round(p_over_2_5, 4),
        "p_over_3_5": round(p_over_3_5, 4),
        "p_btts": round(p_btts, 4),
        "p_home_cs": round(p_home_cs, 4),
        "p_away_cs": round(p_away_cs, 4),
        "p_home_2plus": round(p_home_2plus, 4),
        "p_away_2plus": round(p_away_2plus, 4),
        "most_likely_score": most_likely_score,
        "p_most_likely_score": round(p_most_likely_score, 4),
        "top_scores": top_scores,
        "outcome_entropy": round(float(outcome_entropy), 4),
        "expected_goal_diff": round(lambda_home - lambda_away, 4),
        # Backward compatibility:
        "lambda_h": round(lambda_home, 4),
        "lambda_a": round(lambda_away, 4),
        "lambda_home": round(lambda_home, 4),
        "lambda_away": round(lambda_away, 4),
        "p_home": round(p_home_win, 4),
        "p_away": round(p_away_win, 4),
        "home_pct": round(p_home_win * 100.0, 2),
        "draw_pct": round(p_draw * 100.0, 2),
        "away_pct": round(p_away_win * 100.0, 2),
        "predicted_score": most_likely_score,
    }
