from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir


APP_NAME = "scp-browser-tui"
CONFIG_DIR = Path(user_config_dir(APP_NAME))
PROFILES_FILE = CONFIG_DIR / "profiles.json"


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def load_json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    ensure_config_dir()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
