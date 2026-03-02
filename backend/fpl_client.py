from __future__ import annotations
import requests
from typing import Any, Dict, List

FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_FIXTURES = "https://fantasy.premierleague.com/api/fixtures/"

def fetch_bootstrap(timeout: int = 25) -> Dict[str, Any]:
    r = requests.get(FPL_BOOTSTRAP, timeout=timeout)
    r.raise_for_status()
    return r.json()

def fetch_fixtures(timeout: int = 25) -> List[Dict[str, Any]]:
    r = requests.get(FPL_FIXTURES, timeout=timeout)
    r.raise_for_status()
    return r.json()

def get_next_gw(bootstrap: Dict[str, Any]) -> int:
    events = bootstrap.get("events", [])
    for ev in events:
        if ev.get("is_next") is True:
            return int(ev["id"])
    return int(max(ev.get("id", 1) for ev in events)) if events else 1