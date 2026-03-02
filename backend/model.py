from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from rapidfuzz import fuzz, process

def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default

STATUS_MAP = {
    "a": ("Available", 1.0),
    "d": ("Doubtful", 0.75),
    "i": ("Injured", 0.25),
    "s": ("Suspended", 0.0),
    "u": ("Unavailable", 0.0),
}

TEAM_MAP_FPL_TO_UNDERSTAT = {
    "ARS": "Arsenal",
    "AVL": "Aston Villa",
    "BOU": "Bournemouth",
    "BRE": "Brentford",
    "BHA": "Brighton",
    "CHE": "Chelsea",
    "CRY": "Crystal Palace",
    "EVE": "Everton",
    "FUL": "Fulham",
    "IPS": "Ipswich",
    "LEI": "Leicester",
    "LIV": "Liverpool",
    "MCI": "Manchester City",
    "MUN": "Manchester United",
    "NEW": "Newcastle United",
    "NFO": "Nottingham Forest",
    "SOU": "Southampton",
    "TOT": "Tottenham",
    "WHU": "West Ham",
    "WOL": "Wolverhampton Wanderers",
}

POS_GOAL_POINTS = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}
POS_CS_POINTS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}

def estimate_cs_prob_from_fdr(fdr: float) -> float:
    return clamp(0.65 - (fdr * 0.1), 0.15, 0.65)

def estimate_fixture_multiplier_from_fdr(fdr: float) -> float:
    return clamp(1.16 - 0.08 * fdr, 0.78, 1.10)

def appearance_probability(chance_play_next: Optional[float], minutes_per_game: float, status_factor: float) -> float:
    cop = 1.0 if chance_play_next is None else clamp(chance_play_next / 100.0)
    min_sig = clamp((minutes_per_game - 10) / 80.0, 0.2, 1.0)
    return clamp((0.55 * cop + 0.45 * min_sig) * status_factor)

def minutes_60plus_probability(minutes_per_game: float) -> float:
    return clamp((minutes_per_game - 35) / 50.0, 0.0, 1.0)

def match_understat_player(
    fpl_name: str,
    fpl_team_short: str,
    understat_players: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    target_team = TEAM_MAP_FPL_TO_UNDERSTAT.get(fpl_team_short, None)
    candidates = understat_players

    if target_team:
        same_team = [p for p in understat_players if str(p.get("team_title", "")).strip() == target_team]
        if same_team:
            candidates = same_team

    cand_names = [(str(p.get("player_name", "")).strip(), p) for p in candidates if p.get("player_name")]
    if not cand_names:
        return None

    names_only = [n for n, _ in cand_names]
    best = process.extractOne(fpl_name, names_only, scorer=fuzz.WRatio)
    if not best:
        return None

    best_name, score, idx = best[0], best[1], best[2]
    if score < 78:
        return None
    return cand_names[idx][1]

def xg_xa_per90(understat_row: Dict[str, Any]) -> Tuple[float, float]:
    minutes = safe_float(understat_row.get("minutes", 0), 0.0)
    xg = safe_float(understat_row.get("xG", 0), 0.0)
    xa = safe_float(understat_row.get("xA", 0), 0.0)
    if minutes <= 0:
        return 0.0, 0.0
    return (xg / minutes) * 90.0, (xa / minutes) * 90.0

def expected_points_if_appears(
    pos: str,
    minutes_per_game: float,
    xg90: float,
    xa90: float,
    fdr: float
) -> float:
    p60 = minutes_60plus_probability(minutes_per_game)
    exp_app = 1.0 * (1.0 - p60) + 2.0 * p60

    minute_scale = clamp(minutes_per_game / 90.0, 0.15, 1.0)
    fx_mult = estimate_fixture_multiplier_from_fdr(fdr)

    goal_pts = POS_GOAL_POINTS.get(pos, 4)
    assist_pts = 3.0

    exp_goals = xg90 * minute_scale * fx_mult
    exp_assists = xa90 * minute_scale * fx_mult

    exp_goal_points = exp_goals * goal_pts
    exp_assist_points = exp_assists * assist_pts

    cs_prob = estimate_cs_prob_from_fdr(fdr)
    cs_pts = POS_CS_POINTS.get(pos, 0)
    exp_cs_points = cs_prob * cs_pts

    xgi = exp_goals + exp_assists
    exp_bonus = clamp(0.15 + 0.65 * xgi, 0.0, 1.4)

    return exp_app + exp_goal_points + exp_assist_points + exp_cs_points + exp_bonus