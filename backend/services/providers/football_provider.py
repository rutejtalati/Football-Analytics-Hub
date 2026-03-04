from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, Tuple

import requests
from backend.leagues import CURRENT_SEASON, LEAGUE_IDS
from backend.prediction import estimate_team_strengths, predict_fixture


@dataclass
class ProviderError(Exception):
    message: str
    status_code: int = 503
    upstream_status: Optional[int] = None

    def __str__(self) -> str:
        return self.message


class FootballProvider(Protocol):
    def get_fixtures(self, league_code: str, days: int) -> List[Dict[str, Any]]: ...
    def get_standings(self, league_code: str) -> List[Dict[str, Any]]: ...
    def get_predictions(self, league_code: str, days: int = 14) -> List[Dict[str, Any]]: ...


class _TTLCache:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = int(ttl_seconds)
        self._store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        row = self._store.get(key)
        if not row:
            return None
        expires_at, value = row
        if time.time() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def get_stale(self, key: str) -> Any:
        row = self._store.get(key)
        if not row:
            return None
        _expires_at, value = row
        return value

    def set(self, key: str, value: Any) -> Any:
        self._store[key] = (time.time() + self.ttl_seconds, value)
        return value


class _ThreadSafeTTLCache:
    def __init__(self):
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        now = time.time()
        with self._lock:
            row = self._store.get(key)
            if not row:
                return None
            expires_at, value = row
            if now >= expires_at:
                self._store.pop(key, None)
                return None
            return value

    def get_stale(self, key: str) -> Any:
        with self._lock:
            row = self._store.get(key)
            if not row:
                return None
            _expires_at, value = row
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> Any:
        with self._lock:
            self._store[key] = (time.time() + int(ttl_seconds), value)
        return value


class FootballDataProvider:
    def __init__(self):
        self.base_url = "https://api.football-data.org/v4"
        self.connect_timeout = float(os.getenv("FD_CONNECT_TIMEOUT_SECONDS", "2"))
        self.read_timeout = float(os.getenv("FD_READ_TIMEOUT_SECONDS", "4"))
        self.max_retries = int(os.getenv("FD_RETRIES", "0"))
        self.cache = _TTLCache(ttl_seconds=int(os.getenv("LEAGUE_CACHE_TTL_SECONDS", "600")))

    def _api_key(self) -> str:
        key = (os.getenv("APIFOOTBALL_API_KEY") or "").strip()
        if not key:
            raise ProviderError("APIFOOTBALL_API_KEY missing in Render env vars", status_code=500)
        return key

    def _request_json(self, path: str, cache_key: str) -> Dict[str, Any]:
        key = self._api_key()
        url = f"{self.base_url}{path}"
        headers = {
            "X-Auth-Token": key,
            "Accept": "application/json",
            "User-Agent": "FootballAnalyticsHub/1.0",
        }
        attempts = self.max_retries + 1
        last_error: Optional[ProviderError] = None
        for attempt in range(attempts):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                if response.status_code in (401, 403):
                    raise ProviderError(
                        "api-football auth failed (check API key / plan)",
                        status_code=502,
                        upstream_status=response.status_code,
                    )
                if response.status_code == 429:
                    raise ProviderError(
                        "api-football rate limited (429)",
                        status_code=503,
                        upstream_status=429,
                    )
                if response.status_code != 200:
                    raise ProviderError(
                        f"api-football error {response.status_code}: {response.text[:250]}",
                        status_code=502,
                        upstream_status=response.status_code,
                    )
                payload = response.json()
                self.cache.set(cache_key, payload)
                return payload
            except requests.exceptions.Timeout:
                last_error = ProviderError("api-football timeout", status_code=503, upstream_status=504)
            except requests.exceptions.ConnectionError:
                last_error = ProviderError(
                    "Network connection failed to API-Football",
                    status_code=503,
                    upstream_status=503,
                )
            except ValueError:
                last_error = ProviderError("api-football returned invalid JSON", status_code=502, upstream_status=502)
            except ProviderError as exc:
                last_error = exc

            if attempt < attempts - 1:
                time.sleep(0.25 * (attempt + 1))

        stale = self.cache.get_stale(cache_key)
        if stale is not None:
            return stale
        raise last_error or ProviderError("Unknown provider failure", status_code=503)

    def get_fixtures(self, league_code: str, days: int) -> List[Dict[str, Any]]:
        days = max(1, min(int(days), 60))
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=days)
        cache_key = f"fixtures:{league_code}:{today.isoformat()}:{end.isoformat()}"
        payload = self._request_json(
            f"/competitions/{league_code}/matches?dateFrom={today.isoformat()}&dateTo={end.isoformat()}",
            cache_key=cache_key,
        )
        fixtures: List[Dict[str, Any]] = []
        for match in payload.get("matches", []) or []:
            fixtures.append(
                {
                    "utcDate": match.get("utcDate"),
                    "matchday": int(match.get("matchday") or 0),
                    "competition": league_code,
                    "venue": (match.get("venue") or "Home"),
                    "home": str((match.get("homeTeam") or {}).get("name") or ""),
                    "away": str((match.get("awayTeam") or {}).get("name") or ""),
                    # Backward-compatible fields for existing prediction mapper
                    "match_id": int(match.get("id") or 0),
                    "utc_date": match.get("utcDate"),
                    "status": str(match.get("status") or ""),
                    "home_team_id": int(((match.get("homeTeam") or {}).get("id")) or 0),
                    "home_team_name": str((match.get("homeTeam") or {}).get("shortName") or (match.get("homeTeam") or {}).get("name") or ""),
                    "away_team_id": int(((match.get("awayTeam") or {}).get("id")) or 0),
                    "away_team_name": str((match.get("awayTeam") or {}).get("shortName") or (match.get("awayTeam") or {}).get("name") or ""),
                }
            )
        fixtures.sort(key=lambda x: str(x.get("utcDate") or ""))
        return fixtures

    def get_standings(self, league_code: str) -> List[Dict[str, Any]]:
        cache_key = f"standings:{league_code}"
        payload = self._request_json(f"/competitions/{league_code}/standings", cache_key=cache_key)
        standings = payload.get("standings", []) or []
        table_rows: List[Dict[str, Any]] = []
        for section in standings:
            if str(section.get("type", "")).upper() == "TOTAL":
                table_rows = section.get("table", []) or []
                break
        if not table_rows and standings:
            table_rows = standings[0].get("table", []) or []

        rows: List[Dict[str, Any]] = []
        for row in table_rows:
            team = row.get("team", {}) or {}
            gf = int(row.get("goalsFor") or 0)
            ga = int(row.get("goalsAgainst") or 0)
            rows.append(
                {
                    "position": int(row.get("position") or 0),
                    "teamName": str(team.get("name") or ""),
                    "teamShort": str(team.get("shortName") or team.get("tla") or team.get("name") or ""),
                    "playedGames": int(row.get("playedGames") or 0),
                    "won": int(row.get("won") or 0),
                    "draw": int(row.get("draw") or 0),
                    "lost": int(row.get("lost") or 0),
                    "points": int(row.get("points") or 0),
                    "goalsFor": gf,
                    "goalsAgainst": ga,
                    "goalDifference": int(row.get("goalDifference") if row.get("goalDifference") is not None else (gf - ga)),
                    # Backward-compatible fields used in ranking map path
                    "team": str(team.get("name") or ""),
                }
            )
        return sorted(rows, key=lambda x: x["position"])

    def get_predictions(self, league_code: str, days: int = 14) -> List[Dict[str, Any]]:
        standings = self.get_standings(league_code)
        fixtures = self.get_fixtures(league_code, int(days))
        strengths = estimate_team_strengths(standings)

        out: List[Dict[str, Any]] = []
        for fx in fixtures:
            home_id = int(fx.get("home_team_id") or 0)
            away_id = int(fx.get("away_team_id") or 0)
            pred = predict_fixture(home_id, away_id, strengths)
            if "xgH" in pred and "lambda_h" not in pred:
                pred["lambda_h"] = pred["xgH"]
            if "xgA" in pred and "lambda_a" not in pred:
                pred["lambda_a"] = pred["xgA"]
            out.append(
                {
                    "match_id": fx.get("match_id"),
                    "utc_date": fx.get("utc_date"),
                    "status": fx.get("status"),
                    "home_team_id": home_id,
                    "home_team_name": fx.get("home_team_name"),
                    "away_team_id": away_id,
                    "away_team_name": fx.get("away_team_name"),
                    "prediction": pred,
                }
            )
        return out


class APIFootballProvider:
    """
    Migration stub. Keeps route handlers untouched while allowing FOOTBALL_PROVIDER=api_football.
    """

    CODE_TO_KEY: Dict[str, str] = {
        "PL": "epl",
        "PD": "laliga",
        "SA": "seriea",
        "FL1": "ligue1",
    }
    BASE_URL = "https://v3.football.api-sports.io"

    def __init__(self):
        self.base_url = self.BASE_URL
        self.timeout_seconds = 6
        self.api_key = os.getenv("APIFOOTBALL_API_KEY")
        if not self.api_key:
            raise RuntimeError("APIFOOTBALL_API_KEY not set in environment variables")
        self.default_season = int(CURRENT_SEASON)
        self.standings_ttl_seconds = 1800
        self.fixtures_ttl_seconds = 600
        self.predictions_ttl_seconds = 600
        self.cache = _ThreadSafeTTLCache()

    def _league_meta(self, league_code: str) -> Dict[str, Any]:
        league_key = self.CODE_TO_KEY.get(league_code, "")
        league_id = LEAGUE_IDS.get(league_key) if league_key else None
        return {"league_id": league_id, "season": self.default_season}

    def _headers(self) -> Dict[str, str]:
        return {
            "x-apisports-key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "FootballAnalyticsHub/1.0",
        }

    def _request(self, path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        status_code, payload = self._request_with_status(path, params)
        if status_code != 200:
            return None
        return payload

    def _request_with_status(self, path: str, params: Dict[str, Any]) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        headers = self._headers()
        if not headers:
            return None, None
        try:
            response = requests.get(
                f"{self.base_url}{path}",
                headers=headers,
                params=params,
                timeout=self.timeout_seconds,
            )
            payload = response.json() if response.content else {}
            return int(response.status_code), payload
        except Exception:
            return None, None

    def _parse_matchday(self, round_text: Any) -> int:
        try:
            text = str(round_text or "")
            tail = text.split("-")[-1].strip()
            return int(tail) if tail.isdigit() else 0
        except Exception:
            return 0

    def _cache_key(self, kind: str, league_code: str, season: int, params: Dict[str, Any]) -> str:
        parts = [f"{k}={params[k]}" for k in sorted(params.keys())]
        return f"{kind}:{league_code}:{season}:{'&'.join(parts)}"

    def _to_prob(self, value: Any) -> float:
        try:
            s = str(value or "").strip().replace("%", "")
            if s == "":
                return 0.0
            return float(s) / 100.0
        except Exception:
            return 0.0

    def get_fixtures(self, league_code: str, days: int) -> List[Dict[str, Any]]:
        meta = self._league_meta(league_code)
        league_id = meta.get("league_id")
        if league_id is None:
            return []

        days = max(1, min(int(days), 60))
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=days)
        season = int(meta.get("season") or self.default_season)
        query_params = {
            "league": league_id,
            "season": season,
            "from": today.isoformat(),
            "to": end.isoformat(),
        }
        cache_key = self._cache_key("fixtures", league_code, season, query_params)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        payload = self._request(
            "/fixtures",
            query_params,
        )
        if payload is None:
            stale = self.cache.get_stale(cache_key)
            return stale if stale is not None else []
        fixtures_raw = payload.get("response", []) or []
        out: List[Dict[str, Any]] = []
        for item in fixtures_raw:
            fixture = item.get("fixture", {}) or {}
            teams = item.get("teams", {}) or {}
            home_team = teams.get("home", {}) or {}
            away_team = teams.get("away", {}) or {}
            out.append(
                {
                    "utcDate": fixture.get("date"),
                    "matchday": self._parse_matchday((item.get("league", {}) or {}).get("round")),
                    "competition": league_code,
                    "venue": str((fixture.get("venue") or {}).get("name") or "Home"),
                    "home": str(home_team.get("name") or ""),
                    "away": str(away_team.get("name") or ""),
                    "match_id": int(fixture.get("id") or 0),
                    "utc_date": fixture.get("date"),
                    "status": str(((fixture.get("status") or {}).get("short")) or ""),
                    "home_team_id": int(home_team.get("id") or 0),
                    "home_team_name": str(home_team.get("name") or ""),
                    "away_team_id": int(away_team.get("id") or 0),
                    "away_team_name": str(away_team.get("name") or ""),
                }
            )
        out.sort(key=lambda x: str(x.get("utcDate") or ""))
        return self.cache.set(cache_key, out, self.fixtures_ttl_seconds)

    def get_standings(self, league_code: str) -> List[Dict[str, Any]]:
        meta = self._league_meta(league_code)
        league_id = meta.get("league_id")
        if league_id is None:
            return []
        season = int(meta.get("season") or self.default_season)
        query_params = {"league": league_id, "season": season}
        cache_key = self._cache_key("standings", league_code, season, query_params)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        payload = self._request(
            "/standings",
            query_params,
        )
        if payload is None:
            stale = self.cache.get_stale(cache_key)
            return stale if stale is not None else []
        response_rows = payload.get("response", []) or []
        if not response_rows:
            return self.cache.set(cache_key, [], self.standings_ttl_seconds)
        league_info = response_rows[0].get("league", {}) or {}
        standings_groups = league_info.get("standings", []) or []
        if not standings_groups:
            return self.cache.set(cache_key, [], self.standings_ttl_seconds)
        table_rows = standings_groups[0] or []
        out: List[Dict[str, Any]] = []
        for row in table_rows:
            team = row.get("team", {}) or {}
            goals = row.get("all", {}) or {}
            gf = int((goals.get("goals") or {}).get("for") or 0)
            ga = int((goals.get("goals") or {}).get("against") or 0)
            out.append(
                {
                    "position": int(row.get("rank") or 0),
                    "teamName": str(team.get("name") or ""),
                    "teamShort": str(team.get("code") or team.get("name") or ""),
                    "playedGames": int((goals.get("played") or 0)),
                    "won": int((goals.get("win") or 0)),
                    "draw": int((goals.get("draw") or 0)),
                    "lost": int((goals.get("lose") or 0)),
                    "points": int(row.get("points") or 0),
                    "goalsFor": gf,
                    "goalsAgainst": ga,
                    "goalDifference": int(row.get("goalsDiff") or (gf - ga)),
                    "team": str(team.get("name") or ""),
                }
            )
        sorted_out = sorted(out, key=lambda x: x["position"])
        return self.cache.set(cache_key, sorted_out, self.standings_ttl_seconds)

    def get_predictions(self, league_code: str, days: int = 14) -> List[Dict[str, Any]]:
        meta = self._league_meta(league_code)
        season = int(meta.get("season") or self.default_season)
        cache_key = self._cache_key(
            "predictions",
            league_code,
            season,
            {"days": int(days)},
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            fixtures = self.get_fixtures(league_code, int(days))
            if not fixtures:
                stale = self.cache.get_stale(cache_key)
                return stale if stale is not None else []
            out: List[Dict[str, Any]] = []
            for fx in fixtures:
                match_id = int(fx.get("match_id") or 0)
                if match_id <= 0:
                    continue
                status_code, payload = self._request_with_status("/predictions", {"fixture": match_id})
                if status_code is not None and 400 <= status_code < 500:
                    stale = self.cache.get_stale(cache_key)
                    return stale if stale is not None else []
                if status_code != 200 or payload is None:
                    stale = self.cache.get_stale(cache_key)
                    return stale if stale is not None else []
                items = payload.get("response", []) or []
                if not items:
                    continue
                api_pred = items[0].get("predictions", {}) or {}
                percent = api_pred.get("percent", {}) or {}
                pred = {
                    "home_win": self._to_prob(percent.get("home")),
                    "draw": self._to_prob(percent.get("draw")),
                    "away_win": self._to_prob(percent.get("away")),
                    "lambda_h": 0.0,
                    "lambda_a": 0.0,
                    "xgH": 0.0,
                    "xgA": 0.0,
                }
                out.append(
                    {
                        "match_id": match_id,
                        "utc_date": fx.get("utc_date"),
                        "status": fx.get("status"),
                        "home_team_id": int(fx.get("home_team_id") or 0),
                        "home_team_name": fx.get("home_team_name"),
                        "away_team_id": int(fx.get("away_team_id") or 0),
                        "away_team_name": fx.get("away_team_name"),
                        "prediction": pred,
                    }
                )
            return self.cache.set(cache_key, out, self.predictions_ttl_seconds)
        except Exception:
            stale = self.cache.get_stale(cache_key)
            return stale if stale is not None else []


def get_provider() -> FootballProvider:
    provider_name = (os.getenv("FOOTBALL_PROVIDER") or "").strip().lower()
    if provider_name == "apifootball":
        from .apifootball_provider import ApiFootballProvider

        return ApiFootballProvider()
    return FootballDataProvider()
