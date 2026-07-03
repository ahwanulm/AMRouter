#!/usr/bin/env python3
"""Standalone script to automate Weavy auto-signup via Figma OAuth and Ammail.
Outputs step logs and final results to stdout as JSON lines.
"""

import sys
import json
import argparse
import time
import logging
import re
import sqlite3
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

# Patch Playwright's Locator.is_visible to support the timeout argument
try:
    from playwright.sync_api import Locator
    _orig_is_visible = Locator.is_visible
    def _patched_is_visible(self, *args, **kwargs):
        timeout = kwargs.pop("timeout", None)
        if timeout is None and len(args) > 0:
            timeout = args[0]
            args = args[1:]
        if timeout is not None:
            try:
                self.wait_for(state="visible", timeout=float(timeout))
                return True
            except Exception:
                return False
        return _orig_is_visible(self, *args, **kwargs)
    Locator.is_visible = _patched_is_visible
except ImportError:
    pass

# Setup logger
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("weavy_signup")

# URLs
FIGMA_HOME_URL = "https://www.figma.com/"
FIGMA_SIGNUP_URL = "https://www.figma.com/signup"
WEAVY_SIGNIN_URL = "https://app.weavy.ai/signin"

# Selector banks
FIGMA_GET_STARTED_SELECTORS = (
    "a:has-text('Get started for free'):visible",
    "a:has-text('Get started'):visible",
    "button:has-text('Get started'):visible",
    "button:has-text('Get started for free'):visible",
    "a:text('Get started for free'):visible",
    "a:text('Get started'):visible",
    "button:text('Get started'):visible",
    "button:text('Get started for free'):visible",
)

FIGMA_EMAIL_INPUT_SELECTORS = (
    "input[type='email']",
    "input[name='email']",
    "input[id='email']",
    "input[placeholder*='email' i]",
)

FIGMA_CONTINUE_EMAIL_SELECTORS = (
    "button:has-text('Continue with email')",
    "button:has-text('Continue with Email')",
    "button[type='submit']",
)

FIGMA_PASSWORD_SELECTORS = (
    "input[type='password']",
    "input[name='password']",
)

FIGMA_SUBMIT_SELECTORS = (
    "button[type='submit']",
    "button:has-text('Create account')",
    "button:has-text('Continue')",
    "button:has-text('Next')",
)

WEAVY_FIGMA_LOGIN_SELECTORS = (
    "button:has-text('Log in with Figma')",
    "button[aria-label*='Figma' i]",
    "a:has-text('Log in with Figma')",
    "div[role='button']:has-text('Log in with Figma')",
)

FIGMA_OAUTH_ALLOW_SELECTORS = (
    "button:has-text('Allow access')",
    "button:text('Allow')",
    "button:has-text('Authorize')",
    "button:has-text('Approve')",
)

class WeavyAutomationError(RuntimeError):
    pass

def log_step(msg, *args):
    txt = msg % args if args else msg
    sys.stdout.write(json.dumps({"step": txt}, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def safe_email_to_dirname(email: str) -> str:
    cleaned = (email or "").strip().lower()
    cleaned = cleaned.replace("@", "_at_")
    cleaned = re.sub(r"[^a-z0-9._-]+", "_", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned or "account"

def get_db_path() -> Path:
    # 9router default db path
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
        logger.warning(f"Error loading settings from DB: {e}")
    return {}

# --- Ammail Sync / OTP logic ---
def iso_to_unix(iso_str: str) -> int:
    from datetime import datetime
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return int(dt.timestamp())
    except Exception as e:
        logger.warning(f"Error parsing date {iso_str}: {e}")
        return int(time.time())

def extract_otp_py(text: str, html: str = "", subject: str = "") -> tuple:
    parts = [subject or "", text or "", html or ""]
    haystack = "\n".join(filter(None, parts))
    
    clean_text = re.sub(r"<style[\s\S]*?</style>", " ", haystack, flags=re.IGNORECASE)
    clean_text = re.sub(r"<script[\s\S]*?</script>", " ", clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r"<[^>]+>", " ", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    # Figma verification link pattern
    match = re.search(r"https://[a-zA-Z0-9.-]*figma\.com/[^\s\"'>]+", clean_text)
    verify_url = match.group(0) if match else None
    
    # Try HTML fallback if no link found in plain text
    if not verify_url and html:
        match = re.search(r'href=["\'](https://[a-zA-Z0-9.-]*figma\.com/[^"\']+)["\']', html)
        if match:
            verify_url = match.group(1)

    if verify_url:
        verify_url = verify_url.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
        
    return "", verify_url

def sync_ammail_messages(email: str, since_ts: int, settings: dict):
    db_path = get_db_path()
    if not db_path.exists():
        return

    api_key = settings.get("ammail_api_key")
    base_url = settings.get("ammail_base_url")
    fallback_url = settings.get("ammail_cf_workers_dev_url")

    if not api_key or (not base_url and not fallback_url):
        return

    alias = email.split("@")[0]
    domain = email.split("@")[1] if "@" in email else ""

    urls_to_try = []
    if base_url:
        urls_to_try.append(base_url.rstrip("/"))
    if fallback_url:
        urls_to_try.append(fallback_url.rstrip("/"))

    messages = []
    for base in urls_to_try:
        url = f"{base}/api/inboxes/{alias}/messages"
        req = urllib.request.Request(
            url,
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8"))
                messages = data.get("messages", [])
                if messages:
                    break
        except Exception as e:
            logger.warning(f"Failed to fetch inbox messages from {url}: {e}")

    if not messages:
        return

    for msg in messages:
        msg_id = msg.get("id")
        if not msg_id:
            continue

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM ammailOtps WHERE messageShortId = ?", (msg_id,))
            exists = cursor.fetchone()
            conn.close()
            if exists:
                continue
        except Exception as e:
            logger.warning(f"Error checking message existence: {e}")
            continue

        full_msg = None
        for base in urls_to_try:
            url = f"{base}/api/messages/{msg_id}"
            req = urllib.request.Request(
                url,
                headers={
                    "X-API-Key": api_key,
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as res:
                    data = json.loads(res.read().decode("utf-8"))
                    full_msg = data.get("message")
                    if full_msg:
                        break
            except Exception as e:
                logger.warning(f"Failed to fetch full message {msg_id} from {url}: {e}")

        if not full_msg:
            continue

        body_text = str(full_msg.get("text") or msg.get("snippet") or "")
        body_html = str(full_msg.get("html") or "")
        from_data = full_msg.get("from") or msg.get("from") or {}
        sender = str(from_data.get("address") or from_data.get("name") or "")
        subject = str(full_msg.get("subject") or msg.get("subject") or "")
        received_at_str = full_msg.get("receivedAt") or msg.get("receivedAt") or ""
        received_at = iso_to_unix(received_at_str)

        otp_code, verify_url = extract_otp_py(body_text, body_html, subject)

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ammailOtps (
                    address, alias, domain, sender, subject, otpCode, verifyUrl, 
                    bodyText, bodyHtml, messageShortId, rawEventJson, receivedAt, usedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    email, alias, domain, sender, subject, otp_code, verify_url,
                    body_text, body_html, msg_id, json.dumps(full_msg), received_at
                )
            )
            conn.commit()
            conn.close()
            log_step(f"Sync: Tersinkronisasi email verifikasi Figma: {subject}")
        except Exception as e:
            logger.warning(f"Error storing synced OTP to SQLite: {e}")

def wait_for_otp_from_db(email: str, since_ts: int, settings: dict, timeout: int = 180) -> tuple:
    db_path = get_db_path()
    if not db_path.exists():
        log_step(f"Database file tidak ditemukan di {db_path}, menunggu OTP dialihkan...")
        return "", ""

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sync_ammail_messages(email, since_ts, settings)
        except Exception as e:
            logger.warning(f"Error syncing Ammail messages: {e}")

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, otpCode, verifyUrl FROM ammailOtps WHERE LOWER(address) = ? AND receivedAt >= ? AND usedAt = 0 ORDER BY receivedAt DESC LIMIT 1",
                (email.lower(), since_ts)
            )
            row = cursor.fetchone()
            if row:
                otp_id = row["id"]
                otp_code = row["otpCode"]
                verify_url = row["verifyUrl"]
                now_unix = int(time.time())
                cursor.execute("UPDATE ammailOtps SET usedAt = ? WHERE id = ?", (now_unix, otp_id))
                conn.commit()
                conn.close()
                return otp_code, verify_url
            conn.close()
        except Exception as e:
            logger.warning(f"Error querying SQLite: {e}")
        time.sleep(2)
    return "", ""

# --- 2Captcha Solvers ---
class CaptchaSolverError(RuntimeError):
    pass

def solve_funcaptcha(
    api_key: str,
    public_key: str,
    page_url: str,
    surl: str,
    data_blob: Optional[str] = None,
    user_agent: Optional[str] = None,
    proxy_str: Optional[str] = None,
    proxy_type: Optional[str] = None,
    *,
    timeout: float = 180.0,
    poll_interval: float = 5.0,
) -> str:
    if not api_key:
        raise CaptchaSolverError("2Captcha API key kosong")

    submit_payload = {
        "key": api_key,
        "method": "funcaptcha",
        "publickey": public_key,
        "pageurl": page_url,
        "surl": surl,
        "json": 1,
    }
    if data_blob:
        submit_payload["data[blob]"] = data_blob
    if user_agent:
        submit_payload["userAgent"] = user_agent
    if proxy_str:
        submit_payload["proxy"] = proxy_str
        submit_payload["proxytype"] = proxy_type or "HTTP"

    try:
        r = requests_post("https://2captcha.com/in.php", data=submit_payload)
        data = json.loads(r)
    except Exception as exc:
        raise CaptchaSolverError(f"2Captcha submit gagal: {exc}") from exc

    if str(data.get("status")) != "1":
        raise CaptchaSolverError(f"2Captcha submit ditolak: {data.get('request') or data}")

    task_id = str(data.get("request") or "").strip()
    if not task_id:
        raise CaptchaSolverError("2Captcha tidak return task id")

    log_step("FunCaptcha submitted ke 2Captcha id=%s, polling...", task_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            res_url = f"https://2captcha.com/res.php?key={api_key}&action=get&id={task_id}&json=1"
            r = requests_get(res_url)
            res_data = json.loads(r)
        except Exception as exc:
            logger.warning("[captcha] Poll error (will retry): %s", exc)
            continue

        status = str(res_data.get("status"))
        request_val = str(res_data.get("request") or "")

        if status == "1":
            log_step("FunCaptcha %s solved!", task_id)
            return request_val

        if request_val == "CAPCHA_NOT_READY":
            continue

        raise CaptchaSolverError(f"2Captcha task gagal: {request_val}")

    raise CaptchaSolverError(f"Timeout {int(timeout)}s menunggu solve FunCaptcha dari 2Captcha")

def requests_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8")

def requests_post(url: str, data: dict) -> str:
    encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded_data, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8")

# --- Helper routines ---
_DEBUG_PATH = "/tmp/9router_debug.png"
_debug_page = None

def update_debug_screenshot(page):
    """Safely update /tmp/9router_debug.png."""
    if page:
        try:
            page.screenshot(path=_DEBUG_PATH, timeout=2000)
        except Exception:
            pass

import time
_orig_sleep = time.sleep

def _patched_sleep(seconds):
    global _debug_page
    if _debug_page is None:
        _orig_sleep(seconds)
        return
    
    end_time = time.time() + seconds
    while time.time() < end_time:
        remaining = end_time - time.time()
        chunk = min(0.4, remaining)
        if chunk <= 0:
            break
        _orig_sleep(chunk)
        # Skip screenshots during short sleeps to avoid overhead
        if seconds > 1.0:
            try:
                update_debug_screenshot(_debug_page)
            except Exception:
                pass

time.sleep = _patched_sleep

def click_first(page, selectors, timeout_ms: int = 8000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                count = loc.count()
                visible = False
                if count > 0:
                    visible = loc.is_visible(timeout=300)
                log_step("click_first check: sel='%s', count=%d, visible=%s", sel, count, visible)
                if count > 0 and visible:
                    try:
                        loc.scroll_into_view_if_needed(timeout=600)
                        loc.click(timeout=2000, force=True)
                        update_debug_screenshot(page)
                        return True
                    except Exception as e_click:
                        log_step("click_first standard click failed, trying evaluate click: %s", str(e_click))
                        handle = loc.element_handle(timeout=300)
                        if handle:
                            page.evaluate("(el) => el.click()", handle)
                            update_debug_screenshot(page)
                            return True
            except Exception as e:
                log_step("click_first exception: sel='%s' err='%s'", sel, str(e))
                continue
        time.sleep(0.3)
    return False

def fill_first(page, selectors, value: str, timeout_ms: int = 8000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                count = loc.count()
                visible = False
                if count > 0:
                    visible = loc.is_visible(timeout=300)
                log_step("fill_first check: sel='%s', count=%d, visible=%s", sel, count, visible)
                if count > 0 and visible:
                    try:
                        loc.fill("", timeout=500)
                    except Exception:
                        pass
                    loc.fill(value, timeout=2000)
                    update_debug_screenshot(page)
                    return True
            except Exception as e:
                log_step("fill_first exception: sel='%s' err='%s'", sel, str(e))
                continue
        time.sleep(0.3)
    return False

def complete_figma_onboarding(page, email: str) -> None:
    log_step("Menyelesaikan onboarding Figma...")
    deadline = time.time() + 35.0
    while time.time() < deadline:
        url = page.url
        if "/files" in url or "/dashboard" in url or "canvas" in url:
            log_step("Onboarding selesai, berada di dashboard Figma.")
            return

        try:
            name_input = page.locator("input[name='name'], input[placeholder*='name' i], input[type='text']").first
            if name_input.count() > 0 and name_input.is_visible(timeout=500):
                val = name_input.input_value(timeout=500)
                if not val:
                    name_input.fill(email.split("@")[0], timeout=1000)
                    time.sleep(0.5)
        except Exception:
            pass

        clicked = False
        for text in ("Continue", "Skip", "Next", "Start for free", "Maybe later", "Design", "Personal use", "Start collaborating"):
            try:
                btn = page.locator(f"button:has-text('{text}'), a:has-text('{text}'), div[role='button']:has-text('{text}')").first
                if btn.count() > 0 and btn.is_visible(timeout=500):
                    btn.click(timeout=1000)
                    log_step(f"Klik tombol onboarding: {text}")
                    clicked = True
                    time.sleep(1.5)
                    break
            except Exception:
                continue

        update_debug_screenshot(page)
        if not clicked:
            time.sleep(1.0)

_debug_context = None

def start_debug_screenshots(page):
    """Start capturing screenshots synchronously."""
    global _debug_page
    _debug_page = page
    update_debug_screenshot(page)

def stop_debug_screenshots():
    """Stop capturing screenshots and clean up temp screenshot file."""
    global _debug_page
    _debug_page = None
    try:
        import os
        if os.path.exists(_DEBUG_PATH):
            os.remove(_DEBUG_PATH)
    except Exception:
        pass

def save_debug_screenshots(context, profiles_dir: str, email: str, prefix="failure"):
    try:
        debug_dir = Path(profiles_dir).parent / "weavy_debug" / safe_email_to_dirname(email)
        debug_dir.mkdir(parents=True, exist_ok=True)
        pages = getattr(context, "pages", []) or []
        for i, pg in enumerate(pages):
            try:
                url = pg.url or "empty"
                ts = int(time.time())
                sanitized_url = re.sub(r"[^a-zA-Z0-9.-]", "_", url)[:50]
                screenshot_path = debug_dir / f"{prefix}_page_{i}_{sanitized_url}_{ts}.png"
                html_path = debug_dir / f"{prefix}_page_{i}_{sanitized_url}_{ts}.html"
                
                log_step(f"Mengambil screenshot halaman {i} (URL: {url[:60]})...")
                pg.screenshot(path=str(screenshot_path), full_page=True, timeout=10000)
                try:
                    html_path.write_text(pg.content() or "", encoding="utf-8")
                except Exception:
                    pass
                log_step(f"Screenshot disimpan di: {screenshot_path}")
            except Exception as e:
                logger.warning(f"Failed to screenshot page {i}: {e}")
    except Exception as e:
        logger.warning(f"Error in debug screenshot function: {e}")

def save_immediate_debug(page, email: str, name: str):
    try:
        debug_dir = Path("/home/data/Project/9router/profiles").parent / "weavy_debug" / safe_email_to_dirname(email)
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        screenshot_path = debug_dir / f"{name}_{ts}.png"
        html_path = debug_dir / f"{name}_{ts}.html"
        
        log_step(f"Mengambil screenshot diagnostik '{name}'...")
        page.screenshot(path=str(screenshot_path), full_page=True, timeout=10000)
        try:
            html_path.write_text(page.content() or "", encoding="utf-8")
        except Exception:
            pass
        log_step(f"Screenshot diagnostik '{name}' disimpan di: {screenshot_path}")
    except Exception as e:
        logger.warning(f"Failed to save immediate debug {name}: {e}")

# --- Cloudflare bypass helper ---
def is_on_turnstile_page(page) -> bool:
    try:
        title = page.title() or ""
        if "just a moment" in title.lower() or "security verification" in title.lower():
            return True
    except Exception:
        pass

    # Check if the Cloudflare Turnstile token is already populated.
    # If it is, then the challenge has been resolved, so we are not blocked.
    try:
        token = page.evaluate("() => { const el = document.getElementsByName('cf-turnstile-response')[0] || document.getElementById('cf-turnstile-response'); return el ? el.value : null; }")
        if token is not None:
            return len(token.strip()) == 0
    except Exception:
        pass

    for sel in [
        "text=Just a moment",
        "text=Verifying you are human",
        "text=Verify you are human",
        "text=Checking your browser",
        "#challenge-form",
        "#cf-challenge-running",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=300):
                return True
        except Exception:
            continue

    try:
        for f in page.frames:
            url = f.url or ""
            if ("challenges.cloudflare.com" in url or "turnstile" in url) and "challenge-platform" in url:
                # If there's an iframe, we still check the response token.
                # If the token is populated, then Turnstile is resolved.
                token = page.evaluate("() => { const el = document.getElementsByName('cf-turnstile-response')[0]; return el ? el.value : ''; }")
                if token and len(token.strip()) > 0:
                    return False
                return True
    except Exception:
        pass
    return False

def try_click_turnstile_checkbox(page) -> bool:
    target_frame = None
    try:
        for f in page.frames:
            url = f.url or ""
            if "challenges.cloudflare.com" in url or "turnstile" in url:
                target_frame = f
                break
    except Exception:
        pass

    if target_frame:
        # Check if the iframe itself is actually visible (so we don't click invisible/background widgets)
        try:
            frame_element = page.locator("iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']").first
            if frame_element.count() > 0 and not frame_element.is_visible(timeout=500):
                log_step("Turnstile iframe terdeteksi tetapi invisible. Melewati klik.")
                return False
        except Exception:
            pass

        # Check if the checkbox inside the iframe is already checked
        for cb_sel in [
            "input[type='checkbox']",
            "[role='checkbox']",
        ]:
            try:
                box = target_frame.locator(cb_sel).first
                if box.count() > 0:
                    checked = box.get_attribute("aria-checked") or box.get_attribute("checked")
                    if checked == "true" or checked == "checked" or box.is_checked():
                        log_step("Turnstile checkbox sudah ter-centang/checked. Melewati klik.")
                        return True
            except Exception:
                pass

        for cb_sel in [
            "input[type='checkbox']",
            "label.cb-lb input",
            "[role='checkbox']",
            "div.ctp-checkbox-label",
        ]:
            try:
                box = target_frame.locator(cb_sel).first
                if box.count() > 0:
                    box.click(timeout=3000)
                    log_step(f"Turnstile checkbox di-click via target_frame.locator({cb_sel})")
                    return True
            except Exception:
                continue

        try:
            handle = target_frame.frame_element()
            bbox = handle.bounding_box() if handle else None
            if bbox:
                origin_x = bbox["x"]
                origin_y = bbox["y"]
                iframe_w = bbox["width"]
                iframe_h = bbox["height"]

                target_x = origin_x + 28
                target_y = origin_y + 32
                page.mouse.move(target_x, target_y, steps=10)
                time.sleep(0.3)
                page.mouse.click(target_x, target_y)
                log_step(f"Turnstile checkbox di-click via mouse di coordinate ({target_x}, {target_y})")
                return True
        except Exception as e_mouse:
            logger.debug(f"Turnstile mouse click error: {e_mouse}")

    for iframe_sel in [
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        "iframe[title*='Cloudflare' i]",
    ]:
        for cb_sel in [
            "input[type='checkbox']",
            "label.cb-lb input",
            "[role='checkbox']",
        ]:
            try:
                box = page.frame_locator(iframe_sel).locator(cb_sel).first
                if box.count() > 0:
                    box.click(timeout=3000)
                    log_step(f"Turnstile checkbox di-click via frame_locator fallback")
                    return True
            except Exception:
                continue
    return False

def wait_for_cf_clearance(page, timeout: float = 30.0):
    if not is_on_turnstile_page(page):
        return True

    log_step("Halaman Turnstile terdeteksi, menunggu resolve...")
    deadline = time.time() + timeout
    click_attempts = 0
    max_click_attempts = 4
    next_click_at = time.time() + 5.0

    while time.time() < deadline:
        time.sleep(2.0)
        if not is_on_turnstile_page(page):
            log_step("Turnstile selesai (resolved otomatis).")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            return True

        now = time.time()
        if click_attempts < max_click_attempts and now >= next_click_at:
            click_attempts += 1
            log_step(f"Mencoba klik Turnstile checkbox (Attempt {click_attempts}/{max_click_attempts})...")
            try_click_turnstile_checkbox(page)
            next_click_at = now + 8.0
            time.sleep(2.0)
            if not is_on_turnstile_page(page):
                log_step("Turnstile selesai setelah di-klik.")
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                return True

    log_step("Turnstile challenge timeout.")
    return False

def solve_and_inject_figma_captcha(page, captured_urls, settings, proxy_server=None, proxy_user=None, proxy_pass=None):
    log_step("Menunggu captcha Arkose Labs terintercept...")
    deadline = time.time() + 15.0
    frame_url = None
    while time.time() < deadline:
        for u in captured_urls:
            if "dataExchangeBlob=" in u:
                frame_url = u
                break
        if frame_url:
            break
        time.sleep(0.5)

    if not frame_url:
        log_step("Tidak ada Arkose frame URL terintercept dengan dataExchangeBlob.")
        return False

    log_step("Captcha Figma terdeteksi. Memulai proses pemecahan...")
    api_key = settings.get("codebuddy_2captcha_api_key")
    if not api_key:
        raise WeavyAutomationError("2Captcha API key tidak ditemukan di settings database")

    user_agent = None
    try:
        user_agent = page.evaluate("navigator.userAgent")
    except Exception:
        pass

    proxy_str = None
    proxy_type = None

    parsed = urllib.parse.urlparse(frame_url)
    query_params = urllib.parse.parse_qs(parsed.query)
    data_blob_raw = query_params.get("dataExchangeBlob", [None])[0]
    data_blob = data_blob_raw
    public_key = query_params.get("publicKey", ["A207F8A1-ED09-4325-ACE6-C8E26A458FBA"])[0]

    log_step(f"Public Key: {public_key}")
    if data_blob:
        log_step(f"Data Blob Length (raw): {len(data_blob_raw)}")
        log_step(f"Data Blob preview: {data_blob[:80]}...")
    else:
        log_step("Data Blob is empty/None")

    # Set page_url to the top-level page URL of Figma where the captcha is loaded
    target_page_url = page.url if "figma.com" in page.url else "https://www.figma.com/signup"
    log_step(f"Menggunakan page_url untuk 2Captcha: {target_page_url}")

    try:
        token = solve_funcaptcha(
            api_key=api_key,
            public_key=public_key,
            page_url=target_page_url,
            surl="https://figma-api.arkoselabs.com",
            data_blob=data_blob,
            user_agent=None,
            proxy_str=proxy_str,
            proxy_type=proxy_type,
        )
        log_step("Captcha berhasil di-solve!")
    except CaptchaSolverError as e:
        raise WeavyAutomationError(f"Gagal menyelesaikan captcha: {e}")

    log_step("Captcha solved. Mengirimkan token ke Figma...")
    captcha_frame = None
    for f in page.frames:
        if "verify.figma.com/arkose_frame.html" in f.url:
            captcha_frame = f
            break

    if captcha_frame:
        captcha_frame.evaluate(
            f"""
            parent.postMessage(JSON.stringify({{
                eventId: "challenge-complete",
                payload: {{
                    sessionToken: "{token}"
                }}
            }}), "*");
            """
        )
    else:
        log_step("Frame captcha tidak ditemukan, injeksi token via main page...")
        page.evaluate(
            f"""
            window.postMessage(JSON.stringify({{
                eventId: "challenge-complete",
                payload: {{
                    sessionToken: "{token}"
                }}
            }}), "*");
            """
        )
    time.sleep(5.0)
    return True

# --- Main execution flow ---
def run_automation(args, settings):
    email = args.email
    password = args.password
    profiles_dir = Path(args.profiles_dir)
    profile_dir = (profiles_dir / safe_email_to_dirname(email)).resolve()
    start_time = int(time.time()) - 15

    try:
        from camoufox.sync_api import Camoufox
        from camoufox.addons import DefaultAddons
    except ImportError as e:
        raise WeavyAutomationError(
            "Camoufox not installed. Run in venv: pip install camoufox && python -m camoufox fetch"
        ) from e

    if getattr(args, "clean", False) and profile_dir.exists():
        try:
            import shutil
            shutil.rmtree(str(profile_dir), ignore_errors=True)
            log_step("Konteks profile browser lama dibersihkan (profile dir deleted).")
        except Exception as e_clean:
            logger.debug(f"Failed to delete profile dir: {e_clean}")

    profile_dir.mkdir(parents=True, exist_ok=True)
    launch_kwargs = dict(
        headless=args.headless,
        persistent_context=True, no_viewport=True,
        user_data_dir=str(profile_dir),
        humanize=True,
        geoip=True,
        locale="en-US",
        os=("windows", "macos", "linux"),
        window=(1920, 1080),
        exclude_addons=[DefaultAddons.UBO],
        firefox_user_prefs={
            "network.trr.mode": 5,
        },
        no_viewport=True,
    )

    if args.proxy_server:
        proxy_dict = {"server": args.proxy_server}
        if args.proxy_user:
            proxy_dict["username"] = args.proxy_user
        if args.proxy_pass:
            proxy_dict["password"] = args.proxy_pass
        launch_kwargs["proxy"] = proxy_dict
        log_step(f"Menggunakan proxy: {args.proxy_server}")

    log_step("Meluncurkan browser...")
    
    # Try launching with adaptive kwargs for camoufox API versions
    browser = None
    try:
        browser = Camoufox(**launch_kwargs)
    except TypeError:
        for drop in ("window", "os", "geoip", "humanize"):
            launch_kwargs.pop(drop, None)
            try:
                browser = Camoufox(**launch_kwargs)
                break
            except TypeError:
                continue
        if not browser:
            launch_kwargs.pop("locale", None)
            browser = Camoufox(**launch_kwargs)

    browser_ctx = browser.__enter__()
    try:
        global _debug_context
        context = getattr(browser_ctx, "context", None) or browser_ctx
        _debug_context = context

        # Block Google One Tap requests to prevent click interception overlays
        try:
            context.route("**/gsi/**", lambda route: route.abort())
        except Exception as e_route:
            logger.debug(f"Failed to setup route block: {e_route}")

        # Tutup semua tab lama dari run sebelumnya (kecuali yang terakhir)
        try:
            existing_pages = context.pages
            if len(existing_pages) > 1:
                for old_page in existing_pages[:-1]:
                    try:
                        old_page.close()
                    except Exception:
                        pass
        except Exception:
            pass

        # Clear cookies if --clean flag is provided
        if getattr(args, "clean", False):
            try:
                context.clear_cookies()
                log_step("Konteks browser dibersihkan (cookies cleared).")
            except Exception as e_cookies:
                logger.debug(f"Clear cookies error: {e_cookies}")

        page = context.new_page()
        start_debug_screenshots(page)

        # Set up global request listener on context to capture Arkose Labs / FunCaptcha URLs and Weavy Auth JWT
        captured_urls = []
        captured_jwt = [None]
        captured_firebase_api_key = [None]
        def handle_request(req):
            url = req.url
            if "arkose" in url.lower() or "verify" in url.lower() or "dataexchange" in url.lower():
                log_step(f"Intercepted request: {url[:100]}")
                captured_urls.append(url)
            if "api.weavy.ai" in url:
                try:
                    headers = req.all_headers()
                    auth = headers.get("authorization")
                    if auth and auth.startswith("Bearer "):
                        token = auth.split(" ", 1)[1]
                        if token and len(token) > 20:
                            captured_jwt[0] = token
                except Exception:
                    pass
            # Capture Firebase API key from identitytoolkit or securetoken requests
            if not captured_firebase_api_key[0] and ("identitytoolkit.googleapis.com" in url or "securetoken.googleapis.com" in url):
                try:
                    import urllib.parse as _up
                    qs = _up.parse_qs(_up.urlparse(url).query)
                    key = qs.get("key", [None])[0]
                    if key and key.startswith("AIza"):
                        captured_firebase_api_key[0] = key
                except Exception:
                    pass

        context.on("request", handle_request)

        already_registered = getattr(args, "gsuite", False)
        
        if not getattr(args, "gsuite", False):
            # 1. Check if already logged in to Figma
            log_step("Membuka Figma untuk mengecek status login...")
            page.goto("https://www.figma.com/")
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass

            url = page.url
            if "/files" in url or "/dashboard" in url or "canvas" in url:
                log_step("Figma sudah login (halaman dashboard). Melewati login & register.")
                already_registered = True
            
            if not already_registered:
                signup_success = False
                fallback_to_login = False
                
                for sa_attempt in range(1, 4):
                    log_step(f"Memulai pendaftaran Figma (Percobaan {sa_attempt}/3)...")
                    if sa_attempt > 1:
                        try:
                            context.clear_cookies()
                            log_step("Cookies dibersihkan untuk pendaftaran baru.")
                        except Exception:
                            pass
                    
                    try:
                        captured_urls.clear()
                        # Reset browser storage state to prevent telemetry tracking across retries
                        try:
                            context.clear_cookies()
                            page.goto("about:blank")
                            page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e){} }")
                            log_step("Browser storage (cookies, localStorage, sessionStorage) dibersihkan.")
                        except Exception as e_clear:
                            logger.debug(f"Failed to clear storage: {e_clear}")

                        log_step("Navigasi langsung ke Figma signup page...")
                        page.goto(FIGMA_SIGNUP_URL)
                        try:
                            page.wait_for_load_state("networkidle", timeout=6000)
                        except Exception:
                            pass

                        time.sleep(2.0)

                        # Close cookie banner if present
                        try:
                            cookie_btn = page.locator("button:has-text('Allow all cookies')").first
                            if cookie_btn.count() > 0 and cookie_btn.is_visible():
                                log_step("Cookie banner terdeteksi. Menutup cookie banner...")
                                cookie_btn.click(timeout=2000, force=True)
                                time.sleep(1.0)
                        except Exception:
                            pass

                        # Remove Google One Tap widget to prevent interception
                        try:
                            page.evaluate("() => { const el = document.getElementById('google_one_tap'); if (el) el.remove(); }")
                        except Exception:
                            pass

                        log_step("Mengisi email Figma untuk register...")
                        if not fill_first(page, FIGMA_EMAIL_INPUT_SELECTORS, email, timeout_ms=8000):
                            raise WeavyAutomationError("Email input figma tidak ditemukan")

                        # Check if password field is already visible (single-step signup form)
                        password_visible = False
                        for sel in FIGMA_PASSWORD_SELECTORS:
                            try:
                                loc = page.locator(sel).first
                                if loc.count() > 0 and loc.is_visible(timeout=500):
                                    password_visible = True
                                    break
                            except Exception:
                                pass
                        
                        if not password_visible:
                            # Click Continue to show password (multi-step signup form)
                            if not click_first(page, FIGMA_CONTINUE_EMAIL_SELECTORS, timeout_ms=5000):
                                raise WeavyAutomationError("Tombol continue email figma tidak ditemukan")
                            time.sleep(2.0)

                        log_step("Mengisi password Figma untuk register...")
                        if not fill_first(page, FIGMA_PASSWORD_SELECTORS, password, timeout_ms=8000):
                            raise WeavyAutomationError("Password input figma tidak ditemukan")

                        # Try to prefill Name if asked
                        try:
                            name_loc = page.locator("input[name='name']").first
                            if name_loc.count() > 0 and name_loc.is_visible(timeout=500):
                                name_loc.fill("Jane Doe")
                        except Exception:
                            pass

                        log_step("Mengirimkan pendaftaran Figma...")
                        if not click_first(page, FIGMA_SUBMIT_SELECTORS, timeout_ms=5000):
                            page.keyboard.press("Enter")

                        time.sleep(4.0)
                        
                        # CHECK IF EMAIL ALREADY EXISTS ON FIGMA SIGNUP PAGE
                        try:
                            err_msg = page.locator("text=already exists, text=already registered, text=Choose another email, text=Log in instead, text=incorrect password").first
                            if err_msg.count() > 0 and err_msg.is_visible(timeout=1500):
                                log_step("Email sudah terdaftar di Figma. Beralih ke flow login...")
                                fallback_to_login = True
                                break
                        except Exception:
                            pass
                        
                        save_immediate_debug(page, email, "post_submit")

                        # Check if autologged in on submit
                        url = page.url
                        if "/files" in url or "/dashboard" in url or "canvas" in url:
                            log_step("Terdeteksi akun Figma sudah terdaftar dan masuk dashboard.")
                            signup_success = True
                            break

                        log_step("Menunggu validasi captcha Arkose Labs atau redirect...")
                        deadline = time.time() + 20.0
                        has_captcha = False
                        start_btn_clicked = False
                        puzzle_found_frame = None
                        loop_start = time.time()

                        while time.time() < deadline:
                            url = page.url
                            if "/files" in url or "/dashboard" in url or "canvas" in url:
                                 signup_success = True
                                 break
                            
                            if "figma.com/verify" in url or "verify?fuid=" in url:
                                 log_step("Halaman verifikasi email Figma terdeteksi.")
                                 break

                            # Check if figma captcha iframe is visible
                            try:
                                captcha_frame_visible = False
                                for f in page.frames:
                                    if "verify.figma.com/arkose_frame.html" in f.url:
                                        frame_element = page.locator("iframe[src*='verify.figma.com']").first
                                        if frame_element.count() > 0 and frame_element.is_visible(timeout=300):
                                            captcha_frame_visible = True
                                            break
                                if captcha_frame_visible:
                                    has_captcha = True
                                    break
                            except Exception:
                                pass

                            # Look for puzzle frame or Start puzzle button
                            all_frames = [page] + list(page.frames)
                            for f in all_frames:
                                try:
                                    for sel in [
                                        "button:has-text('Start puzzle')",
                                        "button:has-text('Start')",
                                        "[class*='puzzle']",
                                        "text=Verification",
                                    ]:
                                        loc = f.locator(sel).first
                                        if loc.count() > 0 and loc.is_visible(timeout=300):
                                            puzzle_found_frame = f
                                            break
                                    if puzzle_found_frame:
                                        break
                                except Exception:
                                    continue

                            # Click Start puzzle if found
                            if puzzle_found_frame and not start_btn_clicked:
                                try:
                                    start_btn = puzzle_found_frame.locator("button:has-text('Start puzzle')").first
                                    if start_btn.count() == 0:
                                        start_btn = puzzle_found_frame.locator("button:has-text('Start')").first
                                    if start_btn.count() > 0 and start_btn.is_visible(timeout=1000):
                                        log_step("Klik 'Start puzzle' pada pendaftaran...")
                                        start_btn.click(timeout=5000)
                                        start_btn_clicked = True
                                        time.sleep(3.0)
                                        save_immediate_debug(page, email, "after_reg_puzzle_click")
                                except Exception as e_click:
                                    logger.debug(f"Gagal klik Start puzzle pendaftaran: {e_click}")

                            # Fallback check if captcha is preloaded and we've been waiting for more than 7 seconds on the signup page
                            if time.time() > loop_start + 7.0 and any("dataExchangeBlob=" in u for u in captured_urls):
                                has_captcha = True
                                break

                            time.sleep(0.5)
                            update_debug_screenshot(page)

                        if signup_success:
                            break

                        if has_captcha and not ("figma.com/verify" in page.url or "verify?fuid=" in page.url):
                            solve_and_inject_figma_captcha(
                                page=page,
                                captured_urls=captured_urls,
                                settings=settings,
                                proxy_server=args.proxy_server,
                                proxy_user=args.proxy_user,
                                proxy_pass=args.proxy_pass
                            )

                        save_immediate_debug(page, email, "pre_verification_wait")
                        log_step("Menunggu email verifikasi Figma dari Ammail...")
                        since_ts = start_time
                        _, verify_url = wait_for_otp_from_db(email, since_ts, settings, timeout=180)
                        if not verify_url:
                            raise WeavyAutomationError("Link verifikasi Figma tidak ditemukan di email (Timeout)")

                        log_step(f"Membuka link verifikasi: {verify_url}")
                        page.goto(verify_url)
                        try:
                            page.wait_for_load_state("load", timeout=12000)
                        except Exception:
                            pass
                        time.sleep(4.0)

                        complete_figma_onboarding(page, email)
                        signup_success = True
                        break
                    except Exception as e_sa:
                        log_step(f"Percobaan pendaftaran {sa_attempt} gagal: {e_sa}")
                        if sa_attempt == 3:
                            raise e_sa

                if fallback_to_login:
                    # Try to log in
                    log_step("Membuka Figma login page...")
                    page.goto("https://www.figma.com/login")
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    
                    log_step("Mengisi email Figma...")
                    if fill_first(page, FIGMA_EMAIL_INPUT_SELECTORS, email, timeout_ms=8000):
                        # Check if password field is already visible (single-step login form)
                        password_visible = False
                        for sel in FIGMA_PASSWORD_SELECTORS:
                            try:
                                loc = page.locator(sel).first
                                if loc.count() > 0 and loc.is_visible(timeout=500):
                                    password_visible = True
                                    break
                            except Exception:
                                pass
                        
                        if not password_visible:
                            # Click Continue to show password (multi-step login form)
                            click_first(page, FIGMA_CONTINUE_EMAIL_SELECTORS, timeout_ms=5000)
                            time.sleep(2.0)
                        
                        log_step("Mengisi password Figma...")
                        fill_first(page, FIGMA_PASSWORD_SELECTORS, password, timeout_ms=8000)
                        
                        log_step("Mengirimkan login Figma...")
                        if not click_first(page, FIGMA_SUBMIT_SELECTORS, timeout_ms=5000):
                            page.keyboard.press("Enter")
                        
                        time.sleep(4.0)
                        
                        # Check if login succeeded
                        url = page.url
                        if "/files" in url or "/dashboard" in url or "canvas" in url:
                            log_step("Login Figma sukses.")
                            already_registered = True
                        else:
                            # Wait a bit or check for captcha
                            log_step("Menunggu redirect login atau captcha...")
                            deadline = time.time() + 15.0
                            has_captcha = False
                            while time.time() < deadline:
                                url = page.url
                                if "/files" in url or "/dashboard" in url or "canvas" in url:
                                    already_registered = True
                                    break
                                has_captcha = any("dataExchangeBlob=" in u for u in captured_urls)
                                if has_captcha:
                                    break
                                time.sleep(0.5)
                            
                            if has_captcha and not already_registered:
                                log_step("Captcha terdeteksi saat login. Memulai solver...")
                                solve_and_inject_figma_captcha(
                                    page=page,
                                    captured_urls=captured_urls,
                                    settings=settings,
                                    proxy_server=args.proxy_server,
                                    proxy_user=args.proxy_user,
                                    proxy_pass=args.proxy_pass
                                )
                                time.sleep(5.0)
                                url = page.url
                                if "/files" in url or "/dashboard" in url or "canvas" in url:
                                    already_registered = True
                                    log_step("Login Figma sukses setelah captcha solved.")

        # Re-launch clean tab for oauth
        try:
            page.close()
        except Exception:
            pass

        page = context.new_page()
        start_debug_screenshots(page)
        try:
            page.on("dialog", lambda dialog: dialog.dismiss())
        except Exception:
            pass

        # 5. Weavy Signup Phase
        log_step("Membuka halaman Weavy signin...")
        try:
            page.goto(WEAVY_SIGNIN_URL, timeout=30000)
        except Exception as e_goto:
            logger.debug(f"Weavy signin page.goto timeout/error (expected if Cloudflare blocks): {e_goto}")
        # Tunggu Cloudflare Turnstile challenge selesai (Camoufox auto-solve)
        wait_for_cf_clearance(page, timeout=30.0)
        time.sleep(2.0)

        url = page.url
        if "weavy" in url and "signin" not in url and "oauth" not in url:
            log_step(f"Terdeteksi sudah masuk ke dashboard Weavy: {url}")
        else:
            if getattr(args, "gsuite", False):
                # -----------------
                # Direct Google Flow on Weavy (completely bypassing Figma)
                # -----------------
                log_step("Mengklik tombol Log in with Google di Weavy...")
                btn_locator = None
                wait_deadline = time.time() + 25.0
                while time.time() < wait_deadline:
                    for sel in [
                        "button:has-text('Log in with Google')",
                        "button[aria-label*='Google' i]",
                        "a:has-text('Log in with Google')",
                        "div[role='button']:has-text('Log in with Google')",
                    ]:
                        try:
                            loc = page.locator(sel).first
                            if loc.count() > 0 and loc.is_visible(timeout=300):
                                btn_locator = loc
                                break
                        except Exception:
                            continue
                    if btn_locator:
                        break
                    time.sleep(0.5)

                if not btn_locator:
                    raise WeavyAutomationError("Tombol Log in with Google tidak ditemukan")
                
                with page.expect_popup() as popup_info:
                    btn_locator.click(timeout=5000)
                popup = popup_info.value
                popup.wait_for_load_state()
                time.sleep(2.0)
                
                # Handle Google popup directly
                log_step("Menangani Google OAuth popup...")
                google_deadline = time.time() + 90.0
                filled_email = False
                filled_pass = False
                clicked_account = False
                popup_oauth_done = False
                while time.time() < google_deadline:
                    # Check if MAIN page already redirected (postMessage / server-side callback worked)
                    try:
                        main_url = page.url or ""
                        if "app.weavy.ai" in main_url and "signin" not in main_url and "oauth" not in main_url:
                            log_step(f"Halaman utama sudah di dashboard: {main_url}")
                            popup_oauth_done = True
                            try:
                                if not popup.is_closed():
                                    popup.close()
                            except Exception:
                                pass
                            break
                    except Exception:
                        pass

                    if popup.is_closed():
                        log_step("Popup Google tertutup.")
                        break
                    try:
                        popup_url = popup.url or ""
                        log_step(f"[popup] {popup_url[:100]}")

                        # Popup reached Weavy dashboard — OAuth callback processed successfully
                        if ("app.weavy.ai" in popup_url and
                                "signin" not in popup_url and
                                "callback" not in popup_url and
                                "oauth" not in popup_url):
                            log_step(f"Popup mencapai Weavy dashboard: {popup_url}")
                            popup_oauth_done = True
                            try:
                                popup.close()
                            except Exception:
                                pass
                            break

                        # Check if Google account is disabled / speedbumped
                        if "speedbump/disabled" in popup_url or "disabled" in popup_url or "disabled" in (popup.title() or "").lower():
                            raise WeavyAutomationError(f"Akun Google ({email}) dinonaktifkan/disabled oleh Google.")

                        # Account chooser
                        if not clicked_account and (
                            "accountchooser" in popup_url or
                            "Choose an account" in (popup.title() or "")
                        ):
                            clicked = popup.evaluate(f"""
                                () => {{
                                    const email = '{email}';
                                    const lis = document.querySelectorAll('li[data-identifier], li.JDAKTe');
                                    for (const li of lis) {{
                                        const id = li.getAttribute('data-identifier') || '';
                                        if (id === email || li.textContent.includes(email)) {{
                                            li.click(); return true;
                                        }}
                                    }}
                                    const all = document.querySelectorAll('[data-email], [data-identifier]');
                                    for (const el of all) {{
                                        const v = el.getAttribute('data-email') || el.getAttribute('data-identifier') || '';
                                        if (v === email) {{ el.click(); return true; }}
                                    }}
                                    const rows = document.querySelectorAll('ul li');
                                    if (rows.length > 0) {{ rows[0].click(); return true; }}
                                    return false;
                                }}
                            """)
                            if clicked:
                                log_step(f"JS klik akun '{email}' di account chooser...")
                                clicked_account = True
                                time.sleep(3.0)
                                continue

                        # Email input
                        if not filled_email:
                            email_in = popup.locator("input[type='email'], input[name='identifier']").first
                            if email_in.count() > 0 and email_in.is_visible(timeout=500):
                                log_step("Mengisi email GSuite di popup Google...")
                                email_in.fill(email)
                                popup.keyboard.press("Enter")
                                filled_email = True
                                time.sleep(3.0)
                                continue

                        # Password input
                        if filled_email and not filled_pass:
                            pass_in = popup.locator("input[type='password'], input[name='Passwd']").first
                            if pass_in.count() > 0 and pass_in.is_visible(timeout=500):
                                log_step("Mengisi password GSuite di popup Google...")
                                pass_in.click(timeout=2000)
                                pass_in.fill(password)
                                time.sleep(0.5)
                                # Try clicking the Next button explicitly (more reliable than Enter)
                                next_clicked = False
                                for next_sel in [
                                    "button:has-text('Next')",
                                    "button[type='submit']",
                                    "#passwordNext",
                                    "div[id='passwordNext'] button",
                                ]:
                                    try:
                                        nb = popup.locator(next_sel).first
                                        if nb.count() > 0 and nb.is_visible(timeout=500):
                                            nb.click(timeout=3000)
                                            next_clicked = True
                                            log_step(f"Klik tombol '{next_sel}' setelah isi password")
                                            break
                                    except Exception:
                                        pass
                                if not next_clicked:
                                    popup.keyboard.press("Enter")
                                filled_pass = True
                                time.sleep(4.0)
                                continue

                        # Google post-login challenges (phone, 2FA, identity verify)
                        if filled_pass:
                            popup_url_now = popup.url or ""
                            # "Try another way" or "Continue" on challenge page
                            for challenge_sel in [
                                "button:has-text('Try another way')",
                                "button:has-text('Skip')",
                                "button:has-text('Not now')",
                                "button:has-text('Remind me later')",
                                "button:has-text('Continue')",
                                "button:has-text('Done')",
                                "a:has-text('Skip')",
                            ]:
                                try:
                                    cb = popup.locator(challenge_sel).first
                                    if cb.count() > 0 and cb.is_visible(timeout=300):
                                        log_step(f"Google challenge: klik '{challenge_sel}'")
                                        cb.click(timeout=3000)
                                        time.sleep(2.0)
                                        break
                                except Exception:
                                    pass

                        # "I understand" workspace terms
                        understand_btn = popup.locator(
                            "button:has-text('I understand'), "
                            "button:has-text('I Understand')"
                        ).first
                        if understand_btn.count() > 0 and understand_btn.is_visible(timeout=500):
                            log_step("Klik 'I understand' di Google Workspace terms...")
                            understand_btn.click(timeout=3000)
                            time.sleep(2.0)
                            continue

                        # Handle any remaining Continue/Allow/Next button
                        for btn_text in ["Continue", "Allow", "Next", "Accept", "Confirm"]:
                            try:
                                b = popup.locator(f"button:has-text('{btn_text}')").first
                                if b.count() > 0 and b.is_visible(timeout=300):
                                    log_step(f"Klik '{btn_text}' di popup Google...")
                                    b.click(timeout=3000)
                                    time.sleep(2.0)
                                    break
                            except Exception:
                                pass

                    except WeavyAutomationError as e_wa:
                        raise e_wa
                    except Exception as e_popup:
                        logger.debug(f"Google popup handler: {e_popup}")
                    time.sleep(1.0)

                # Log final popup URL before it closes (for debugging)
                try:
                    if not popup.is_closed():
                        log_step(f"[popup final URL] {popup.url}")
                except Exception:
                    pass

                # After popup closes/completes, wait for main page to update via Firebase postMessage
                if not popup_oauth_done:
                    log_step("Popup Google selesai. Menunggu Firebase auth state update di halaman utama...")

                # Phase 1: Watch for natural redirect (Firebase postMessage → Weavy router)
                # Don't navigate! Let Firebase SDK process the popup result first.
                phase1_deadline = time.time() + 25.0
                phase1_done = False
                while time.time() < phase1_deadline:
                    try:
                        current = page.url or ""
                        if "app.weavy.ai" in current and "signin" not in current and "oauth" not in current:
                            log_step(f"Firebase auth redirect terdeteksi: {current}")
                            phase1_done = True
                            break
                    except Exception:
                        pass
                    time.sleep(1.5)
                    update_debug_screenshot(page)

                if phase1_done:
                    pass  # Already on dashboard
                else:
                    # Phase 2: Force navigate (fallback for cases where postMessage didn't trigger redirect)
                    log_step("Halaman utama tidak redirect otomatis. Coba force navigate...")
                    for nav_attempt in range(1, 4):
                        try:
                            current = page.url or ""
                            if "app.weavy.ai" in current and "signin" not in current and "oauth" not in current:
                                log_step(f"Berhasil masuk: {current}")
                                break
                            log_step(f"Force navigate attempt {nav_attempt}/3 ke app.weavy.ai...")
                            page.goto("https://app.weavy.ai/", timeout=20000)
                            wait_for_cf_clearance(page, timeout=15.0)
                            time.sleep(5.0)
                            update_debug_screenshot(page)
                        except Exception as e_nav:
                            logger.debug(f"Nav attempt {nav_attempt} error: {e_nav}")
                            time.sleep(2.0)


            else:
                # -----------------
                # Regular Figma Flow
                # -----------------
                log_step("Mengklik tombol Log in with Figma...")
                btn_locator = None
                wait_deadline = time.time() + 25.0
                while time.time() < wait_deadline:
                    for sel in WEAVY_FIGMA_LOGIN_SELECTORS:
                        try:
                            loc = page.locator(sel).first
                            if loc.count() > 0 and loc.is_visible(timeout=300):
                                btn_locator = loc
                                break
                        except Exception:
                            continue
                    if btn_locator:
                        break
                    time.sleep(0.5)

                btn_clicked = False
                if btn_locator:
                    try:
                        btn_locator.click(timeout=5000)
                        btn_clicked = True
                    except Exception:
                        pass

                # Tunggu navigasi/transisi URL setelah klik (bisa ke figma.com atau Weavy dashboard)
                log_step("Menunggu transisi URL setelah klik login...")
                nav_deadline = time.time() + 15.0
                while time.time() < nav_deadline:
                    url = page.url
                    if "figma.com" in url or ("weavy" in url and "signin" not in url and "oauth" not in url):
                        break
                    time.sleep(0.5)
                    update_debug_screenshot(page)

                url = page.url
                if not btn_clicked and "figma.com" not in url and not ("weavy" in url and "signin" not in url):
                    raise WeavyAutomationError("Tombol Log in with Figma tidak ditemukan")

                if "weavy" in url and "signin" not in url and "oauth" not in url:
                    log_step(f"Sudah di dashboard Weavy: {url}")
                else:
                    save_immediate_debug(page, email, "after_login_click")

                    # Handle figma login with email/password (if not logged in)
                    ready_deadline = time.time() + 10.0
                    while time.time() < ready_deadline:
                        if "figma.com/login" in page.url or "figma.com/signup" in page.url:
                            break
                        time.sleep(0.5)
                        update_debug_screenshot(page)

                    current_url = page.url
                    if "figma.com/login" in current_url or "figma.com/signup" in current_url:
                        log_step(f"Diarahkan ke halaman login Figma: {current_url}. Mengisi email/password...")
                        if not fill_first(page, FIGMA_EMAIL_INPUT_SELECTORS, email, timeout_ms=8000):
                            raise WeavyAutomationError("Email input figma tidak ditemukan")
                        if not click_first(page, FIGMA_CONTINUE_EMAIL_SELECTORS, timeout_ms=5000):
                            raise WeavyAutomationError("Tombol continue email figma tidak ditemukan")
                        time.sleep(2.0)
                        if not fill_first(page, FIGMA_PASSWORD_SELECTORS, password, timeout_ms=8000):
                            raise WeavyAutomationError("Password input figma tidak ditemukan")
                        if not click_first(page, FIGMA_SUBMIT_SELECTORS, timeout_ms=5000):
                            page.keyboard.press("Enter")
                        time.sleep(4.0)

                    # Handle Figma anti-bot verification puzzle (dalam iframe)
                    log_step("Menunggu captcha figma atau redirect ke dashboard...")
                    puzzle_deadline = time.time() + 25.0
                    puzzle_found_frame = None
                    start_btn_clicked = False
                    has_captcha = False

                    while time.time() < puzzle_deadline:
                        # Check if already authorized or redirected
                        current_url = page.url
                        if "weavy" in current_url and "signin" not in current_url and "oauth" not in current_url:
                            break
                        
                        # Handle Figma blocked page (anti-bot false positive during OAuth redirection)
                        if "figma.com/blocked.html" in current_url:
                            log_step("Figma blocked page terdeteksi saat OAuth. Mengulangi login Figma...")
                            page.goto("https://www.figma.com/login")
                            try:
                                page.wait_for_load_state("networkidle", timeout=6000)
                            except Exception:
                                pass
                            
                            log_step("Mengisi email Figma...")
                            if fill_first(page, FIGMA_EMAIL_INPUT_SELECTORS, email, timeout_ms=8000):
                                click_first(page, FIGMA_CONTINUE_EMAIL_SELECTORS, timeout_ms=5000)
                                time.sleep(2.0)
                                log_step("Mengisi password Figma...")
                                fill_first(page, FIGMA_PASSWORD_SELECTORS, password, timeout_ms=8000)
                                log_step("Mengirimkan login Figma...")
                                if not click_first(page, FIGMA_SUBMIT_SELECTORS, timeout_ms=5000):
                                    page.keyboard.press("Enter")
                                time.sleep(5.0)
                            
                            # Navigate back to Weavy login to retry OAuth handshake
                            log_step("Kembali ke Weavy signin untuk retry OAuth handshake...")
                            page.goto(WEAVY_SIGNIN_URL)
                            try:
                                page.wait_for_load_state("networkidle", timeout=8000)
                            except Exception:
                                pass
                            wait_for_cf_clearance(page, timeout=30.0)
                            click_first(page, WEAVY_FIGMA_LOGIN_SELECTORS, timeout_ms=8000)
                            time.sleep(4.0)
                            continue

                        # Check for figma oauth allow button (meaning captcha was bypassed or solved)
                        try:
                            allow_loc = page.locator("button:text('Allow access'), button:text('Allow')").first
                            if allow_loc.count() > 0 and allow_loc.is_visible(timeout=500):
                                break
                        except Exception:
                            pass

                        # Look for puzzle frame or Start puzzle button
                        all_frames = [page] + list(page.frames)
                        for f in all_frames:
                            try:
                                for sel in [
                                    "button:has-text('Start puzzle')",
                                    "button:has-text('Start')",
                                    "[class*='puzzle']",
                                    "text=Verification",
                                ]:
                                    loc = f.locator(sel).first
                                    if loc.count() > 0 and loc.is_visible(timeout=500):
                                        puzzle_found_frame = f
                                        break
                                if puzzle_found_frame:
                                    break
                            except Exception:
                                continue

                        # If we found the puzzle frame and haven't clicked the button yet
                        if puzzle_found_frame and not start_btn_clicked:
                            try:
                                start_btn = puzzle_found_frame.locator("button:has-text('Start puzzle')").first
                                if start_btn.count() == 0:
                                    start_btn = puzzle_found_frame.locator("button:has-text('Start')").first
                                if start_btn.count() > 0 and start_btn.is_visible(timeout=1000):
                                    log_step("Klik 'Start puzzle'...")
                                    start_btn.click(timeout=5000)
                                    start_btn_clicked = True
                                    time.sleep(3.0)
                                    save_immediate_debug(page, email, "after_puzzle_click")
                            except Exception as e_click:
                                logger.debug(f"Gagal mengklik Start puzzle: {e_click}")

                        # Check if any dataExchangeBlob is captured
                        has_captcha = any("dataExchangeBlob=" in u for u in captured_urls)
                        if has_captcha:
                            break

                        # Check if "Reload Captcha" is shown (captcha loading failed)
                        try:
                            for f in all_frames:
                                reload_btn = f.locator("button:has-text('Reload Captcha')").first
                                if reload_btn.count() > 0 and reload_btn.is_visible(timeout=500):
                                    log_step("Tombol 'Reload Captcha' terdeteksi. Mengklik reload...")
                                    reload_btn.click(timeout=3000)
                                    time.sleep(3.0)
                                    break
                        except Exception:
                            pass

                        time.sleep(1.0)
                        update_debug_screenshot(page)

                    if has_captcha:
                        solve_and_inject_figma_captcha(
                            page=page,
                            captured_urls=captured_urls,
                            settings=settings,
                            proxy_server=args.proxy_server,
                            proxy_user=args.proxy_user,
                            proxy_pass=args.proxy_pass
                        )
                        log_step("Menunggu verifikasi captcha selesai...")
                        time.sleep(5.0)

                    # Handle Figma OAuth Allow access (for standard flow)
                    log_step("Mengklik Allow access pada Figma OAuth (jika ada)...")
                    if click_first(page, FIGMA_OAUTH_ALLOW_SELECTORS, timeout_ms=15000):
                        log_step("Figma OAuth allow access di-klik.")
                    else:
                        log_step("Tombol Allow access Figma OAuth tidak ditemukan. Mungkin auto-authorize?")



        log_step("Menunggu redirect ke Weavy dashboard...")
        deadline = time.time() + 90.0
        redirected = False
        while time.time() < deadline:
            url = page.url
            if "weavy" in url and "signin" not in url and "oauth" not in url:
                redirected = True
                break
            time.sleep(1.0)
            update_debug_screenshot(page)

        if not redirected:
            raise WeavyAutomationError(f"Gagal redirect ke Weavy dashboard. URL akhir: {page.url}")

        log_step("Berhasil masuk Weavy dashboard. Mengekstrak cookies...")
        cookies_list = page.context.cookies()
        weavy_cookies = []
        for c in cookies_list:
            domain = c.get("domain") or ""
            if "weavy.ai" in domain:
                weavy_cookies.append(f"{c.get('name')}={c.get('value')}")
        
        cookies_str = "; ".join(weavy_cookies)
        if not cookies_str:
            cookies_str = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies_list)

        # Extract Firebase refresh token from IndexedDB (Firebase SDK v9+ stores here, not localStorage)
        firebase_refresh_token = None
        try:
            fb_refresh = page.evaluate("""
                () => new Promise((resolve) => {
                    try {
                        // Firebase v9+ stores auth in IndexedDB
                        const req = indexedDB.open('firebaseLocalStorageDb');
                        req.onsuccess = (e) => {
                            try {
                                const db = e.target.result;
                                const storeName = db.objectStoreNames.contains('firebaseLocalStorage')
                                    ? 'firebaseLocalStorage' : db.objectStoreNames[0];
                                if (!storeName) { resolve(null); return; }
                                const tx = db.transaction(storeName, 'readonly');
                                const store = tx.objectStore(storeName);
                                const getAllReq = store.getAll();
                                getAllReq.onsuccess = (e) => {
                                    const items = e.target.result || [];
                                    for (const item of items) {
                                        const v = item.value || item;
                                        if (v && v.stsTokenManager && v.stsTokenManager.refreshToken) {
                                            resolve(v.stsTokenManager.refreshToken);
                                            return;
                                        }
                                    }
                                    resolve(null);
                                };
                                getAllReq.onerror = () => resolve(null);
                            } catch(e2) { resolve(null); }
                        };
                        req.onerror = () => {
                            // Fallback: try localStorage (Firebase v8 compat)
                            try {
                                for (const key of Object.keys(localStorage || {})) {
                                    if (key.startsWith('firebase:authUser:')) {
                                        const u = JSON.parse(localStorage.getItem(key) || '{}');
                                        const rt = (u && u.stsTokenManager && u.stsTokenManager.refreshToken);
                                        if (rt) { resolve(rt); return; }
                                    }
                                }
                            } catch(e3) {}
                            resolve(null);
                        };
                    } catch(e) { resolve(null); }
                })
            """)
            if fb_refresh:
                firebase_refresh_token = fb_refresh
                log_step("Firebase refresh token berhasil ditangkap dari IndexedDB!")
            else:
                log_step("Firebase refresh token tidak ditemukan di IndexedDB/localStorage.")
        except Exception as e_fb:
            logger.debug(f"Failed to extract Firebase refresh token: {e_fb}")


        # Also try to capture Firebase API key from page meta/script if not yet captured
        if not captured_firebase_api_key[0]:
            try:
                fb_key = page.evaluate("""
                    () => {
                        // Try various global config locations
                        if (window.__FIREBASE_CONFIG__ && window.__FIREBASE_CONFIG__.apiKey) return window.__FIREBASE_CONFIG__.apiKey;
                        if (window._env && window._env.FIREBASE_API_KEY) return window._env.FIREBASE_API_KEY;
                        if (window.NEXT_PUBLIC_FIREBASE_API_KEY) return window.NEXT_PUBLIC_FIREBASE_API_KEY;
                        // Scan meta tags
                        const metas = document.querySelectorAll('meta[name*="firebase"], meta[name*="FIREBASE"]');
                        for (const m of metas) {
                            const v = m.getAttribute('content') || '';
                            if (v.startsWith('AIza')) return v;
                        }
                        return null;
                    }
                """)
                if fb_key:
                    captured_firebase_api_key[0] = fb_key
            except Exception:
                pass

        balance = 150.0

        # Success result output
        sys.stdout.write(json.dumps({
            "status": "success",
            "cookie": cookies_str,
            "balance": balance,
            "jwt": captured_jwt[0] or "",
            "firebase_refresh_token": firebase_refresh_token or "",
            "firebase_api_key": captured_firebase_api_key[0] or ""
        }, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception as e:
        try:
            save_debug_screenshots(context, args.profiles_dir, args.email, prefix="fail")
        except Exception:
            pass
        raise e
    finally:
        browser.__exit__(None, None, None)

def main():
    parser = argparse.ArgumentParser(description="Weavy Auto-signup Standalone")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--profiles-dir", required=True)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--proxy-server")
    parser.add_argument("--proxy-user")
    parser.add_argument("--proxy-pass")
    parser.add_argument("--gsuite", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    # Auto-detect GSuite: jika domain email BUKAN domain ammail → langsung GSuite (Google OAuth di Weavy)
    email_lower = (args.email or "").strip().lower()
    settings = load_settings_db()

    if not args.gsuite:
        email_domain = email_lower.split("@")[-1] if "@" in email_lower else ""

        # Kumpulkan semua known ammail domains dari settings
        known_ammail_domains = set()
        for key in ("ammail_default_domain", "ammail_cf_domain"):
            d = (settings.get(key) or "").strip().lower()
            if d:
                known_ammail_domains.add(d)

        if email_domain and email_domain not in known_ammail_domains:
            # Domain tidak ada di ammail → ini akun eksternal (Google/GSuite)
            log_step(f"Domain '{email_domain}' bukan domain ammail → Google OAuth (GSuite) flow.")
            args.gsuite = True
        else:
            log_step(f"Domain '{email_domain}' adalah domain ammail → Figma signup flow.")

    try:
        run_automation(args, settings)
    except Exception as exc:
        if _debug_context:
            try:
                save_debug_screenshots(_debug_context, args.profiles_dir, args.email, prefix="fail")
            except Exception:
                pass
        sys.stdout.write(json.dumps({
            "status": "error",
            "message": str(exc)
        }, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        sys.exit(1)
    finally:
        stop_debug_screenshots()

if __name__ == "__main__":
    main()
