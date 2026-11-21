"""Load KEY=value lines from the project's .env file into os.environ.

No extra dependency. Values already set in the real environment win, so the same code
works on your laptop (.env file) and on Ninoo (environment settings). Never commit .env.
"""
import os
from pathlib import Path


def load_env(path=None) -> list:
    p = Path(path) if path else Path(__file__).resolve().parents[1] / ".env"
    if not p.is_file():
        return []
    loaded = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = (s.strip() for s in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
