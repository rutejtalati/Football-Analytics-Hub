# AGENTS.md

## Project Overview
- Frontend: Vite + React in `/frontend`
- Backend: Python API in `/backend`

## Run Commands
- Frontend dev: `cd frontend && npm run dev`
- Frontend build: `cd frontend && npm run build`
- Backend dev (uvicorn): `cd backend && uvicorn main:app --reload --host 127.0.0.1 --port 8000`

## Working Rules
- Before changing code: identify the root cause and point to file + line.
- After changes: run lint/build/tests if available and report results.
- Keep API contracts stable unless explicitly requested otherwise.
- Prefer minimal diffs and clear, explicit error handling.
