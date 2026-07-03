#!/usr/bin/env python3
"""Standalone script to automate Kimi Coding auto-login via Google/GSuite OAuth.
Outputs step logs and final results to stdout as JSON lines.
"""

import sys
import json
import argparse
import time
import logging
import re
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

# Setup logger
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("kimi_signup")

class KimiAutomationError(RuntimeError):
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

def request_device_code() -> dict:
    url = "https://auth.kimi.com/api/oauth/device_authorization"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    data = urllib.parse.urlencode({"client_id": "17e5f671-d194-4dfb-9706-5516cb48c098"}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise KimiAutomationError(f"Gagal mengambil device code Kimi: {e}")

def click_first(page, selectors, timeout_ms: int = 8000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    try:
                        loc.scroll_into_view_if_needed(timeout=600)
                        loc.click(timeout=2000, force=True)
                        return True
                    except Exception:
                        handle = loc.element_handle(timeout=300)
                        if handle:
                            page.evaluate("(el) => el.click()", handle)
                            return True
            except Exception:
                continue
        time.sleep(0.3)
    return False

def fill_first(page, selectors, value: str, timeout_ms: int = 8000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    try:
                        loc.fill("", timeout=500)
                    except Exception:
                        pass
                    loc.fill(value, timeout=2000)
                    return True
            except Exception:
                continue
        time.sleep(0.3)
    return False

def poll_token(device_code: str, timeout: int = 120, interval: int = 5) -> dict:
    url = "https://auth.kimi.com/api/oauth/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": "17e5f671-d194-4dfb-9706-5516cb48c098",
        "device_code": device_code
    }
    
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                if "access_token" in res_data:
                    return res_data
        except urllib.error.HTTPError as he:
            try:
                err_data = json.loads(he.read().decode("utf-8"))
                err = err_data.get("error")
                if err == "authorization_pending":
                    pass
                else:
                    log_step(f"Polling error Kimi: {err_data.get('error_description') or err}")
                    if err in ("expired_token", "access_denied"):
                        break
            except Exception:
                pass
        except Exception as e:
            log_step(f"Polling network error Kimi: {e}")
            
        time.sleep(interval)
    return {}

def main():
    parser = argparse.ArgumentParser(description="Kimi Coding auto-login tool")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--proxy-server")
    parser.add_argument("--proxy-user")
    parser.add_argument("--proxy-pass")
    parser.add_argument("--profiles-dir", required=True)
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    # Step 1: Request Device Authorization Code
    log_step("Mengambil device code dari Kimi...")
    try:
        device_data = request_device_code()
    except Exception as e:
        sys.stdout.write(json.dumps({"status": "error", "message": str(e)}) + "\n")
        sys.exit(1)

    device_code = device_data.get("device_code")
    user_code = device_data.get("user_code")
    verification_uri = device_data.get("verification_uri") or "https://www.kimi.com/code/authorize_device"
    complete_url = f"{verification_uri}?user_code={user_code}"

    log_step(f"Device code: {user_code}. Menghubungkan URL verifikasi...")

    # Step 2: Set up profile dir and launch Camoufox
    profiles_root = Path(args.profiles_dir)
    profiles_root.mkdir(parents=True, exist_ok=True)
    profile_dir = profiles_root / safe_email_to_dirname(args.email)
    profile_dir.mkdir(parents=True, exist_ok=True)

    proxy_dict = None
    if args.proxy_server:
        proxy_dict = {"server": args.proxy_server}
        if args.proxy_user:
            proxy_dict["username"] = args.proxy_user
        if args.proxy_pass:
            proxy_dict["password"] = args.proxy_pass

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        sys.stdout.write(json.dumps({"status": "error", "message": "Camoufox tidak terinstall di environment python."}) + "\n")
        sys.exit(1)

    kwargs = dict(
        headless=args.headless,
        persistent_context=True, no_viewport=True,
        user_data_dir=str(profile_dir),
        humanize=True,
        geoip=True,
        locale="en-US",
        os=("windows", "macos", "linux"),
        window=(1280, 800),
        firefox_user_prefs={
            "network.trr.mode": 5,
        }
    )
    if proxy_dict:
        kwargs["proxy"] = {k: v for k, v in proxy_dict.items() if v}

    log_step("Meluncurkan browser...")
    try:
        with Camoufox(**kwargs) as browser:
            context = getattr(browser, "context", None) or browser
            page = context.new_page()

            log_step("Membuka halaman verifikasi Kimi...")
            page.goto(complete_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.0)

            # Wait for either the login button or the authorize button to appear
            log_step("Menunggu halaman verifikasi termuat...")
            try:
                page.wait_for_selector(".google-login-btn, button:has-text('Confirm'), button:has-text('Authorize'), button:has-text('Allow'), button:has-text('确认授权'), button:has-text('确认'), button:has-text('授权'), button.kimi-button", timeout=15000)
            except Exception:
                pass

            google_btn = page.locator(".google-login-btn").first
            is_login_required = False
            if google_btn.count() > 0 and google_btn.is_visible(timeout=500):
                is_login_required = True

            if is_login_required:
                log_step("Menemukan tombol login Google. Mengklik tombol...")
                
                with page.expect_popup() as popup_info:
                    google_btn.click(timeout=5000)
                popup = popup_info.value
                popup.wait_for_load_state()
                time.sleep(2.0)

                log_step("Menangani Google OAuth popup...")
                google_deadline = time.time() + 60.0
                filled_email = False
                filled_pass = False
                clicked_account = False
                
                while time.time() < google_deadline:
                    if popup.is_closed():
                        log_step("Popup Google ditutup.")
                        break
                    try:
                        popup_url = popup.url or ""
                        popup_title = popup.title() or ""
                        log_step(f"Popup URL: {popup_url}, Title: {popup_title}")

                        # Check if Google account is disabled / speedbumped
                        if "speedbump/disabled" in popup_url or "disabled" in popup_url or "disabled" in popup_title.lower():
                            raise KimiAutomationError(f"Akun Google ({args.email}) dinonaktifkan/disabled oleh Google.")

                        # Account chooser
                        if not clicked_account and (
                            "accountchooser" in popup_url or
                            "Choose an account" in (popup.title() or "")
                        ):
                            clicked = popup.evaluate(f"""
                                () => {{
                                    const email = '{args.email}';
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
                                        if (v === args.email) {{ el.click(); return true; }}
                                    }}
                                    const rows = document.querySelectorAll('ul li');
                                    if (rows.length > 0) {{ rows[0].click(); return true; }}
                                    return false;
                                }}
                            """)
                            if clicked:
                                log_step(f"Memilih akun '{args.email}' di Google chooser...")
                                clicked_account = True
                                time.sleep(3.0)
                                continue

                        # Email input
                        if not filled_email:
                            email_in = popup.locator("input[type='email'], input[name='identifier']").first
                            if email_in.count() > 0 and email_in.is_visible(timeout=500):
                                log_step("Mengisi email GSuite di popup Google...")
                                email_in.fill(args.email)
                                popup.keyboard.press("Enter")
                                filled_email = True
                                time.sleep(3.0)
                                continue

                        # Password input
                        if filled_email and not filled_pass:
                            pass_in = popup.locator("input[type='password'], input[name='Passwd']").first
                            if pass_in.count() > 0 and pass_in.is_visible(timeout=500):
                                log_step("Mengisi password GSuite di popup Google...")
                                pass_in.fill(args.password)
                                popup.keyboard.press("Enter")
                                filled_pass = True
                                time.sleep(3.0)
                                continue

                        # Workspace terms confirmation
                        understand_btn = popup.locator(
                            "button:has-text('I understand'), "
                            "button:has-text('I Understand')"
                        ).first
                        if understand_btn.count() > 0 and understand_btn.is_visible(timeout=500):
                            log_step("Klik 'I understand' di Google terms...")
                            understand_btn.click(timeout=3000)
                            time.sleep(2.0)
                            continue

                        # General continue button in Google Popup
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

                    except KimiAutomationError:
                        raise
                    except Exception as e_popup:
                        logger.debug(f"Google popup handler error: {e_popup}")
                    time.sleep(1.0)

                # Google popup final state screenshot
                try:
                    popup_screenshot = str(profile_dir / "google_popup_final.png")
                    popup.screenshot(path=popup_screenshot)
                    log_step(f"Google popup final screenshot saved: {popup_screenshot}")
                except Exception:
                    pass

            # Wait for main page to reload/render after login
            time.sleep(5.0)

            # Accept terms check box if present on Kimi
            try:
                page.evaluate("""() => {
                    const checkboxes = document.querySelectorAll("input[type='checkbox']");
                    for (const cb of checkboxes) {
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                        cb.dispatchEvent(new Event('click', { bubbles: true }));
                    }
                }""")
            except Exception:
                pass

            # Click Authorize/Confirm button on Kimi page
            log_step("Mengklik tombol konfirmasi/otorisasi Kimi...")
            auth_selectors = [
                "button:has-text('Confirm')",
                "button:has-text('Authorize')",
                "button:has-text('Allow')",
                "button:has-text('确认授权')",
                "button:has-text('确认')",
                "button:has-text('授权')",
                "button.kimi-button"
            ]
            clicked_auth = click_first(page, auth_selectors, timeout_ms=10000)
            if clicked_auth:
                log_step("Tombol otorisasi diklik, menunggu proses...")
            else:
                log_step("Tombol otorisasi tidak ditemukan, kemungkinan sudah terotorisasi otomatis.")
            
            # Kimi main page final state screenshot
            try:
                screenshot_path = str(profile_dir / "final_state.png")
                page.screenshot(path=screenshot_path)
                log_step(f"Screenshot disimpan ke: {screenshot_path}")
            except Exception:
                pass

            time.sleep(5.0)

    except Exception as e:
        try:
            err_screenshot = str(profile_dir / "error_state.png")
            page.screenshot(path=err_screenshot)
            log_step(f"Error screenshot disimpan ke: {err_screenshot}")
        except Exception:
            pass
        sys.stdout.write(json.dumps({"status": "error", "message": f"Browser Error: {e}"}) + "\n")
        sys.exit(1)

    # Step 3: Poll for token
    log_step("Menunggu Kimi menerbitkan token...")
    token_data = poll_token(device_code, timeout=60, interval=5)
    
    if not token_data or "access_token" not in token_data:
        sys.stdout.write(json.dumps({"status": "error", "message": "Timeout menunggu otorisasi token Kimi."}) + "\n")
        sys.exit(1)

    # Successful output
    sys.stdout.write(json.dumps({
        "status": "success",
        "api_key": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_in": token_data.get("expires_in") or 86400
    }) + "\n")
    sys.stdout.flush()

if __name__ == "__main__":
    main()
