from __future__ import annotations
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

DEFAULT_LEAGUE = "EPL"
DEFAULT_SEASON = "2025"

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
PLAYERS_CACHE = os.path.join(CACHE_DIR, "understat_players.json")
TEAMS_CACHE = os.path.join(CACHE_DIR, "understat_teams.json")

def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)

def _now() -> float:
    return time.time()

def _read_cache(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _write_cache(path: str, payload: Dict[str, Any]) -> None:
    _ensure_cache_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

def _extract_embedded_json(html: str, key: str) -> Any:
    """
    Understat embeds JSON in a few different patterns over time.
    We try multiple extraction strategies.

    Expected keys: playersData, teamsData
    """
    # Pattern A: var playersData = JSON.parse('....');
    pat_a = re.compile(rf"{re.escape(key)}\s*=\s*JSON\.parse\('(.+?)'\)\s*;", re.DOTALL)
    m = pat_a.search(html)
    if m:
        raw = m.group(1)
        raw = raw.encode("utf-8").decode("unicode_escape")
        raw = raw.replace("\\'", "'")
        return json.loads(raw)

    # Pattern B: JSON.parse("....") (double quotes)
    pat_b = re.compile(rf"{re.escape(key)}\s*=\s*JSON\.parse\(\"(.+?)\"\)\s*;", re.DOTALL)
    m = pat_b.search(html)
    if m:
        raw = m.group(1)
        raw = raw.encode("utf-8").decode("unicode_escape")
        raw = raw.replace('\\"', '"')
        return json.loads(raw)

    # Pattern C: script contains "JSON.parse('...')" but without "key ="
    pat_c = re.compile(rf"JSON\.parse\('(.+?)'\)", re.DOTALL)
    for mm in pat_c.finditer(html):
        raw = mm.group(1)
        try:
            txt = raw.encode("utf-8").decode("unicode_escape").replace("\\'", "'")
            obj = json.loads(txt)
            # Heuristic: playersData is dict keyed by ids; teamsData is dict keyed by ids
            if isinstance(obj, dict) and obj:
                any_val = next(iter(obj.values()))
                if key == "playersData" and isinstance(any_val, dict) and ("xG" in any_val or "xA" in any_val):
                    return obj
                if key == "teamsData" and isinstance(any_val, dict) and ("history" in any_val or "title" in any_val):
                    return obj
        except Exception:
            continue

    # If we reach here, it might be a blocked page (Cloudflare) or Understat changed structure.
    raise ValueError(f"Could not extract {key} from Understat page (page structure may have changed or request blocked).")
def fetch_understat_league_players(
    league: str = DEFAULT_LEAGUE,
    season: str = DEFAULT_SEASON,
    ttl_seconds: int = 24 * 3600,
    timeout: int = 25
) -> List[Dict[str, Any]]:
    _ensure_cache_dir()
    cached = _read_cache(PLAYERS_CACHE)
    if cached and (float(cached.get("fetched_at", 0)) + ttl_seconds) > _now():
        return cached["data"]

    url = f"https://understat.com/league/{league}/{season}"
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    html = r.text

    data = _extract_embedded_json(html, "playersData")
    out: List[Dict[str, Any]] = []
    for pid, row in data.items():
        row = dict(row)
        row["understat_id"] = str(pid)
        out.append(row)

    _write_cache(PLAYERS_CACHE, {"fetched_at": _now(), "league": league, "season": season, "data": out})
    return out

def fetch_understat_league_teams(
    league: str = DEFAULT_LEAGUE,
    season: str = DEFAULT_SEASON,
    ttl_seconds: int = 24 * 3600,
    timeout: int = 25
) -> Dict[str, Any]:
    _ensure_cache_dir()
    cached = _read_cache(TEAMS_CACHE)
    if cached and (float(cached.get("fetched_at", 0)) + ttl_seconds) > _now():
        return cached["data"]

    url = f"https://understat.com/league/{league}/{season}"
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    html = r.text

    data = _extract_embedded_json(html, "teamsData")
    _write_cache(TEAMS_CACHE, {"fetched_at": _now(), "league": league, "season": season, "data": data})
    return data