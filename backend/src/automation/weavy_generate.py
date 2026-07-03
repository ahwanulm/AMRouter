#!/usr/bin/env python3
import sys
import os
import json
import argparse
import sqlite3
import re
import time
import logging
from pathlib import Path
from typing import Any, Dict, Optional


# Configure logging to see automation logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)

# Add the current directory to sys.path so we can import weavy_service
sys.path.append(str(Path(__file__).parent.resolve()))
from weavy_service import WeavyService

def safe_email_to_dirname(email: str) -> str:
    cleaned = (email or "").strip().lower()
    cleaned = cleaned.replace("@", "_at_")
    cleaned = re.sub(r"[^a-z0-9._-]+", "_", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned or "account"

def get_db_path() -> Path:
    """Return the path to the 9router data DB, respecting DATA_DIR env var."""
    data_dir = os.environ.get("DATA_DIR", str(Path.home() / ".9router-v2"))
    return Path(data_dir) / "db" / "data.sqlite"


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
        sys.stderr.write(f"[weavy_generate] Error loading settings: {e}\n")
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
        sys.stderr.write(f"[weavy_generate] Error loading connection: {e}\n")
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
        pass

def run_generation(service: WeavyService, token: str, prompt: str, aspect_ratio: str, model_type: str, model: str, duration: Optional[int] = None, image_url: Optional[str] = None, end_image_url: Optional[str] = None, video_url: Optional[str] = None, negative_prompt: Optional[str] = None, recipe_id: Optional[str] = None):
    # 1. Select template ID
    template_id = "SZXXYN7L9PN2SCTVYAlt" # default image template
    if model_type.lower() == "video":
        template_id = "6e2Si9kdgSmxQ6JwpHaz" # default video template

    # 2. Duplicate recipe — skip if recipeId already provided by Node.js caller
    if recipe_id:
        logging.getLogger(__name__).info("[weavy_generate] Using pre-created recipe_id from caller: %s (skip duplicate)", recipe_id)
    else:
        recipe_id = service.duplicate_recipe(token, template_id)

    # 3. Configure flow and execute to get batch ID
    batch_id = service.execute_flow(
        token=token,
        recipe_id=recipe_id,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        model_type=model_type,
        model=model,
        duration=duration,
        image_url=image_url,
        end_image_url=end_image_url,
        video_url=video_url,
        negative_prompt=negative_prompt
    )

    # 4. Poll batch status until complete
    # Image models finish faster — use tighter poll interval to reduce latency
    poll_interval = 3.0 if model_type.lower() == "video" else 1.5
    timeout = 600.0 if model_type.lower() == "video" else 180.0
    deadline = time.time() + timeout

    while time.time() < deadline:
        status_data = service.poll_batch_status(token, recipe_id, batch_id)
        status = status_data.get("status")

        if status == "completed":
            return status_data.get("urls") or []
        elif status == "failed":
            raise RuntimeError(status_data.get("error") or "Weavy flow execution failed")
        
        time.sleep(poll_interval)

    raise TimeoutError("Weavy generation timed out")

def main():
    parser = argparse.ArgumentParser(description="Weavy Generation Helper")
    parser.add_argument("--email", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--aspect-ratio", default="1:1")
    parser.add_argument("--model-type", default="image")
    parser.add_argument("--model", default="")
    parser.add_argument("--profiles-dir", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--duration", type=int, default=None)
    parser.add_argument("--image-url", default=None)
    parser.add_argument("--end-image-url", default=None)
    parser.add_argument("--video-url", default=None)
    parser.add_argument("--negative-prompt", default=None)
    parser.add_argument("--recipe-id", default=None, help="Pre-created recipe ID from Node.js duplicate call; skips Python duplicate_recipe() to avoid double call")
    args = parser.parse_args()

    email = args.email.strip()
    prompt = args.prompt.strip()
    aspect_ratio = args.aspect_ratio.strip()
    model_type = args.model_type.strip().lower()
    model = args.model.strip().lower()
    profiles_root = Path(args.profiles_dir)
    resolved_profile_dir = profiles_root / safe_email_to_dirname(email)

    settings = load_settings_db()
    conn_row = load_connection_db(email)

    store = MockStore(email, conn_row, settings, resolved_profile_dir)
    service = WeavyService(store=store, profiles_root=profiles_root)

    token = args.token.strip()
    
    # Try with existing token first if provided
    try:
        if token:
            urls = run_generation(
                service, token, prompt, aspect_ratio, model_type, model, 
                args.duration, args.image_url, args.end_image_url, args.video_url, args.negative_prompt,
                recipe_id=args.recipe_id
            )
            # Fetch real credits balance so Node.js can update quota tracker
            try:
                balance = service.get_account_balance(0)
            except Exception:
                balance = None
            out = {"status": "success", "urls": urls}
            if balance is not None:
                out["balance"] = balance
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            sys.exit(0)
    except Exception as e:
        # Decide whether to retry with a fresh token.
        # Clear auth failures: 401 / Unauthorized (any Weavy endpoint).
        # Weavy-specific: HTTP 500 with internalErrorCode:1999 means the Firebase
        # token is expired — Weavy returns 500 instead of 401 for this case.
        # Guard: only retry if the local Camoufox profile exists so we can actually
        # capture a new token (avoids pointless browser launch on machines without profiles).
        err_msg = str(e)
        has_profile = resolved_profile_dir.exists()
        
        # Explicitly exclude credit-exhaustion and billing-related errors
        is_credit_issue = (
            "insufficient credits" in err_msg.lower()
            or "1007" in err_msg
            or "1076" in err_msg
            or "only available on paid plans" in err_msg.lower()
        )
        
        is_auth_failure = False
        if not is_credit_issue:
            is_auth_failure = (
                "401" in err_msg
                or "Unauthorized" in err_msg
                or "Authentication Error" in err_msg
                or (has_profile and "internalErrorCode" in err_msg)
            )
            
        if not is_auth_failure:
            sys.stdout.write(json.dumps({
                "status": "error",
                "message": err_msg
            }, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            sys.exit(1)
        sys.stderr.write(f"[weavy_generate] Auth/token failure ({err_msg[:100]}). Refreshing via Camoufox...\n")






    # If no token, or token expired, trigger a refresh and run
    try:
        token = service.get_auth_token(account_id=0, force_refresh=True)
        if not token:
            raise RuntimeError("Failed to capture fresh token")
        
        urls = run_generation(
            service, token, prompt, aspect_ratio, model_type, model, 
            args.duration, args.image_url, args.end_image_url, args.video_url, args.negative_prompt,
            recipe_id=None  # Fresh token path: always duplicate from scratch
        )
        # Fetch real credits balance so Node.js can update quota tracker
        try:
            balance = service.get_account_balance(0)
        except Exception:
            balance = None
        out = {"status": "success", "urls": urls, "refreshed_token": token}
        if balance is not None:
            out["balance"] = balance
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        sys.exit(0)

    except Exception as e:
        sys.stdout.write(json.dumps({
            "status": "error",
            "message": str(e)
        }, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        sys.exit(1)

if __name__ == "__main__":
    main()
