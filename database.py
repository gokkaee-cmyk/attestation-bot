import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/app/data/attestations.json")


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        DB_PATH.write_text("[]", encoding="utf-8")


def save_attestation(record: dict):
    _ensure_db()
    data = json.loads(DB_PATH.read_text(encoding="utf-8"))
    data.append(record)
    DB_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_all_attestations() -> list:
    _ensure_db()
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


def clear_attestations():
    _ensure_db()
    DB_PATH.write_text("[]", encoding="utf-8")


def get_count() -> int:
    return len(get_all_attestations())
