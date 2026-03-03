from __future__ import annotations

from dotenv import load_dotenv

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from fpl_client import fetch_bootstrap, fetch_fixtures, get_next_gw
from understat_client import fetch_understat_league_players, fetch_understat_league_teams
from model import (
    STATUS_MAP,
    appearance_probability,
    expected_points_if_appears,
    match_understat_player,
    xg_xa_per90,
)

import requests
from leagues import list_leagues
from league_client import fetch_fixtures as fetch_league_fixtures, fetch_standings as fetch_league_standings
from prediction import estimate_team_strengths, predict_fixture

BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BACKEND_DIR / ".env")

app = FastAPI(title="FPL Predicted Points API", version="2.0.0")

origins = [
    "https://rutejtalati.github.io",
    "https://rutejtalati.github.io/Football-Analytics-Hub",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UNDERSTAT_LEAGUE = os.environ.get("UNDERSTAT_LEAGUE", "EPL")
UNDERSTAT_SEASON = os.environ.get("UNDERSTAT_SEASON", "2025")
UNDERSTAT_TTL_SECONDS = int(os.environ.get("UNDERSTAT_TTL_SECONDS", str(24 * 3600)))

FPL_BASE = "https://fantasy.premierleague.com/api"
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
FALLBACK_STANDINGS_PATH = Path(__file__).with_name("standings_fallback.json")
LEAGUE_CODE_MAP = {
    "PL": "PL",
    "EPL": "PL",
    "PD": "PD",
    "LALIGA": "PD",
    "SA": "SA",
    "SERIEA": "SA",
    "FL1": "FL1",
    "LIGUE1": "FL1",
}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """
    <html>
      <head>
        <title>FPL API</title>
        <meta charset="utf-8" />
      </head>
      <body style="font-family: Arial; padding: 20px;">
        <h2>FPL Predicted Points API</h2>
        <p>Backend is running ✅</p>
        <ul>
          <li><a href="/api/health">/api/health</a></li>
          <li><a href="/api/players?gws=3">/api/players?gws=3</a></li>
          <li><a href="/api/best_team?gws=3">/api/best_team?gws=3</a></li>
          <li>/api/squad?team_id=YOUR_TEAM_ID</li>
        </ul>
        <p>Frontend (Vite) usually runs at <b>http://localhost:5173</b></p>
      </body>
    </html>
    """


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/leagues")
def api_leagues() -> Dict[str, Any]:
    return {"leagues": list_leagues()}


def _normalize_league_code(league_code: str) -> str:
    code_raw = (league_code or "").upper().strip()
    code = LEAGUE_CODE_MAP.get(code_raw)
    if not code:
        raise HTTPException(status_code=400, detail="Unsupported league code.")
    return code


def _get_fd_key(failure_label: str) -> str:
    key = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Failed to fetch {failure_label}: FOOTBALL_DATA_API_KEY is not set. "
                "Set FOOTBALL_DATA_API_KEY in backend/.env and restart the backend."
            ),
        )
    return key


def _fd_get(path: str, key: str, failure_label: str) -> Dict[str, Any]:
    url = f"{FOOTBALL_DATA_BASE}{path}"
    try:
        resp = requests.get(url, headers={"X-Auth-Token": key}, timeout=20)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Failed to fetch {failure_label}: could not reach football-data.org. "
                "Check FOOTBALL_DATA_API_KEY in backend/.env and your network."
            ),
        ) from exc
    if resp.status_code != 200:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Failed to fetch {failure_label}: football-data.org returned HTTP {resp.status_code}. "
                "Verify FOOTBALL_DATA_API_KEY in backend/.env."
            ),
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch {failure_label}: football-data.org returned invalid JSON.",
        ) from exc


def _extract_total_standings_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    standings = payload.get("standings", []) or []
    table_rows: List[Dict[str, Any]] = []
    for s in standings:
        if str(s.get("type", "")).upper() == "TOTAL":
            table_rows = s.get("table", []) or []
            break
    if not table_rows and standings:
        table_rows = standings[0].get("table", []) or []
    return table_rows


def _normalize_standings_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in _extract_total_standings_rows(payload):
        team = r.get("team", {}) or {}
        goals_for = _to_int(r.get("goalsFor"), 0)
        goals_against = _to_int(r.get("goalsAgainst"), 0)
        rows.append(
            {
                "position": _to_int(r.get("position"), 0),
                "teamName": str(team.get("name") or ""),
                "teamShort": str(team.get("shortName") or team.get("tla") or team.get("name") or ""),
                "playedGames": _to_int(r.get("playedGames"), 0),
                "won": _to_int(r.get("won"), 0),
                "draw": _to_int(r.get("draw"), 0),
                "lost": _to_int(r.get("lost"), 0),
                "points": _to_int(r.get("points"), 0),
                "goalsFor": goals_for,
                "goalsAgainst": goals_against,
                "goalDifference": _to_int(r.get("goalDifference"), goals_for - goals_against),
            }
        )
    return sorted(rows, key=lambda x: x.get("position", 0))


@app.get("/api/league/{league}/fixtures")
def api_league_fixtures(league: str, days: int = Query(default=14, ge=1, le=60)) -> Dict[str, Any]:
    code = _normalize_league_code(league)
    key = _get_fd_key("fixtures")
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=int(days))
    payload = _fd_get(
        f"/competitions/{code}/matches?dateFrom={today.isoformat()}&dateTo={end.isoformat()}",
        key,
        "fixtures",
    )
    fixtures: List[Dict[str, Any]] = []
    for match in payload.get("matches", []) or []:
        fixtures.append(
            {
                "utcDate": match.get("utcDate"),
                "matchday": _to_int(match.get("matchday"), 0),
                "competition": code,
                "venue": (match.get("venue") or "Home"),
                "home": str((match.get("homeTeam") or {}).get("name") or ""),
                "away": str((match.get("awayTeam") or {}).get("name") or ""),
            }
        )
    fixtures.sort(key=lambda x: str(x.get("utcDate") or ""))
    return {"league": code, "fixtures": fixtures}


@app.get("/api/league/{league_code}/table")
def api_league_table(league_code: str) -> Dict[str, Any]:
    code = _normalize_league_code(league_code)
    key = _get_fd_key("standings")
    payload = _fd_get(f"/competitions/{code}/standings", key, "standings")
    out_rows = _normalize_standings_rows(payload)
    return {
        "league": code,
        "updated": datetime.now(timezone.utc).isoformat(),
        "table": out_rows,
        "source": "football-data.org",
    }


@app.get("/api/league/{league}/standings")
def api_league_standings(league: str) -> Dict[str, Any]:
    code = _normalize_league_code(league)
    key = _get_fd_key("standings")
    payload = _fd_get(f"/competitions/{code}/standings", key, "standings")
    return {"league": code, "standings": _normalize_standings_rows(payload)}


@app.get("/api/league/{code}/predictions")
def api_league_predictions(code: str, days: int = Query(default=14, ge=1, le=60)) -> Dict[str, Any]:
    comp_id = _normalize_league_code(code)
    _get_fd_key("predictions")
    try:
        standings = fetch_league_standings(comp_id)
        fixtures = fetch_league_fixtures(comp_id, days=days)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch league data: {e}") from e

    strengths = estimate_team_strengths(standings)
    out = []
    for fx in fixtures:
        home_id = _to_int(fx.get("home_team_id"))
        away_id = _to_int(fx.get("away_team_id"))
        pred = predict_fixture(home_id, away_id, strengths)
        # TODO: remove lambda_* once frontend is updated.
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
    return {
        "league": {"code": comp_id, "name": comp_id, "competition_id": comp_id},
        "days": days,
        "predictions": out,
    }


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def _to_int(x: Any, default: int = 0) -> int:
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except Exception:
        return default


def build_fixture_difficulty(
    fixtures: List[Dict[str, Any]], gws: List[int]
) -> Dict[int, Dict[int, float]]:
    """
    gw -> { team_id -> difficulty }
    """
    out: Dict[int, Dict[int, float]] = {}
    for gw in gws:
        team_diff: Dict[int, float] = {}
        for f in fixtures:
            if f.get("event") != gw:
                continue
            th = _to_int(f.get("team_h"))
            ta = _to_int(f.get("team_a"))
            team_diff[th] = _to_float(f.get("team_h_difficulty"), 3.0)
            team_diff[ta] = _to_float(f.get("team_a_difficulty"), 3.0)
        out[gw] = team_diff
    return out


def build_next_opponent_map(
    fixtures: List[Dict[str, Any]], next_gw: int, team_id_to_short: Dict[int, str]
) -> Dict[int, Dict[str, Any]]:
    """
    team_id -> { opponent: "ARS", is_home: bool, fixture_id: int }
    If blank gameweek, team won't exist in map.
    """
    out: Dict[int, Dict[str, Any]] = {}
    for f in fixtures:
        if f.get("event") != next_gw:
            continue
        th = _to_int(f.get("team_h"))
        ta = _to_int(f.get("team_a"))
        fid = _to_int(f.get("id"))
        out[th] = {"opponent": team_id_to_short.get(ta, ""), "is_home": True, "fixture_id": fid}
        out[ta] = {"opponent": team_id_to_short.get(th, ""), "is_home": False, "fixture_id": fid}
    return out


def fetch_entry_picks(entry_id: int, event: int) -> Dict[str, Any]:
    url = f"{FPL_BASE}/entry/{entry_id}/event/{event}/picks/"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def normalize_position(p: Dict[str, Any]) -> str:
    raw = str(p.get("position") or p.get("pos") or "").strip().upper()
    if raw in {"GK", "GKP", "GOALKEEPER"}:
        return "GK"
    if raw in {"DEF", "DEFENDER"}:
        return "DEF"
    if raw in {"MID", "MIDFIELDER"}:
        return "MID"
    if raw in {"FWD", "FORWARD", "ATT", "STR"}:
        return "FWD"
    et = p.get("element_type")
    if et in {1, "1"}:
        return "GK"
    if et in {2, "2"}:
        return "DEF"
    if et in {3, "3"}:
        return "MID"
    if et in {4, "4"}:
        return "FWD"
    return raw or "UNK"


def projected_score(p: Dict[str, Any], start_gw: int, mode: str, apply_prob: bool) -> float:
    if mode == "single":
        v = p.get(f"pts_gw{start_gw}")
        base = _to_float(v, _to_float(p.get("pts_next_sum"), 0.0))
    elif mode == "next5":
        vals = [_to_float(p.get(f"pts_gw{start_gw + i}"), 0.0) for i in range(5)]
        base = sum(vals) if any(vals) else _to_float(p.get("pts_rest"), 0.0)
    else:
        vals = [_to_float(p.get(f"pts_gw{start_gw + i}"), 0.0) for i in range(4)]
        base = sum(vals) if any(vals) else _to_float(p.get("pts_rest"), 0.0)

    if apply_prob:
        return base * _to_float(p.get("prob_appear"), 0.0)
    return base


def _formation_options() -> List[Tuple[int, int, int]]:
    out: List[Tuple[int, int, int]] = []
    for d in range(3, 6):
        for m in range(3, 6):
            for f in range(1, 4):
                if d + m + f == 10:
                    out.append((d, m, f))
    return out


def _rows_by_pos(xi: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    rows = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in xi:
        pos = normalize_position(p)
        if pos in rows:
            rows[pos].append(p)
    return rows


def optimize_xi_from_pool(
    players: List[Dict[str, Any]],
    start_gw: int,
    mode: str = "single",
    apply_prob: bool = True,
    max_from_team: int = 3,
    allowed_ids: Optional[set[int]] = None,
) -> Dict[str, Any]:
    """
    ILP-style constrained optimizer (deterministic exhaustive over legal formations + pruned candidates).
    Enforces: XI=11, GK=1, DEF 3..5, MID 3..5, FWD 1..3, max 3 per club.
    """
    pool: List[Dict[str, Any]] = []
    for p in players:
        pid = _to_int(p.get("id"))
        if allowed_ids is not None and pid not in allowed_ids:
            continue
        cp = dict(p)
        cp["position"] = normalize_position(cp)
        cp["_score"] = projected_score(cp, start_gw, mode, apply_prob)
        pool.append(cp)

    if not pool:
        return {"error": "No players available for optimization."}

    by: Dict[str, List[Dict[str, Any]]] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in sorted(pool, key=lambda x: (x["_score"], x.get("value_rest", 0.0)), reverse=True):
        pos = p["position"]
        if pos in by:
            by[pos].append(p)

    if not by["GK"]:
        return {"error": "No GK available."}
    if len(by["DEF"]) < 3 or len(by["MID"]) < 3 or len(by["FWD"]) < 1:
        return {"error": "Insufficient players by position."}

    # Candidate pruning for speed while keeping high quality.
    by["GK"] = by["GK"][:12]
    by["DEF"] = by["DEF"][:40]
    by["MID"] = by["MID"][:40]
    by["FWD"] = by["FWD"][:24]

    best: Optional[Dict[str, Any]] = None
    formations = _formation_options()

    def pick_top_with_team_cap(cands: List[Dict[str, Any]], need: int, team_counts: Dict[str, int]) -> Optional[List[Dict[str, Any]]]:
        out: List[Dict[str, Any]] = []
        local_team = dict(team_counts)
        for p in cands:
            t = str(p.get("team", ""))
            if local_team.get(t, 0) >= max_from_team:
                continue
            out.append(p)
            local_team[t] = local_team.get(t, 0) + 1
            if len(out) == need:
                return out
        return None

    for gk in by["GK"]:
        base_team_counts: Dict[str, int] = {str(gk.get("team", "")): 1}
        for d, m, f in formations:
            defs = pick_top_with_team_cap(by["DEF"], d, base_team_counts)
            if defs is None:
                continue
            team_after_def = dict(base_team_counts)
            for p in defs:
                t = str(p.get("team", ""))
                team_after_def[t] = team_after_def.get(t, 0) + 1

            mids = pick_top_with_team_cap(by["MID"], m, team_after_def)
            if mids is None:
                continue
            team_after_mid = dict(team_after_def)
            for p in mids:
                t = str(p.get("team", ""))
                team_after_mid[t] = team_after_mid.get(t, 0) + 1

            fwds = pick_top_with_team_cap(by["FWD"], f, team_after_mid)
            if fwds is None:
                continue

            xi = [gk] + defs + mids + fwds
            score = sum(_to_float(p.get("_score"), 0.0) for p in xi)
            if best is None or score > best["projected"]:
                best = {
                    "xi": xi,
                    "formation": f"{d}-{m}-{f}",
                    "projected": float(score),
                    "rows_by_pos": _rows_by_pos(xi),
                }

    if best is None:
        return {"error": "Could not satisfy XI constraints."}
    return best


def team_count_map(players: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in players:
        t = str(p.get("team", ""))
        counts[t] = counts.get(t, 0) + 1
    return counts


class TransferSuggestionRequest(BaseModel):
    squad_ids: List[int] = Field(default_factory=list, min_length=15, max_length=15)
    bank: float = 0.0
    free_transfers: int = Field(default=1, ge=0, le=5)
    hit_cost: float = Field(default=4.0, ge=0.0)
    horizon: int = Field(default=1, ge=1, le=5)
    apply_prob: bool = True


@app.get("/api/players")
def api_players(
    gws: int = Query(default=3, ge=1, le=8),
    include_with_prob: bool = Query(default=True),
    start_gw: Optional[int] = Query(default=None),
) -> Dict[str, Any]:
    bootstrap = fetch_bootstrap()
    fixtures = fetch_fixtures()
    next_gw = get_next_gw(bootstrap)

    if start_gw is None:
        start_gw = next_gw

    # Try Understat; if it fails, continue with FPL-only proxy
    try:
        under_players = fetch_understat_league_players(
            league=UNDERSTAT_LEAGUE,
            season=UNDERSTAT_SEASON,
            ttl_seconds=UNDERSTAT_TTL_SECONDS,
        )
        _ = fetch_understat_league_teams(
            league=UNDERSTAT_LEAGUE,
            season=UNDERSTAT_SEASON,
            ttl_seconds=UNDERSTAT_TTL_SECONDS,
        )
        understat_ok = True
    except Exception:
        under_players = []
        understat_ok = False

    # lookups
    team_id_to_short = {int(t["id"]): t["short_name"] for t in bootstrap["teams"]}
    elem_type_to_pos = {int(p["id"]): p["singular_name_short"] for p in bootstrap["element_types"]}

    gw_list = list(range(int(start_gw), int(start_gw) + int(gws)))
    fdr_map = build_fixture_difficulty(fixtures, gw_list)

    # next opponent for NEXT_GW specifically (not start_gw)
    next_opp_map = build_next_opponent_map(fixtures, next_gw, team_id_to_short)

    rows: List[Dict[str, Any]] = []
    for p in bootstrap["elements"]:
        pid = _to_int(p.get("id"))
        team_id = _to_int(p.get("team"))
        team_short = team_id_to_short.get(team_id, "")
        pos = elem_type_to_pos.get(_to_int(p.get("element_type")), "")
        pos_norm = normalize_position({"position": pos, "element_type": p.get("element_type")})

        player_name = f"{p.get('first_name','').strip()} {p.get('second_name','').strip()}".strip()
        cost = _to_float(p.get("now_cost")) / 10.0

        status = str(p.get("status", ""))
        availability_text, status_factor = STATUS_MAP.get(status, ("Unknown", 0.5))

        # FPL "chance_of_playing_next_round" is percent or null
        chance_play = p.get("chance_of_playing_next_round", None)
        chance_play = None if chance_play is None else _to_float(chance_play)

        # minutes / appearance proxy
        minutes = _to_float(p.get("minutes", 0.0))
        # crude appearances proxy to avoid division-by-zero
        apps_proxy = max(1.0, minutes / 75.0)
        minutes_per_game = minutes / apps_proxy

        prob_appear = appearance_probability(chance_play, minutes_per_game, float(status_factor))

        # Understat match
        us_row = match_understat_player(player_name, team_short, under_players) if under_players else None
        if us_row:
            xg90, xa90 = xg_xa_per90(us_row)
            mins = _to_float(us_row.get("minutes", 0.0))
            conf = 1.0 if mins >= 300 else 0.7
        else:
            xg90, xa90, conf = 0.0, 0.0, 0.0

        # Fallback when no Understat match (use ICT index as weak proxy)
        ict_index = _to_float(p.get("ict_index", 0.0))
        ict_xgi90 = max(0.0, min(0.6, ict_index / 200.0))
        if conf <= 0.0:
            xg90 = ict_xgi90 * 0.6
            xa90 = ict_xgi90 * 0.4

        # FPL stats you requested
        total_points = _to_int(p.get("total_points", 0))
        selected_pct = _to_float(p.get("selected_by_percent", 0.0))
        transfers_in_gw = _to_int(p.get("transfers_in_event", 0))
        transfers_out_gw = _to_int(p.get("transfers_out_event", 0))

        # next opponent info
        opp_info = next_opp_map.get(team_id, None)
        next_opp = opp_info["opponent"] if opp_info else ""
        is_home = bool(opp_info["is_home"]) if opp_info else None

        row: Dict[str, Any] = {
            "id": pid,
            "player_name": player_name,
            "team": team_short,
            "position": pos_norm,
            "position_norm": pos_norm,
            "element_type": _to_int(p.get("element_type")),
            "cost": cost,
            "availability_text": availability_text,
            "chance_play_pct": chance_play,   # may be None
            "prob_appear": float(prob_appear),

            "minutes_per_game": float(minutes_per_game),
            "form": _to_float(p.get("form", 0.0)),
            "merit": _to_float(p.get("value_form", 0.0)),  # not true "merit", but useful proxy

            "xg90": float(xg90),
            "xa90": float(xa90),

            "points_so_far": total_points,
            "selected_pct": float(selected_pct),
            "transfers_in_gw": transfers_in_gw,
            "transfers_out_gw": transfers_out_gw,

            "next_opponent": next_opp,
            "next_is_home": is_home,
        }

        # per-gw projections
        pts_rest_sum = 0.0
        val_rest_sum = 0.0
        pts_next_sum = 0.0

        for gw in gw_list:
            fdr = _to_float(fdr_map.get(gw, {}).get(team_id, 3.0))
            pts = expected_points_if_appears(
                pos=pos_norm,
                minutes_per_game=minutes_per_game,
                xg90=xg90,
                xa90=xa90,
                fdr=fdr,
            )
            pts_float = float(pts)
            row[f"fdr_gw{gw}"] = float(fdr)
            row[f"pts_gw{gw}"] = pts_float
            row[f"val_gw{gw}"] = float(pts_float / cost) if cost > 0 else 0.0

            pts_rest_sum += pts_float
            val_rest_sum += float(pts_float / cost) if cost > 0 else 0.0

            if include_with_prob:
                wp = float(pts_float * prob_appear)
                row[f"with_prob_gw{gw}"] = wp
                pts_next_sum += wp
            else:
                pts_next_sum += pts_float

        row["pts_rest"] = float(pts_rest_sum)
        row["value_rest"] = float(val_rest_sum)
        row["pts_next_sum"] = float(pts_next_sum)

        rows.append(row)

    # default sort: best near-term
    rows.sort(key=lambda r: r.get("pts_next_sum", 0.0), reverse=True)

    return {
        "next_gw": next_gw,
        "start_gw": start_gw,
        "gws": gws,
        "include_with_prob": include_with_prob,
        "understat_ok": understat_ok,
        "players": rows,
    }


@app.get("/api/squad")
def api_squad(team_id: int = Query(..., ge=1)) -> Dict[str, Any]:
    """
    Loads last-deadline squad from FPL entry ID.
    Uses event = next_gw - 1.
    """
    bootstrap = fetch_bootstrap()
    next_gw = get_next_gw(bootstrap)
    last_gw = max(1, next_gw - 1)

    data = fetch_entry_picks(team_id, last_gw)
    picks = data.get("picks", [])

    # Return just element IDs + captain flags etc (frontend can join to players list)
    simplified = [
        {
            "element": int(p.get("element")),
            "multiplier": int(p.get("multiplier", 1)),
            "is_captain": bool(p.get("is_captain", False)),
            "is_vice_captain": bool(p.get("is_vice_captain", False)),
            "position": int(p.get("position", 0)),
        }
        for p in picks
    ]

    return {
        "team_id": team_id,
        "event": last_gw,
        "picks": simplified,
        "note": "This is last-deadline squad (event = next_gw-1).",
    }


def _norm_team_name(name: str) -> str:
    t = (name or "").upper().replace(" FC", "").replace("AFC ", "").replace("&", "AND")
    t = t.replace("-", " ").replace(".", " ").strip()
    return " ".join(t.split())


def _bootstrap_name_to_code() -> Dict[str, str]:
    bootstrap = fetch_bootstrap()
    out: Dict[str, str] = {}
    for t in bootstrap.get("teams", []):
        short = str(t.get("short_name", "")).strip().upper()
        name = _norm_team_name(str(t.get("name", "")))
        if short:
            out[name] = short
    # common aliases
    out["SPURS"] = "TOT"
    out["MANCHESTER UTD"] = "MUN"
    out["MANCHESTER UNITED"] = "MUN"
    out["MANCHESTER CITY"] = "MCI"
    out["NOTTINGHAM FOREST"] = "NFO"
    out["WOLVERHAMPTON WANDERERS"] = "WOL"
    out["LEICESTER CITY"] = "LEI"
    out["SHEFFIELD UNITED"] = "SHU"
    out["LUTON TOWN"] = "LUT"
    out["IPSWICH TOWN"] = "IPS"
    return out


def _read_fallback_ranks() -> Dict[str, int]:
    if not FALLBACK_STANDINGS_PATH.exists():
        return {}
    try:
        data = json.loads(FALLBACK_STANDINGS_PATH.read_text(encoding="utf-8"))
        ranks = data.get("ranks", {})
        return {str(k).upper(): _to_int(v) for k, v in ranks.items() if _to_int(v) > 0}
    except Exception:
        return {}


@app.get("/api/epl_table")
def api_epl_table() -> Dict[str, Any]:
    key = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
    name_map = _bootstrap_name_to_code()
    fallback = {
        "season": "fallback",
        "updated": datetime.now(timezone.utc).isoformat(),
        "source": "fallback",
        "ranks": _read_fallback_ranks(),
    }
    if key:
        try:
            resp = requests.get(
                f"{FOOTBALL_DATA_BASE}/competitions/PL/standings",
                headers={"X-Auth-Token": key},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            standings = data.get("standings", [])
            table = standings[0].get("table", []) if standings else []
            ranks: Dict[str, int] = {}
            for row in table:
                team_name = _norm_team_name(str(row.get("team", {}).get("name", "")))
                code = name_map.get(team_name)
                if not code:
                    continue
                ranks[code] = _to_int(row.get("position"))
            if ranks:
                return {
                    "season": str(data.get("season", {}).get("id", "live")),
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "source": "football-data.org",
                    "ranks": ranks,
                }
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            short = f"football-data request failed: {status}" if status is not None else "football-data request failed"
            fallback["error"] = short
            return fallback

    return fallback


def _player_brief(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _to_int(p.get("id")),
        "name": p.get("player_name", ""),
        "team": p.get("team", ""),
        "pos": normalize_position(p),
        "cost": _to_float(p.get("cost"), 0.0),
    }


def _candidate_in_pool(
    all_players: List[Dict[str, Any]],
    current_ids: set[int],
    pos: str,
    start_gw: int,
    mode: str,
    apply_prob: bool,
    limit: int = 24,
) -> List[Dict[str, Any]]:
    cands = []
    for p in all_players:
        pid = _to_int(p.get("id"))
        if pid in current_ids:
            continue
        if normalize_position(p) != pos:
            continue
        cp = dict(p)
        cp["_score"] = projected_score(cp, start_gw, mode, apply_prob)
        cands.append(cp)
    cands.sort(key=lambda x: x["_score"], reverse=True)
    return cands[:limit]


@app.post("/api/transfer_suggestions")
def api_transfer_suggestions(req: TransferSuggestionRequest) -> Dict[str, Any]:
    payload = api_players(gws=8, include_with_prob=True, start_gw=None)
    players = payload["players"]
    next_gw = payload["next_gw"]
    mode = "single" if req.horizon == 1 else "next5"
    squad_id_set = {int(x) for x in req.squad_ids}

    by_id = {_to_int(p.get("id")): p for p in players}
    squad = [by_id[i] for i in req.squad_ids if i in by_id]
    if len(squad) != 15:
        return {"error": "Could not resolve all squad_ids against players dataset."}

    base = optimize_xi_from_pool(
        players=squad,
        start_gw=next_gw,
        mode=mode,
        apply_prob=req.apply_prob,
        max_from_team=3,
        allowed_ids=None,
    )
    if base.get("error"):
        return {"error": base["error"]}
    baseline_projected = _to_float(base.get("projected"), 0.0)

    current_total = sum(_to_float(p.get("cost"), 0.0) for p in squad)
    budget_cap = current_total + _to_float(req.bank, 0.0)

    squad_scored = []
    for p in squad:
        cp = dict(p)
        cp["_score"] = projected_score(cp, next_gw, mode, req.apply_prob)
        squad_scored.append(cp)
    squad_scored.sort(key=lambda x: x["_score"])

    outs = squad_scored[:8]
    suggestions: List[Dict[str, Any]] = []

    def evaluate_candidate(new_squad: List[Dict[str, Any]], transfer_count: int, out_players: List[Dict[str, Any]], in_players: List[Dict[str, Any]], kind: str) -> None:
        if len(new_squad) != 15:
            return
        counts = team_count_map(new_squad)
        if any(v > 3 for v in counts.values()):
            return
        cost = sum(_to_float(p.get("cost"), 0.0) for p in new_squad)
        if cost > budget_cap + 1e-9:
            return

        xi = optimize_xi_from_pool(
            players=new_squad,
            start_gw=next_gw,
            mode=mode,
            apply_prob=req.apply_prob,
            max_from_team=3,
        )
        if xi.get("error"):
            return
        proj = _to_float(xi.get("projected"), 0.0)
        hit_penalty = max(0, transfer_count - int(req.free_transfers)) * _to_float(req.hit_cost, 4.0)
        net = proj - hit_penalty
        gain = net - baseline_projected
        suggestions.append(
            {
                "type": kind,
                "transfers_out": [_player_brief(p) for p in out_players],
                "transfers_in": [_player_brief(p) for p in in_players],
                "projected": round(net, 3),
                "gain": round(gain, 3),
            }
        )

    evaluate_candidate(squad, 0, [], [], "HOLD")

    # 1 FT
    for out_p in outs:
        pos = normalize_position(out_p)
        for in_p in _candidate_in_pool(players, squad_id_set, pos, next_gw, mode, req.apply_prob, limit=18):
            new_squad = [p for p in squad if _to_int(p.get("id")) != _to_int(out_p.get("id"))] + [in_p]
            evaluate_candidate(new_squad, 1, [out_p], [in_p], "1FT")

    # 2 FT (beam: top 40 from 1FT raw gain)
    one_moves = sorted([s for s in suggestions if s["type"] == "1FT"], key=lambda x: x["gain"], reverse=True)[:40]
    top_one_by_out = one_moves[:12]
    # Rebuild from selected outs/ins IDs
    for i in range(len(top_one_by_out)):
        for j in range(i + 1, len(top_one_by_out)):
            out_ids = {m["transfers_out"][0]["id"] for m in [top_one_by_out[i], top_one_by_out[j]]}
            if len(out_ids) < 2:
                continue
            in_ids = {m["transfers_in"][0]["id"] for m in [top_one_by_out[i], top_one_by_out[j]]}
            if len(in_ids) < 2:
                continue
            out_players = [by_id[_to_int(x)] for x in out_ids if _to_int(x) in by_id]
            in_players = [by_id[_to_int(x)] for x in in_ids if _to_int(x) in by_id]
            if len(out_players) != 2 or len(in_players) != 2:
                continue
            new_squad = [p for p in squad if _to_int(p.get("id")) not in out_ids] + in_players
            kind = "2FT" if req.free_transfers >= 2 else "-4"
            evaluate_candidate(new_squad, 2, out_players, in_players, kind)

    # keep top non-hold suggestions plus hold
    uniq: Dict[Tuple[str, Tuple[int, ...], Tuple[int, ...]], Dict[str, Any]] = {}
    for s in suggestions:
        out_key = tuple(sorted(int(x["id"]) for x in s["transfers_out"]))
        in_key = tuple(sorted(int(x["id"]) for x in s["transfers_in"]))
        k = (s["type"], out_key, in_key)
        cur = uniq.get(k)
        if cur is None or s["gain"] > cur["gain"]:
            uniq[k] = s
    merged = list(uniq.values())
    merged.sort(key=lambda x: x["gain"], reverse=True)
    top = [x for x in merged if x["type"] == "HOLD"][:1] + [x for x in merged if x["type"] != "HOLD"][:9]

    return {
        "baseline": {
            "xi_ids": [_to_int(p.get("id")) for p in base["xi"]],
            "formation": base["formation"],
            "projected": round(baseline_projected, 3),
        },
        "suggestions": top,
    }


@app.get("/api/best_team")
def api_best_team(
    gws: int = Query(default=3, ge=1, le=8),
    include_with_prob: bool = Query(default=True),
    mode: str = Query(default="single", pattern="^(single|next5)$"),
) -> Dict[str, Any]:
    """
    Best XI suggestion with XI constraints and max 3 players per club.
    """
    payload = api_players(gws=gws, include_with_prob=include_with_prob, start_gw=None)
    players = payload["players"]
    best = optimize_xi_from_pool(
        players=players,
        start_gw=payload["next_gw"],
        mode=mode,
        apply_prob=include_with_prob,
        max_from_team=3,
    )
    if best.get("error"):
        return {
            "next_gw": payload["next_gw"],
            "gws": payload["gws"],
            "include_with_prob": payload["include_with_prob"],
            "error": best["error"],
            "best_team": None,
        }
    picked = [
        {
            "id": _to_int(p.get("id")),
            "player_name": p.get("player_name"),
            "team": p.get("team"),
            "position": normalize_position(p),
            "cost": _to_float(p.get("cost"), 0.0),
            "pts_next_sum": _to_float(p.get("pts_next_sum"), 0.0),
        }
        for p in best["xi"]
    ]
    return {
        "next_gw": payload["next_gw"],
        "gws": payload["gws"],
        "include_with_prob": payload["include_with_prob"],
        "best_team": {
            "picked": picked,
            "formation": best["formation"],
            "projected": round(_to_float(best["projected"], 0.0), 3),
            "note": "Constrained optimizer with max 3 players per club.",
        },
    }
