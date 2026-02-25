import json
from pathlib import Path

CONFIG_FILE = Path("config.json")

DEFAULTS = {
    "poll_interval_minutes": 30,
    "poll_start_hour": 0,
    "poll_end_hour": 23,
    "autonomy_level": 1,
    "anthropic_model": "claude-sonnet-4-6",
    "low_confidence_threshold": 0.70,
    "user_timezone": "America/Chicago",
    "lookback_hours": 72,
}

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return {**DEFAULTS, **data}
    return DEFAULTS.copy()

def save_config(updates: dict) -> dict:
    config = load_config()
    config.update(updates)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    return config
