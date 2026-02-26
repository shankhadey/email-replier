"""
Behavioral parameters loader — reads behavior_params.json.
Edit that file to change AI voice, classification rules, and persona
without touching code. Changes take effect on the next processed email.
"""

import json
from pathlib import Path

PARAMS_FILE = Path("behavior_params.json")


def load_params() -> dict:
    if PARAMS_FILE.exists():
        with open(PARAMS_FILE) as f:
            return json.load(f)
    return {}
