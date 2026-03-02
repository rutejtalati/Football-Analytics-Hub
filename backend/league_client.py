from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

import requests

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
FOOTBALL_DATA_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
CACHE_TTL_SECONDS = int(os.environ.get("FOOTBALL_DATA_TTL_SECONDS", "900"))
_CACHE: Dict[str, Tuple[float, Any]] = {}


def _cache_get(key: str) -> Any:
    row = _CACHE.get(key)
    if not row:
        return None
    expires_at, data = row
    if time.time() >= expires_at:
        _CACHE.pop(key, None)
        return None
    return data


def _cache_set(key: str, data: Any) -> Any:
    _CACHE[key] = (time.time() + CACHE_TTL_SECONDS, data)
    return data


def _request(path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not FOOTBALL_DATA_API_KEY:
        raise RuntimeError("FOOTBALL_DATA_API_KEY is not set.")
    url = f"{FOOTBALL_DATA_BASE}{path}"
    r = requests.get(url, params=params or {}, headers={"X-Auth-Token": FOOTBALL_DATA_API_KEY}, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_standings(comp_id: str) -> List[Dict[str, Any]]:
    cache_key = f"standings:{comp_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    data = _request(f"/competitions/{comp_id}/standings")
    standings = data.get("standings", [])
    table = []
    for s in standings:
        if str(s.get("type", "")).upper() == "TOTAL":
            table = s.get("table", [])
            break
    if not table and standings:
        table = standings[0].get("table", [])

    rows: List[Dict[str, Any]] = []
    for r in table:
        t = r.get("team", {}) or {}
        gf = int(r.get("goalsFor", 0) or 0)
        ga = int(r.get("goalsAgainst", 0) or 0)
        rows.append(
            {
                "position": int(r.get("position", 0) or 0),
                "team_name": str(t.get("shortName") or t.get("name") or ""),
                "team_id": int(t.get("id", 0) or 0),
                "played": int(r.get("playedGames", 0) or 0),
                "points": int(r.get("points", 0) or 0),
                "gf": gf,
                "ga": ga,
                "gd": gf - ga,
            }
        )
    rows.sort(key=lambda x: x["position"])
    return _cache_set(cache_key, rows)


def fetch_fixtures(comp_id: str, days: int) -> List[Dict[str, Any]]:
    span = max(1, min(int(days), 60))
    start = date.today()
    end = start + timedelta(days=span)
    date_from = start.isoformat()
    date_to = end.isoformat()
    cache_key = f"fixtures:{comp_id}:{date_from}:{date_to}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    data = _request(
        f"/competitions/{comp_id}/matches",
        params={"dateFrom": date_from, "dateTo": date_to},
    )
    matches = data.get("matches", [])
    out: List[Dict[str, Any]] = []
    for m in matches:
        home = m.get("homeTeam", {}) or {}
        away = m.get("awayTeam", {}) or {}
        out.append(
            {
                "match_id": int(m.get("id", 0) or 0),
                "utc_date": str(m.get("utcDate") or ""),
                "status": str(m.get("status") or ""),
                "home_team_id": int(home.get("id", 0) or 0),
                "home_team_name": str(home.get("shortName") or home.get("name") or ""),
                "away_team_id": int(away.get("id", 0) or 0),
                "away_team_name": str(away.get("shortName") or away.get("name") or ""),
            }
        )
    out.sort(key=lambda x: x["utc_date"])
    return _cache_set(cache_key, out)
