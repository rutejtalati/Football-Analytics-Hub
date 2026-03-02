# Backend API

## Run
```bash
cd backend
uvicorn main:app --reload
```

## Environment
Set football-data.org key for multi-league endpoints:

```bash
export FOOTBALL_DATA_API_KEY="db789c47e9e94ceeb9b5e256934fc723"
```

Windows PowerShell:
```powershell
$env:FOOTBALL_DATA_API_KEY="db789c47e9e94ceeb9b5e256934fc723"
```

## Existing FPL endpoints
- `GET /api/players?gws=3&include_with_prob=true&start_gw=29`
- `GET /api/squad?team_id=123456`
- `GET /api/best_team?gws=3&include_with_prob=true`
- `GET /api/epl_table`
- `POST /api/transfer_suggestions`

## New multi-league endpoints
- `GET /api/leagues`
- `GET /api/league/{code}/standings`
- `GET /api/league/{code}/fixtures?days=14`
- `GET /api/league/{code}/predictions?days=14`

Supported league codes:
- `EPL` (PL)
- `LALIGA` (PD)
- `LIGUE1` (FL1)
- `SERIEA` (SA)

## Example curl
```bash
curl "http://127.0.0.1:8000/api/leagues"
curl "http://127.0.0.1:8000/api/league/EPL/standings"
curl "http://127.0.0.1:8000/api/league/EPL/fixtures?days=14"
curl "http://127.0.0.1:8000/api/league/EPL/predictions?days=14"
```

Notes:
- Responses are normalized (no raw vendor payload is returned).
- Multi-league routes use in-memory TTL caching to reduce API calls.

## EPL table live key
- Create `backend/.env` and set: `FOOTBALL_DATA_API_KEY=PASTE_YOUR_FOOTBALL_DATA_TOKEN_HERE`
- `/api/epl_table` uses `X-Auth-Token` against `/v4/competitions/PL/standings`.
- If key is missing or request fails, fallback standings are returned; free tier is rate-limited.

## Setup football-data.org token
- Put your token into `backend/.env` as `FOOTBALL_DATA_API_KEY=...`
- Restart `uvicorn main:app --reload`
