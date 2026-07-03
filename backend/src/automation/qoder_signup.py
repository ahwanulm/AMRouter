#!/usr/bin/env python3
"""Standalone script to automate Qoder login via Google OAuth and retrieve device token.
Outputs step logs and final results to stdout as JSON lines.
"""

import sys
import json
import argparse
import time
import logging
import re
import uuid
import hashlib
import base64
import urllib.request
import urllib.parse
import os
from pathlib import Path

# Setup simple stdout logger to not conflict with JSON line prints
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("qoder_signup")

class QoderAutomationError(RuntimeError):
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

def base64Url(buf: bytes) -> str:
    return base64.urlsafe_b64encode(buf).decode("utf-8").replace("=", "")

def poll_device_token(nonce: str, verifier: str) -> dict:
    url = f"https://openapi.qoder.sh/api/v1/deviceToken/poll?nonce={urllib.parse.quote(nonce)}&verifier={urllib.parse.quote(verifier)}&challenge_method=S256"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Go-http-client/2.0",
    }
    
    # Poll up to 60 seconds
    deadline = time.time() + 60.0
    while time.time() < deadline:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                if status == 200:
                    res_data = json.loads(resp.read().decode("utf-8"))
                    if "token" in res_data:
                        return res_data
        except urllib.error.HTTPError as he:
            # 202/404 means authorization pending
            if he.code in (202, 404):
                pass
            else:
                log_step(f"Polling HTTP Error: {he.code}")
        except Exception as e:
            log_step(f"Polling Network Error: {e}")
            
        time.sleep(2.0)
    return {}

def fetch_user_info(access_token: str) -> dict:
    url = "https://openapi.qoder.sh/api/v1/userinfo"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "Go-http-client/2.0",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return {}

def main():
    parser = argparse.ArgumentParser(description="Qoder Google auto-login tool")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--proxy-server")
    parser.add_argument("--proxy-user")
    parser.add_argument("--proxy-pass")
    parser.add_argument("--profiles-dir", required=True)
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    # Step 1: Initiate local device flow PKCE/nonce parameters
    verifier = base64Url(os.urandom(32))
    challenge = base64Url(hashlib.sha256(verifier.encode("utf-8")).digest())
    nonce = str(uuid.uuid4())
    machine_id = str(uuid.uuid4())
    
    client_id = "e883ade2-e6e3-4d6d-adf7-f92ceff5fdcb"
    url = f"https://qoder.com/device/selectAccounts?challenge={challenge}&challenge_method=S256&machine_id={machine_id}&nonce={nonce}&client_id={client_id}"
    log_step(f"Meluncurkan flow otorisasi Qoder...")

    # Step 2: Set up profile dir
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
    token_data = {}
    try:
        with Camoufox(**kwargs) as browser:
            context = getattr(browser, "context", None) or browser
            page = context.new_page()

            log_step("Membuka halaman verifikasi Qoder...")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Wait for either sign-in or authorization page to render
            login_needed = False
            for _ in range(30):
                cur_url = page.url.lower()
                if "sign-in" in cur_url or page.locator("a:has-text('Sign in with Google')").count() > 0:
                    login_needed = True
                    break
                # Authorize / Confirm page or success already loaded
                if "success" in cur_url or page.locator("button:has-text('Confirm'), button:has-text('Authorize'), button:has-text('Allow')").count() > 0 or "success" in (page.title() or "").lower():
                    break
                time.sleep(0.5)

            if login_needed:
                google_sel = "a:has-text('Sign in with Google'), a[href*='/sso/login/google']"
                try:
                    page.wait_for_selector(google_sel, timeout=10000)
                    google_link = page.locator(google_sel).first
                    log_step("Mengklik tombol Google Sign-in...")
                    google_link.click(force=True)
                    time.sleep(5.0)
                except Exception as e:
                    log_step(f"Tombol Google Sign-in tidak ditemukan: {e}")

                # Handle Google login pages
                google_deadline = time.time() + 60.0
                filled_email = False
                filled_pass = False
                clicked_account = False
                
                GOOGLE_EMAIL_SELECTORS = ["input[type='email']", "input[name='identifier']", "input#identifierId"]
                GOOGLE_PASSWORD_SELECTORS = ["input[type='password'][name='Passwd']", "input[type='password']", "input[name='password']"]

                while time.time() < google_deadline:
                    cur_url = page.url.lower()
                    if "google.com" not in cur_url and "googleusercontent.com" not in cur_url:
                        break

                    # Disabled speedbump check
                    if "speedbump/disabled" in cur_url or "disabled" in cur_url or "disabled" in (page.title() or "").lower():
                        raise QoderAutomationError(f"Akun Google ({args.email}) dinonaktifkan oleh Google.")

                    # 1. Chooser page / Select account
                    if not clicked_account and ("accountchooser" in cur_url or "Choose an account" in (page.title() or "")):
                        clicked = page.evaluate(f"""
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
                                    if (v === email) {{ el.click(); return true; }}
                                }}
                                return false;
                            }}
                        """)
                        if clicked:
                            log_step("JS klik akun di chooser...")
                            clicked_account = True
                            time.sleep(3.0)
                            continue

                    # 2. Email input
                    if not filled_email:
                        email_in = page.locator(",".join(GOOGLE_EMAIL_SELECTORS)).first
                        if email_in.count() > 0 and email_in.is_visible(timeout=500):
                            log_step("Mengisi email Google...")
                            email_in.fill(args.email)
                            page.keyboard.press("Enter")
                            filled_email = True
                            time.sleep(3.0)
                            continue

                    # 3. Password input
                    if filled_email and not filled_pass:
                        pass_in = page.locator(",".join(GOOGLE_PASSWORD_SELECTORS)).first
                        if pass_in.count() > 0 and pass_in.is_visible(timeout=500):
                            log_step("Mengisi password Google...")
                            pass_in.fill(args.password)
                            page.keyboard.press("Enter")
                            filled_pass = True
                            time.sleep(5.0)
                            continue

                    # 4. Workspace terms agreement
                    understand_btn = page.locator("button:has-text('I understand'), button:has-text('I Understand')").first
                    if understand_btn.count() > 0 and understand_btn.is_visible(timeout=500):
                        log_step("Klik 'I understand' di terms...")
                        understand_btn.click(timeout=3000)
                        time.sleep(2.0)
                        continue

                    # 5. Consent Continue/Allow buttons
                    clicked_btn = False
                    for btn_text in ["Continue", "Allow", "Next", "Accept", "Confirm"]:
                        try:
                            btn = page.locator(f"button:has-text('{btn_text}')").first
                            if btn.count() > 0 and btn.is_visible(timeout=300):
                                log_step(f"Klik tombol Google: {btn_text}")
                                btn.click(timeout=1000, force=True)
                                clicked_btn = True
                                time.sleep(3.0)
                                break
                        except Exception:
                            pass
                    if clicked_btn:
                        continue

                    time.sleep(1.0)

            # Back on Qoder - Wait for authorization to resolve
            time.sleep(5.0)
            
            # Check if there is an authorize button to confirm
            confirm_sel = "button:has-text('Confirm'), button:has-text('Authorize'), button:has-text('Allow'), button:has-text('Sign in'), button:has-text('Continue')"
            try:
                confirm_btn = page.locator(confirm_sel).first
                if confirm_btn.count() > 0 and confirm_btn.is_visible(timeout=1000):
                    log_step("Mengklik tombol konfirmasi/otorisasi...")
                    confirm_btn.click(force=True)
                    time.sleep(5.0)
            except Exception:
                pass

            # Step 3: Poll for the device token
            log_step("Menunggu Qoder menerbitkan token...")
            token_data = poll_device_token(nonce, verifier)
            if not token_data or "token" not in token_data:
                # Take screenshot on warning/error
                try:
                    screenshot_path = str(profile_dir / "qoder_not_success.png")
                    page.screenshot(path=screenshot_path)
                    log_step(f"Screenshot disimpan ke {screenshot_path}")
                    html_path = str(profile_dir / "qoder_not_success.html")
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(page.content())
                    log_step(f"HTML disimpan ke {html_path}")
                except Exception as ex:
                    log_step(f"Gagal mengambil screenshot/HTML: {ex}")
                raise QoderAutomationError("Timeout menunggu device token Qoder.")

    except Exception as e:
        sys.stdout.write(json.dumps({"status": "error", "message": f"Browser Error: {e}"}) + "\n")
        sys.exit(1)

    # Step 4: Fetch Profile User Info best effort
    user_info = fetch_user_info(token_data["token"])

    # Output Success JSON line
    sys.stdout.write(json.dumps({
        "status": "success",
        "api_key": token_data["token"],
        "refresh_token": token_data.get("refresh_token") or "",
        "expires_in": token_data.get("expires_in") or 2592000,
        "user_id": token_data.get("user_id") or user_info.get("user_id") or "",
        "machine_id": machine_id,
        "name": user_info.get("name") or user_info.get("username") or "",
        "email": user_info.get("email") or args.email,
        "organization_id": user_info.get("organization_id") or ""
    }) + "\n")
    sys.stdout.flush()

if __name__ == "__main__":
    main()
