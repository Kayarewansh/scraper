"""Local config storage: API key and default save folder. Stored in the
user's Application Support folder (not next to the script), so settings
persist correctly regardless of whether the app runs from source or as a
packaged .app bundle."""
import json
import os

APP_SUPPORT_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Lead Scraper")
CONFIG_PATH = os.path.join(APP_SUPPORT_DIR, "config.json")

DEFAULT_SAVE_FOLDER = os.path.join(os.path.expanduser("~"), "Documents", "Lead Scraper Leads")


def _load() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def load_api_key() -> str:
    return _load().get("google_places_api_key", "")


def save_api_key(key: str) -> None:
    data = _load()
    data["google_places_api_key"] = key.strip()
    _save(data)


def load_save_folder() -> str:
    """Returns the user's chosen default save folder, creating it (or the
    built-in default under ~/Documents) if it doesn't exist yet."""
    folder = _load().get("save_folder") or DEFAULT_SAVE_FOLDER
    os.makedirs(folder, exist_ok=True)
    return folder


def save_save_folder(folder: str) -> None:
    data = _load()
    data["save_folder"] = folder
    _save(data)
