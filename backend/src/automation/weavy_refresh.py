#!/usr/bin/env python3
import sys
import json
import argparse
import sqlite3
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Add the current directory to sys.path so we can import weavy_service
sys.path.append(str(Path(__file__).parent.resolve()))
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
from weavy_service import WeavyService

def safe_email_to_dirname(email: str) -> str:
    cleaned = (email or "").strip().lower()
    cleaned = cleaned.replace("@", "_at_")
    cleaned = re.sub(r"[^a-z0-9._-]+", "_", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned or "account"

def get_db_path() -> Path:
    return Path.home() / ".9router-v2" / "db" / "data.sqlite"

def load_settings_db() -> Dict[str, Any]:
    db_path = get_db_path()
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT data FROM settings WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            return json.loads(row["data"])
    except Exception as e:
        sys.stderr.write(f"[weavy_refresh] Error loading settings: {e}\n")
    return {}

def load_connection_db(email: str) -> Optional[Dict[str, Any]]:
    db_path = get_db_path()
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM providerConnections WHERE provider = 'weavy' AND (email = ? OR name = ?)", (email, email))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception as e:
        sys.stderr.write(f"[weavy_refresh] Error loading connection: {e}\n")
    return None

class MockStore:
    def __init__(self, email: str, conn_row: Optional[dict], settings: dict, resolved_profile_dir: Path):
        self.email = email
        self.conn_row = conn_row or {}
        self.settings = settings
        self.resolved_profile_dir = resolved_profile_dir

    def get_setting(self, key: str, default: str = "") -> str:
        return str(self.settings.get(key, default))

    def get_weavy_account(self, account_id: int) -> dict:
        return {
            "email": self.email,
            "profile_dir": str(self.resolved_profile_dir.resolve())
        }

    def update_weavy_account_credits(self, account_id: int, credits: float):
        # Node.js/testUtils will update the sqlite DB with the returned balance, so we skip it here
        pass

def main():
    parser = argparse.ArgumentParser(description="Weavy Token Refresh Helper")
    parser.add_argument("--email", required=True)
    parser.add_argument("--profiles-dir", required=True)
    args = parser.parse_args()

    email = args.email.strip()
    profiles_root = Path(args.profiles_dir)
    resolved_profile_dir = profiles_root / safe_email_to_dirname(email)

    settings = load_settings_db()
    conn_row = load_connection_db(email)

    store = MockStore(email, conn_row, settings, resolved_profile_dir)
    service = WeavyService(store=store, profiles_root=profiles_root)

    try:
        # 1. Grab fresh Firebase JWT token via Camoufox
        token = service.get_auth_token(account_id=0, force_refresh=True)
        if not token:
            raise RuntimeError("Failed to capture token")

        # 2. Get fresh balance
        balance = service.get_account_balance(account_id=0)

        # Output result
        sys.stdout.write(json.dumps({
            "status": "success",
            "jwt": token,
            "balance": balance
        }, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    except Exception as e:
        sys.stdout.write(json.dumps({
            "status": "error",
            "message": str(e)
        }, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        sys.exit(1)

if __name__ == "__main__":
    main()
