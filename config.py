"""Local config storage for the API key. Stored next to the app, never sent anywhere but Google's API."""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_api_key() -> str:
    if not os.path.exists(CONFIG_PATH):
        return ""
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        return data.get("google_places_api_key", "")
    except (json.JSONDecodeError, OSError):
        return ""


def save_api_key(key: str) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump({"google_places_api_key": key.strip()}, f, indent=2)
