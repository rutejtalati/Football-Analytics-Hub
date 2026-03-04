from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from services.providers.football_provider import ProviderError


class ApiFootballProvider:
    BASE_URL = "https://v3.football.api-sports.io"
    LEAGUE_IDS = {
        "PL": 39,
        "PD": 140,
        "SA": 135,
        "FL1": 61,
    }

    def __init__(self):
        self.season = int(os.getenv("APIFOOTBALL_SEASON", "2025"))
        self.fixtures_ttl_seconds = 600
        self._fixtures_cache: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
        self._cache_lock = threading.Lock()

    def _api_key(self) -> str:
        key = (os.getenv("APIFOOTBALL_API_KEY") or os.getenv("FOOTBALL_DATA_API_KEY") or "").strip()
        if not key:
            raise ProviderError("APIFOOTBALL_API_KEY (or FOOTBALL_DATA_API_KEY) is not set.", status_code=503)
        return key

    def _league_id(self, code: str) -> int:
        league_id = self.LEAGUE_IDS.get((code or "").upper())
        if not league_id:
            raise ProviderError("Unsupported league code.", status_code=400)
        return league_id

    def _headers(self) -> Dict[str, str]:
        return {
            "x-apisports-key": self._api_key(),
            "Accept": "application/json",
            "User-Agent": "FootballAnalyticsHub/1.0",
        }

    def _request(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.BASE_URL}{path}",
                headers=self._headers(),
                params=params,
                timeout=6,
            )
        except requests.exceptions.Timeout as exc:
            raise ProviderError("API-Football timeout", status_code=503, upstream_status=504) from exc
        except requests.RequestException as exc:
            raise ProviderError(f"API-Football request failed: {str(exc)[:200]}", status_code=502) from exc

        if response.status_code != 200:
            raise ProviderError(
                f"API-Football error {response.status_code}: {response.text[:200]}",
                status_code=502,
                upstream_status=response.status_code,
            )
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise ProviderError("API-Football returned invalid JSON", status_code=502) from exc

    def _parse_matchday(self, round_text: Any) -> int:
        try:
            text = str(round_text or "")
            tail = text.split("-")[-1].strip()
            return int(tail) if tail.isdigit() else 0
        except Exception:
            return 0

    def _fixtures_cache_key(self, code: str, date_from: str, date_to: str) -> str:
        return f"{code}:{self.season}:{date_from}:{date_to}"

    def _fixtures_cache_get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        now = time.time()
        with self._cache_lock:
            row = self._fixtures_cache.get(key)
            if not row:
                return None
            expires_at, payload = row
            if now >= expires_at:
                self._fixtures_cache.pop(key, None)
                return None
            return payload

    def _fixtures_cache_set(self, key: str, payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        with self._cache_lock:
            self._fixtures_cache[key] = (time.time() + self.fixtures_ttl_seconds, payload)
        return payload

    def get_standings(self, code: str) -> List[Dict[str, Any]]:
        league_id = self._league_id(code)
        payload = self._request(
            "/standings",
            {"league": league_id, "season": self.season},
        )
        response_rows = payload.get("response", []) or []
        if not response_rows:
            return []
        league_info = response_rows[0].get("league", {}) or {}
        standings_groups = league_info.get("standings", []) or []
        if not standings_groups:
            return []

        rows: List[Dict[str, Any]] = []
        for row in standings_groups[0] or []:
            team = row.get("team", {}) or {}
            stats = row.get("all", {}) or {}
            goals = stats.get("goals", {}) or {}
            gf = int(goals.get("for") or 0)
            ga = int(goals.get("against") or 0)
            rows.append(
                {
                    "position": int(row.get("rank") or 0),
                    "teamName": str(team.get("name") or ""),
                    "teamShort": str(team.get("code") or team.get("name") or ""),
                    "playedGames": int(stats.get("played") or 0),
                    "won": int(stats.get("win") or 0),
                    "draw": int(stats.get("draw") or 0),
                    "lost": int(stats.get("lose") or 0),
                    "goalsFor": gf,
                    "goalsAgainst": ga,
                    "goalDifference": int(row.get("goalsDiff") or (gf - ga)),
                    "points": int(row.get("points") or 0),
                    "form": str(row.get("form") or ""),
                    # compatibility for epl table mapper in main.py
                    "team": str(team.get("name") or ""),
                }
            )
        return sorted(rows, key=lambda x: x.get("position", 0))

    def get_fixtures(self, code: str, days: int) -> List[Dict[str, Any]]:
        league_id = self._league_id(code)
        span = max(1, min(int(days), 60))
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=span)
        date_from = today.isoformat()
        date_to = end.isoformat()
        cache_key = self._fixtures_cache_key(code, date_from, date_to)
        cached = self._fixtures_cache_get(cache_key)
        if cached is not None:
            return cached
        payload = self._request(
            "/fixtures",
            {
                "league": league_id,
                "season": self.season,
                "from": date_from,
                "to": date_to,
            },
        )
        out: List[Dict[str, Any]] = []
        for item in payload.get("response", []) or []:
            fixture = item.get("fixture", {}) or {}
            teams = item.get("teams", {}) or {}
            home_team = teams.get("home", {}) or {}
            away_team = teams.get("away", {}) or {}
            out.append(
                {
                    "utcDate": fixture.get("date"),
                    "matchday": self._parse_matchday((item.get("league", {}) or {}).get("round")),
                    "competition": code,
                    "venue": str((fixture.get("venue") or {}).get("name") or "Home"),
                    "home": str(home_team.get("name") or ""),
                    "away": str(away_team.get("name") or ""),
                }
            )
        out.sort(key=lambda x: str(x.get("utcDate") or ""))
        return self._fixtures_cache_set(cache_key, out)

    def get_predictions(self, code: str, days: int = 14) -> List[Dict[str, Any]]:
        return []
