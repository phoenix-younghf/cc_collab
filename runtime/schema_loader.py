from __future__ import annotations

import json
from pathlib import Path


SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def load_schema_text(name: str) -> str:
    return (SCHEMA_DIR / name).read_text(encoding="utf-8")
