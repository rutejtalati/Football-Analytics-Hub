from __future__ import annotations

from typing import Dict, List, Optional, Tuple

LEAGUE_IDS: Dict[str, int] = {
    "epl": 39,
    "laliga": 140,
    "seriea": 135,
    "ligue1": 61,
}

CURRENT_SEASON = 2025

SUPPORTED_LEAGUES: Dict[str, Dict[str, str]] = {
    "EPL": {"name": "Premier League", "competition_id": "PL"},
    "LALIGA": {"name": "La Liga", "competition_id": "PD"},
    "LIGUE1": {"name": "Ligue 1", "competition_id": "FL1"},
    "SERIEA": {"name": "Serie A", "competition_id": "SA"},
}


def list_leagues() -> List[Dict[str, str]]:
    return [
        {"code": code, "name": meta["name"], "competition_id": meta["competition_id"]}
        for code, meta in SUPPORTED_LEAGUES.items()
    ]


def get_competition(code: str) -> Optional[Tuple[str, str]]:
    if not code:
        return None
    key = code.strip().upper()
    meta = SUPPORTED_LEAGUES.get(key)
    if not meta:
        return None
    return meta["name"], meta["competition_id"]
