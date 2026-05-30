#!/usr/bin/env python3
"""Shared configuration and Cloudflare D1 helpers for Hermes Project.

This module is intentionally public-repo-safe:
- no hardcoded secrets
- no hardcoded personal chat IDs
- project root resolved dynamically
- environment values may come from OS env vars or a local `.env`
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_CONFIG_CACHE: Dict[str, str] | None = None
_D1_SESSION: requests.Session | None = None
_PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parent)).resolve()
_HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()


def get_project_root() -> Path:
    return _PROJECT_ROOT


def _candidate_env_files() -> Iterable[Path]:
    explicit = os.getenv("HERMES_PROJECT_ENV")
    paths = []
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.extend(
        [
            _PROJECT_ROOT / ".env",
            _HERMES_HOME / ".env",
        ]
    )
    seen = set()
    for path in paths:
        resolved = path.resolve() if path.exists() else path
        if str(resolved) in seen:
            continue
        seen.add(str(resolved))
        yield path


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_config() -> Dict[str, str]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    merged: Dict[str, str] = {}
    for env_file in _candidate_env_files():
        merged.update(_parse_env_file(env_file))

    # Real environment variables override file values.
    merged.update({k: v for k, v in os.environ.items() if v is not None})

    merged.setdefault("PROJECT_ROOT", str(_PROJECT_ROOT))
    merged.setdefault("HERMES_HOME", str(_HERMES_HOME))
    merged.setdefault("CHROMA_HOST", "localhost")
    merged.setdefault("CHROMA_PORT", "8000")
    merged.setdefault("HERMES_NEWS_TARGET", "qqbot")
    merged.setdefault("HERMES_PROJECT_REPORT_DIR", str(_PROJECT_ROOT / "doctor-reports"))
    merged.setdefault("HERMES_PROJECT_ARCHIVE_DIR", str(_PROJECT_ROOT / "archive"))

    _CONFIG_CACHE = merged
    return _CONFIG_CACHE


def require_config(*keys: str) -> Dict[str, str]:
    cfg = get_config()
    missing = [key for key in keys if not cfg.get(key)]
    if missing:
        raise RuntimeError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Set them in the environment or in .env / $HERMES_HOME/.env."
        )
    return cfg


def get_d1_session() -> requests.Session:
    global _D1_SESSION
    if _D1_SESSION is not None:
        return _D1_SESSION

    cfg = require_config(
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_FINANCE_D1_DATABASE_ID",
    )
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update(
        {
            "Authorization": f"Bearer {cfg['CLOUDFLARE_API_TOKEN']}",
            "Content-Type": "application/json",
        }
    )
    _D1_SESSION = session
    return _D1_SESSION


def query_d1(sql: str, params=None):
    cfg = require_config(
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_FINANCE_D1_DATABASE_ID",
    )
    session = get_d1_session()
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{cfg['CLOUDFLARE_ACCOUNT_ID']}/d1/database/"
        f"{cfg['CLOUDFLARE_FINANCE_D1_DATABASE_ID']}/query"
    )
    payload = {"sql": sql}
    if params:
        payload["params"] = params

    resp = session.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()
