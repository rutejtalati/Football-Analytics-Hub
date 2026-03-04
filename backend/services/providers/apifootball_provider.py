from __future__ import annotations

import math
import os
import random
import threading
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from cachetools import TTLCache

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
        self.season = self._configured_or_inferred_season()
        self.standings_ttl_seconds = 1800
        self.fixtures_ttl_seconds = 600
        self.predictions_ttl_seconds = int(os.getenv("APIFOOTBALL_PREDICTIONS_TTL_SECONDS", "3600"))
        self.predictions_cache_size = int(os.getenv("APIFOOTBALL_PREDICTIONS_CACHE_SIZE", "100"))
        self.home_adv = float(os.getenv("HOME_ADV", "1.10"))
        self.max_goals = int(os.getenv("POISSON_MAX_GOALS", "6"))
        self.timeout_seconds = float(os.getenv("APIFOOTBALL_TIMEOUT_SECONDS", "10"))
        self._standings_cache: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
        self._fixtures_cache: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
        self._predictions_cache: TTLCache[str, List[Dict[str, Any]]] = TTLCache(
            maxsize=self.predictions_cache_size,
            ttl=self.predictions_ttl_seconds,
        )
        self._predictions_stale: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()
        self._logger = logging.getLogger(__name__)

    def _api_key(self) -> str:
        key = (os.getenv("APIFOOTBALL_API_KEY") or "").strip()
        if not key:
            raise ProviderError("APIFOOTBALL_API_KEY missing in environment.", status_code=503)
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
                timeout=self.timeout_seconds,
            )
        except requests.exceptions.Timeout as exc:
            raise ProviderError("API-Football timeout", status_code=503, upstream_status=504) from exc
        except requests.RequestException as exc:
            raise ProviderError(f"API-Football request failed: {str(exc)[:200]}", status_code=503) from exc

        if response.status_code != 200:
            if response.status_code == 429:
                raise ProviderError("API-Football rate limited (429)", status_code=503, upstream_status=429)
            raise ProviderError(
                f"API-Football error {response.status_code}: {response.text[:200]}",
                status_code=503,
                upstream_status=response.status_code,
            )
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise ProviderError("API-Football returned invalid JSON", status_code=503) from exc

    def _parse_matchday(self, round_text: Any) -> int:
        try:
            text = str(round_text or "")
            tail = text.split("-")[-1].strip()
            return int(tail) if tail.isdigit() else 0
        except Exception:
            return 0

    def _configured_or_inferred_season(self) -> int:
        configured = (os.getenv("APIFOOTBALL_SEASON") or "").strip()
        if configured:
            try:
                return int(configured)
            except ValueError:
                pass
        now = datetime.now(timezone.utc)
        return now.year if now.month >= 7 else now.year - 1

    def _season_candidates(self, date_from: datetime.date) -> List[int]:
        inferred = date_from.year if date_from.month >= 7 else date_from.year - 1
        candidates = [self.season, inferred, inferred - 1, self.season - 1, self.season + 1]
        out: List[int] = []
        for c in candidates:
            if c < 2010:
                continue
            if c not in out:
                out.append(c)
        return out

    def _cache_get(self, store: Dict[str, tuple[float, List[Dict[str, Any]]]], key: str) -> Optional[List[Dict[str, Any]]]:
        now = time.time()
        with self._cache_lock:
            row = store.get(key)
            if not row:
                return None
            expires_at, payload = row
            if now >= expires_at:
                store.pop(key, None)
                return None
            return payload

    def _cache_get_stale(self, store: Dict[str, tuple[float, List[Dict[str, Any]]]], key: str) -> Optional[List[Dict[str, Any]]]:
        with self._cache_lock:
            row = store.get(key)
            if not row:
                return None
            _expires_at, payload = row
            return payload

    def _cache_set(
        self,
        store: Dict[str, tuple[float, List[Dict[str, Any]]]],
        key: str,
        payload: List[Dict[str, Any]],
        ttl_seconds: int,
    ) -> List[Dict[str, Any]]:
        with self._cache_lock:
            store[key] = (time.time() + ttl_seconds, payload)
        return payload

    def _standings_cache_key(self, code: str, season: int) -> str:
        return f"{code}:{season}"

    def _fixtures_cache_key(self, code: str, season: int, date_from: str, date_to: str) -> str:
        return f"{code}:{season}:{date_from}:{date_to}"

    def _predictions_cache_key(self, code: str, days: int) -> str:
        return f"{code}:{days}"

    def _pred_cache_get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        with self._cache_lock:
            return self._predictions_cache.get(key)

    def _pred_cache_get_stale(self, key: str) -> Optional[List[Dict[str, Any]]]:
        with self._cache_lock:
            return self._predictions_stale.get(key)

    def _pred_cache_set(self, key: str, payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        with self._cache_lock:
            self._predictions_cache[key] = payload
            self._predictions_stale[key] = payload
        return payload

    def _fetch_standings_rows(self, code: str, season: int) -> List[Dict[str, Any]]:
        cache_key = self._standings_cache_key(code, season)
        cached = self._cache_get(self._standings_cache, cache_key)
        if cached is not None:
            return cached
        league_id = self._league_id(code)
        payload = self._request("/standings", {"league": league_id, "season": season})
        response_rows = payload.get("response", []) or []
        if not response_rows:
            return self._cache_set(self._standings_cache, cache_key, [], self.standings_ttl_seconds)
        league_info = response_rows[0].get("league", {}) or {}
        standings_groups = league_info.get("standings", []) or []
        if not standings_groups:
            return self._cache_set(self._standings_cache, cache_key, [], self.standings_ttl_seconds)

        out: List[Dict[str, Any]] = []
        for row in standings_groups[0] or []:
            team = row.get("team", {}) or {}
            stats_all = row.get("all", {}) or {}
            goals_all = stats_all.get("goals", {}) or {}
            gf = int(goals_all.get("for") or 0)
            ga = int(goals_all.get("against") or 0)
            out.append(
                {
                    "position": int(row.get("rank") or 0),
                    "teamName": str(team.get("name") or ""),
                    "teamShort": str(team.get("code") or team.get("name") or ""),
                    "playedGames": int(stats_all.get("played") or 0),
                    "won": int(stats_all.get("win") or 0),
                    "draw": int(stats_all.get("draw") or 0),
                    "lost": int(stats_all.get("lose") or 0),
                    "goalsFor": gf,
                    "goalsAgainst": ga,
                    "goalDifference": int(row.get("goalsDiff") or (gf - ga)),
                    "points": int(row.get("points") or 0),
                    "form": str(row.get("form") or ""),
                    "team": str(team.get("name") or ""),
                    "team_id": int(team.get("id") or 0),
                    "home_played": int((row.get("home", {}) or {}).get("played") or 0),
                    "home_gf": int((((row.get("home", {}) or {}).get("goals", {}) or {}).get("for") or 0)),
                    "home_ga": int((((row.get("home", {}) or {}).get("goals", {}) or {}).get("against") or 0)),
                    "away_played": int((row.get("away", {}) or {}).get("played") or 0),
                    "away_gf": int((((row.get("away", {}) or {}).get("goals", {}) or {}).get("for") or 0)),
                    "away_ga": int((((row.get("away", {}) or {}).get("goals", {}) or {}).get("against") or 0)),
                }
            )
        out_sorted = sorted(out, key=lambda x: x.get("position", 0))
        return self._cache_set(self._standings_cache, cache_key, out_sorted, self.standings_ttl_seconds)

    def _poisson_pmf(self, lam: float, k: int) -> float:
        if k < 0:
            return 0.0
        lam = max(0.01, float(lam))
        return math.exp(-lam) * (lam ** k) / math.factorial(k)

    def _poisson_outcome_metrics(self, lam_home: float, lam_away: float, max_goals: int = 10) -> Dict[str, Any]:
        p_home = [self._poisson_pmf(lam_home, i) for i in range(max_goals + 1)]
        p_away = [self._poisson_pmf(lam_away, j) for j in range(max_goals + 1)]
        p_home[-1] += max(0.0, 1.0 - sum(p_home))
        p_away[-1] += max(0.0, 1.0 - sum(p_away))

        home_win = 0.0
        draw = 0.0
        away_win = 0.0
        over25 = 0.0
        btts = 0.0
        best_prob = -1.0
        best_score = (0, 0)

        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p = p_home[i] * p_away[j]
                if p > best_prob:
                    best_prob = p
                    best_score = (i, j)
                if i > j:
                    home_win += p
                elif i == j:
                    draw += p
                else:
                    away_win += p
                if i + j >= 3:
                    over25 += p
                if i > 0 and j > 0:
                    btts += p

        expected_total = lam_home + lam_away
        outcome_probs = [home_win, draw, away_win]
        entropy = 0.0
        for p in outcome_probs:
            if p > 0:
                entropy -= p * math.log(p)

        return {
            "model_score": f"{best_score[0]}-{best_score[1]}",
            "home_win_prob": home_win,
            "draw_prob": draw,
            "away_win_prob": away_win,
            "over25_prob": over25,
            "btts_prob": btts,
            "expected_game_goals": expected_total,
            "outcome_uncertainty": entropy,
            "home_clean_sheet_prob": p_away[0],
            "away_clean_sheet_prob": p_home[0],
            "home_2plus_prob": sum(p_home[2:]),
            "away_2plus_prob": sum(p_away[2:]),
        }

    def _dixon_coles_tau(self, x: int, y: int, lam_home: float, lam_away: float, rho: float) -> float:
        if x == 0 and y == 0:
            return 1.0 - (lam_home * lam_away * rho)
        if x == 0 and y == 1:
            return 1.0 + (lam_home * rho)
        if x == 1 and y == 0:
            return 1.0 + (lam_away * rho)
        if x == 1 and y == 1:
            return 1.0 - rho
        return 1.0

    def _score_probs(self, lam_home: float, lam_away: float, rho: float, max_goals: int = 8) -> List[Tuple[int, int, float]]:
        out: List[Tuple[int, int, float]] = []
        total = 0.0
        for h in range(max_goals + 1):
            p_h = self._poisson_pmf(lam_home, h)
            for a in range(max_goals + 1):
                p = p_h * self._poisson_pmf(lam_away, a)
                p *= max(0.01, self._dixon_coles_tau(h, a, lam_home, lam_away, rho))
                out.append((h, a, p))
                total += p
        if total <= 0:
            return [(0, 0, 1.0)]
        return [(h, a, p / total) for (h, a, p) in out]

    def _sample_poisson(self, lam: float, rng: random.Random) -> int:
        lam = max(0.01, float(lam))
        if lam > 8.0:
            x = int(round(rng.gauss(lam, math.sqrt(lam))))
            return max(0, x)
        l = math.exp(-lam)
        k = 0
        p = 1.0
        while p > l:
            k += 1
            p *= rng.random()
        return k - 1

    def _monte_carlo_metrics(self, lam_home: float, lam_away: float, fixture_id: int, sims: int) -> Dict[str, float]:
        rng = random.Random(fixture_id or int(lam_home * 1000 + lam_away * 100))
        home_win = 0
        draw = 0
        away_win = 0
        over25 = 0
        btts = 0
        home_cs = 0
        away_cs = 0
        for _ in range(sims):
            h = self._sample_poisson(lam_home, rng)
            a = self._sample_poisson(lam_away, rng)
            if h > a:
                home_win += 1
            elif h == a:
                draw += 1
            else:
                away_win += 1
            if h + a >= 3:
                over25 += 1
            if h > 0 and a > 0:
                btts += 1
            if a == 0:
                home_cs += 1
            if h == 0:
                away_cs += 1
        denom = float(max(1, sims))
        return {
            "p_home_sim": home_win / denom,
            "p_draw_sim": draw / denom,
            "p_away_sim": away_win / denom,
            "over25_sim": over25 / denom,
            "btts_sim": btts / denom,
            "home_cs_sim": home_cs / denom,
            "away_cs_sim": away_cs / denom,
        }

    def _fetch_recent_results(self, code: str, season: int, target_matches: int) -> List[Dict[str, Any]]:
        league_id = self._league_id(code)
        out: List[Dict[str, Any]] = []
        for s in [season, season - 1]:
            payload = self._request(
                "/fixtures",
                {"league": league_id, "season": s, "status": "FT", "last": target_matches},
            )
            for item in payload.get("response", []) or []:
                fixture = item.get("fixture", {}) or {}
                teams = item.get("teams", {}) or {}
                goals = item.get("goals", {}) or {}
                home_team = teams.get("home", {}) or {}
                away_team = teams.get("away", {}) or {}
                home_goals = goals.get("home")
                away_goals = goals.get("away")
                if home_goals is None or away_goals is None:
                    continue
                out.append(
                    {
                        "fixture_id": int(fixture.get("id") or 0),
                        "utcDate": fixture.get("date"),
                        "home_team_id": int(home_team.get("id") or 0),
                        "away_team_id": int(away_team.get("id") or 0),
                        "home_goals": int(home_goals),
                        "away_goals": int(away_goals),
                    }
                )
            if len(out) >= target_matches:
                break
        out.sort(key=lambda m: str(m.get("utcDate") or ""), reverse=True)
        return out[:target_matches]

    def _estimate_rho(self, matches: List[Dict[str, Any]], avg_home_goals: float, avg_away_goals: float) -> float:
        if not matches:
            return -0.06
        observed = 0
        for m in matches:
            hg = int(m.get("home_goals") or 0)
            ag = int(m.get("away_goals") or 0)
            if (hg, ag) in {(0, 0), (1, 0), (0, 1), (1, 1)}:
                observed += 1
        observed_rate = observed / float(len(matches))

        # Baseline independent-Poisson low-score mass around league averages.
        baseline_probs = self._score_probs(avg_home_goals, avg_away_goals, rho=0.0, max_goals=3)
        baseline_rate = sum(p for h, a, p in baseline_probs if (h, a) in {(0, 0), (1, 0), (0, 1), (1, 1)})
        rho = (observed_rate - baseline_rate) * 0.9
        return max(-0.12, min(0.12, rho))

    def get_standings(self, code: str) -> List[Dict[str, Any]]:
        today = datetime.now(timezone.utc).date()
        last_error: Optional[ProviderError] = None
        for season in self._season_candidates(today):
            try:
                rows = self._fetch_standings_rows(code, season)
            except ProviderError as exc:
                last_error = exc
                continue
            if rows:
                self.season = season
                return rows
        if last_error:
            raise last_error
        raise ProviderError("No standings available for the selected league/season.", status_code=503)

    def get_fixtures(self, code: str, days: int) -> List[Dict[str, Any]]:
        league_id = self._league_id(code)
        span = max(1, min(int(days), 60))
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=span)
        date_from = today.isoformat()
        date_to = end.isoformat()
        last_error: Optional[ProviderError] = None
        for season in self._season_candidates(today):
            cache_key = self._fixtures_cache_key(code, season, date_from, date_to)
            cached = self._cache_get(self._fixtures_cache, cache_key)
            if cached is not None:
                self.season = season
                return cached
            try:
                payload = self._request(
                    "/fixtures",
                    {
                        "league": league_id,
                        "season": season,
                        "from": date_from,
                        "to": date_to,
                    },
                )
            except ProviderError as exc:
                last_error = exc
                continue
            out: List[Dict[str, Any]] = []
            for item in payload.get("response", []) or []:
                fixture = item.get("fixture", {}) or {}
                teams = item.get("teams", {}) or {}
                home_team = teams.get("home", {}) or {}
                away_team = teams.get("away", {}) or {}
                kickoff = fixture.get("date")
                matchday = self._parse_matchday((item.get("league", {}) or {}).get("round"))
                home_name = str(home_team.get("name") or "")
                away_name = str(away_team.get("name") or "")
                venue_name = str((fixture.get("venue") or {}).get("name") or "Home")
                status = str(((fixture.get("status") or {}).get("short")) or "")
                out.append(
                    {
                        "id": int(fixture.get("id") or 0),
                        "kickoff": kickoff,
                        "matchday": matchday,
                        "home_team": home_name,
                        "away_team": away_name,
                        "venue": venue_name,
                        "status": status,
                        "home_team_id": int(home_team.get("id") or 0),
                        "away_team_id": int(away_team.get("id") or 0),
                        "season": season,
                        # Backward-compatible keys for existing frontend fields.
                        "utcDate": kickoff,
                        "competition": code,
                        "home": home_name,
                        "away": away_name,
                    }
                )
            if not out:
                continue
            out.sort(key=lambda x: str(x.get("utcDate") or ""))
            self.season = season
            return self._cache_set(self._fixtures_cache, cache_key, out, self.fixtures_ttl_seconds)
        if last_error:
            raise last_error
        raise ProviderError("No fixtures available for the selected period.", status_code=503)

    def get_predictions(self, code: str, days: int = 14) -> List[Dict[str, Any]]:
        span = max(1, min(int(days), 60))
        cache_key = self._predictions_cache_key(code, span)
        cached = self._pred_cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            fixtures = self.get_fixtures(code, span)
            if not fixtures:
                self._logger.warning("Predictions skipped: no fixtures for code=%s days=%s", code, span)
                return self._pred_cache_set(cache_key, [])

            standings = self.get_standings(code)
            team_stats_by_id: Dict[int, Dict[str, float]] = {}
            team_stats_by_name: Dict[str, Dict[str, float]] = {}
            ga_values: List[float] = []
            for row in standings:
                team_name = str(row.get("teamName") or row.get("team") or "").strip()
                team_id = int(row.get("team_id") or 0)
                played = float(row.get("playedGames") or 0.0)
                if not team_name or played <= 0:
                    continue
                gfpg = float(row.get("goalsFor") or 0.0) / played
                gapg = float(row.get("goalsAgainst") or 0.0) / played
                stats = {"GFpg": gfpg, "GApg": gapg}
                team_stats_by_name[team_name.lower()] = stats
                if team_id > 0:
                    team_stats_by_id[team_id] = stats
                ga_values.append(gapg)

            if not ga_values:
                self._logger.warning("Predictions skipped: missing standings goals data for code=%s", code)
                return self._pred_cache_set(cache_key, [])

            league_gapg = sum(ga_values) / float(len(ga_values))
            if league_gapg <= 0:
                self._logger.warning("Predictions skipped: league_GApg invalid for code=%s", code)
                return self._pred_cache_set(cache_key, [])

            # Fallback prevents empty predictions when a team name/id mapping is missing.
            avg_gfpg = sum((v["GFpg"] for v in (team_stats_by_id.values() or team_stats_by_name.values())), 0.0) / max(
                1, len(team_stats_by_id) or len(team_stats_by_name)
            )
            avg_stats = {"GFpg": avg_gfpg, "GApg": league_gapg}

            max_goals = max(1, self.max_goals)
            out: List[Dict[str, Any]] = []
            for fx in fixtures:
                home = str(fx.get("home_team") or fx.get("home") or "").strip()
                away = str(fx.get("away_team") or fx.get("away") or "").strip()
                home_id = int(fx.get("home_team_id") or 0)
                away_id = int(fx.get("away_team_id") or 0)
                hs = team_stats_by_id.get(home_id) or team_stats_by_name.get(home.lower()) or avg_stats
                aw = team_stats_by_id.get(away_id) or team_stats_by_name.get(away.lower()) or avg_stats
                if not home or not away:
                    continue

                xg_home = self.home_adv * hs["GFpg"] * aw["GApg"] / max(league_gapg, 0.01)
                xg_away = aw["GFpg"] * hs["GApg"] / max(league_gapg, 0.01)
                xg_home = max(0.05, min(5.0, xg_home))
                xg_away = max(0.05, min(5.0, xg_away))

                p_home = 0.0
                p_draw = 0.0
                p_away = 0.0
                p_over_25 = 0.0
                p_btts = 0.0
                p_home_cs = 0.0
                p_away_cs = 0.0
                score_probs: List[Tuple[int, int, float]] = []
                for i in range(max_goals + 1):
                    pi = self._poisson_pmf(xg_home, i)
                    for j in range(max_goals + 1):
                        p = pi * self._poisson_pmf(xg_away, j)
                        score_probs.append((i, j, p))
                        if i > j:
                            p_home += p
                        elif i == j:
                            p_draw += p
                        else:
                            p_away += p
                        if i + j >= 3:
                            p_over_25 += p
                        if i >= 1 and j >= 1:
                            p_btts += p
                        if j == 0:
                            p_home_cs += p
                        if i == 0:
                            p_away_cs += p

                total_prob = p_home + p_draw + p_away
                if total_prob > 0:
                    p_home /= total_prob
                    p_draw /= total_prob
                    p_away /= total_prob
                    p_over_25 /= total_prob
                    p_btts /= total_prob
                    p_home_cs /= total_prob
                    p_away_cs /= total_prob

                score_probs.sort(key=lambda t: t[2], reverse=True)
                most_likely = f"{score_probs[0][0]}-{score_probs[0][1]}" if score_probs else "0-0"
                top3 = [{"score": f"{i}-{j}", "p": round(float(p), 6)} for i, j, p in score_probs[:3]]

                out.append(
                    {
                        "utcDate": fx.get("kickoff") or fx.get("utcDate"),
                        "matchday": int(fx.get("matchday") or 0),
                        "home": home,
                        "away": away,
                        "venue": fx.get("venue") or "Home",
                        "xg_home": round(float(xg_home), 4),
                        "xg_away": round(float(xg_away), 4),
                        "home_win": float(p_home),
                        "draw": float(p_draw),
                        "away_win": float(p_away),
                        "over_2_5": float(p_over_25),
                        "btts": float(p_btts),
                        "home_cs": float(p_home_cs),
                        "away_cs": float(p_away_cs),
                        "most_likely_score": most_likely,
                        "top_scorelines": top3,
                        "model": "poisson_v1",
                        # compatibility aliases
                        "p_home": float(p_home),
                        "p_draw": float(p_draw),
                        "p_away": float(p_away),
                        "score_mode": most_likely,
                        "over25": float(p_over_25),
                        "home_win_prob": float(p_home),
                        "draw_prob": float(p_draw),
                        "away_win_prob": float(p_away),
                        "over25_prob": float(p_over_25),
                    }
                )

            out.sort(key=lambda r: str(r.get("utcDate") or ""))
            return self._pred_cache_set(cache_key, out)
        except ProviderError:
            stale = self._pred_cache_get_stale(cache_key)
            if stale is not None:
                self._logger.warning("Serving stale predictions cache for key=%s", cache_key)
                return stale
            raise
        except Exception as exc:
            stale = self._pred_cache_get_stale(cache_key)
            if stale is not None:
                self._logger.warning("Serving stale predictions cache after error for key=%s", cache_key)
                return stale
            raise ProviderError(f"Predictions generation failed: {str(exc)[:200]}", status_code=503) from exc
