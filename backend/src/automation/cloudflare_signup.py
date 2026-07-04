#!/usr/bin/env python3
"""Cloudflare account auto-signup via Camoufox (anti-fingerprint) + Ammail email verification.

Outputs JSON lines to stdout:
  {"step": "..."} — progress update
  {"status": "success", "api_key": "...", "account_id": "...", "email": "..."} — final result
  {"status": "error", "error": "..."} — failure
"""

import sys
import json
import argparse
import time
import random
import string
import re
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# ── Stdout JSON helpers ────────────────────────────────────────────────────────
def emit(obj):
    print(json.dumps(obj), flush=True)

def log_step(msg):
    emit({"step": msg})

def success(api_key, account_id, email):
    # Clean api_key — extract Bearer token if it's a curl command
    import re as _re_clean
    bearer_match = _re_clean.search(r'Bearer\s+([A-Za-z0-9_\-]{20,})', api_key)
    if bearer_match:
        api_key = bearer_match.group(1)
    # Also match cfut_ token pattern directly
    cfut_match = _re_clean.search(r'\b(cfut_[A-Za-z0-9_\-]{30,})\b', api_key)
    if cfut_match:
        api_key = cfut_match.group(1)
    emit({"status": "success", "api_key": api_key, "account_id": account_id, "email": email})

def die(msg):
    emit({"status": "error", "error": msg})
    sys.exit(1)

# ── Ammail helpers ─────────────────────────────────────────────────────────────
def ammail_request(base_url, api_key, path, method="GET", data=None, host_header=None):
    url = base_url.rstrip("/") + "/api" + path
    req = urllib.request.Request(url, method=method)
    req.add_header("X-API-Key", api_key)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    req.add_header("Accept", "application/json, */*")
    # Nginx vhost routing: tambah Host header jika base_url adalah localhost
    if host_header:
        req.add_header("Host", host_header)
    elif "localhost" in base_url or "127.0.0.1" in base_url:
        req.add_header("Host", "ammail.klipers.site")
    if data:
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def create_ammail_inbox(base_url, api_key, email):
    """Create inbox by splitting email into alias + domain."""
    try:
        alias, domain = email.split("@", 1)
        ammail_request(base_url, api_key, "/inboxes", method="POST",
                       data={"alias": alias, "domain": domain})
    except Exception:
        pass  # might already exist

def wait_for_cf_verify_email(base_url, api_key, email, timeout=120):
    log_step(f"Menunggu email verifikasi Cloudflare ({email})...")
    alias = email.split("@")[0]
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        try:
            data = ammail_request(base_url, api_key, f"/inboxes/{urllib.parse.quote(alias)}/messages")
            messages = data.get("messages", [])
            for msg in messages:
                msg_id = msg.get("id", "")
                subject = msg.get("subject", "")
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)
                if "cloudflare" in subject.lower() or "verify" in subject.lower() or "confirm" in subject.lower():
                    # Fetch full message body
                    try:
                        full = ammail_request(base_url, api_key, f"/messages/{urllib.parse.quote(msg_id)}")
                        msg_body = full.get("message", full)
                        body = msg_body.get("body", msg_body.get("html", msg_body.get("text", "")))
                    except Exception:
                        body = msg.get("snippet", "")
                    patterns = [
                        r'https://dash\.cloudflare\.com/email-verification[^\s\'"<>]+',
                        r'https://[^\s\'"<>]*confirm[^\s\'"<>]*',
                        r'https://[^\s\'"<>]*verify[^\s\'"<>]*',
                        r'https://dash\.cloudflare\.com/[^\s\'"<>]+',
                    ]
                    for pat in patterns:
                        links = re.findall(pat, body)
                        if links:
                            link = links[0].rstrip(".")
                            log_step(f"Link verifikasi ditemukan!")
                            return link
        except Exception as e:
            log_step(f"Ammail poll error: {e}")
        time.sleep(5)
    return None

# ── 2Captcha Turnstile solver ───────────────────────────────────────────────────
CF_SIGNUP_TURNSTILE_SITEKEY = "0x4AAAAAAAJel0iaAR3mgkjp"
CF_SIGNUP_PAGE_URL = "https://dash.cloudflare.com/sign-up"

def solve_turnstile_2captcha(api_key, page_url, sitekey, timeout=120):
    """Submit Turnstile to 2Captcha and wait for solution token."""
    log_step("Mengirim Turnstile ke 2Captcha untuk diselesaikan...")
    try:
        # Submit task
        submit_data = {
            "key": api_key,
            "method": "turnstile",
            "sitekey": sitekey,
            "pageurl": page_url,
            "json": 1,
        }
        encoded = urllib.parse.urlencode(submit_data).encode()
        req = urllib.request.Request("https://2captcha.com/in.php", data=encoded)
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
        if not resp.get("status") == 1:
            log_step(f"2Captcha submit error: {resp}")
            return None
        task_id = resp.get("request")
        log_step(f"2Captcha task submitted: {task_id}")

        # Poll for result
        deadline = time.time() + timeout
        time.sleep(15)  # initial wait
        while time.time() < deadline:
            res_url = f"https://2captcha.com/res.php?key={api_key}&action=get&id={task_id}&json=1"
            req2 = urllib.request.Request(res_url)
            with urllib.request.urlopen(req2, timeout=15) as r2:
                res = json.loads(r2.read())
            if res.get("status") == 1:
                token = res.get("request")
                log_step(f"2Captcha Turnstile solved!")
                return token
            if res.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
                log_step("2Captcha: captcha unsolvable")
                return None
            time.sleep(5)
        log_step("2Captcha Turnstile timeout")
        return None
    except Exception as e:
        log_step(f"2Captcha error: {e}")
        return None

def inject_turnstile_token(page, token):
    """Inject solved Turnstile token into the page."""
    try:
        page.evaluate(f"""
        (function() {{
            // Set cf-turnstile-response hidden input
            var inputs = document.querySelectorAll('input[name="cf-turnstile-response"], input[name="cf_challenge_response"]');
            inputs.forEach(function(el) {{ el.value = '{token}'; }});
            // Also try window.turnstile callback
            if (window.turnstile && window.turnstile.getResponse) {{
                try {{ window.turnstile.execute(); }} catch(e) {{}}
            }}
        }})();
        """)
        return True
    except Exception as e:
        log_step(f"inject_turnstile_token error: {e}")
        return False

# ── Turnstile bypass (ported from weavy_signup.py) ─────────────────────────────
def is_on_turnstile_page(page) -> bool:
    try:
        title = page.title() or ""
        if "just a moment" in title.lower() or "security verification" in title.lower():
            return True
    except Exception:
        pass
    try:
        token = page.evaluate("() => { const el = document.getElementsByName('cf-turnstile-response')[0] || document.getElementById('cf-turnstile-response'); return el ? el.value : null; }")
        if token is not None:
            return len(token.strip()) == 0
    except Exception:
        pass
    for sel in ["text=Just a moment", "text=Verifying you are human", "#challenge-form", "#cf-challenge-running"]:
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
        try:
            frame_element = page.locator("iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']").first
            if frame_element.count() > 0 and not frame_element.is_visible(timeout=500):
                return False
        except Exception:
            pass
        for cb_sel in ["input[type='checkbox']", "[role='checkbox']", "div.ctp-checkbox-label"]:
            try:
                box = target_frame.locator(cb_sel).first
                if box.count() > 0:
                    box.click(timeout=3000)
                    return True
            except Exception:
                continue
        try:
            handle = target_frame.frame_element()
            bbox = handle.bounding_box() if handle else None
            if bbox:
                x = bbox["x"] + 28
                y = bbox["y"] + 32
                page.mouse.move(x, y, steps=10)
                time.sleep(0.3)
                page.mouse.click(x, y)
                return True
        except Exception:
            pass
    for iframe_sel in ["iframe[src*='challenges.cloudflare.com']", "iframe[src*='turnstile']"]:
        for cb_sel in ["input[type='checkbox']", "[role='checkbox']"]:
            try:
                box = page.frame_locator(iframe_sel).locator(cb_sel).first
                if box.count() > 0:
                    box.click(timeout=3000)
                    return True
            except Exception:
                continue
    return False

def wait_for_cf_clearance(page, timeout=45.0):
    if not is_on_turnstile_page(page):
        return True
    log_step("Cloudflare Turnstile terdeteksi, menunggu resolve...")
    deadline = time.time() + timeout
    click_attempts = 0
    next_click_at = time.time() + 4.0
    while time.time() < deadline:
        time.sleep(2.0)
        if not is_on_turnstile_page(page):
            log_step("Turnstile selesai!")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            return True
        now = time.time()
        if click_attempts < 5 and now >= next_click_at:
            click_attempts += 1
            log_step(f"Klik Turnstile checkbox (attempt {click_attempts}/5)...")
            try_click_turnstile_checkbox(page)
            next_click_at = now + 8.0
            time.sleep(2.0)
    return False

# ── Cloudflare API ─────────────────────────────────────────────────────────────
CF_API = "https://api.cloudflare.com/client/v4"

def cf_api_call(path, global_key, email, method="GET", body=None):
    url = CF_API + path
    req = urllib.request.Request(url, method=method)
    req.add_header("X-Auth-Key", global_key)
    req.add_header("X-Auth-Email", email)
    req.add_header("Content-Type", "application/json")
    if body:
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"CF API {path} {e.code}: {e.read().decode()}")

def get_account_id_via_api(global_key, email):
    try:
        r = cf_api_call("/accounts?per_page=1", global_key, email)
        if r.get("success") and r.get("result"):
            return r["result"][0]["id"], r["result"][0]["name"]
    except Exception as e:
        log_step(f"get_account_id error: {e}")
    return None, None

def create_workers_ai_token(global_key, email, account_id, token_name="9router Workers AI"):
    """Create Workers AI Read+Edit token via CF API using Global API Key."""
    try:
        # Get permission groups
        r = cf_api_call(f"/accounts/{account_id}/tokens/permission_groups", global_key, email)
        groups = r.get("result", [])
        read_g = next((g for g in groups if "workers ai" in g["name"].lower() and "read" in g["name"].lower()), None)
        edit_g = next((g for g in groups if "workers ai" in g["name"].lower() and "edit" in g["name"].lower()), None)
        if not read_g or not edit_g:
            # fallback: first two that match workers ai
            wa = [g for g in groups if "workers ai" in g["name"].lower()]
            if len(wa) >= 2:
                read_g, edit_g = wa[0], wa[1]
            elif len(wa) == 1:
                read_g = edit_g = wa[0]
            else:
                return None
        payload = {
            "name": token_name,
            "policies": [{
                "effect": "allow",
                "permission_groups": [{"id": read_g["id"]}, {"id": edit_g["id"]}],
                "resources": {f"com.cloudflare.api.account.{account_id}": "*"},
            }],
        }
        r2 = cf_api_call("/user/tokens", global_key, email, method="POST", body=payload)
        if r2.get("success") and r2.get("result", {}).get("value"):
            return r2["result"]["value"]
    except Exception as e:
        log_step(f"create_workers_ai_token error: {e}")
    return None

# ── Handle "Verify Your Identity" popup ────────────────────────────────────────
def handle_identity_verification(page, ammail_base_url, ammail_api_key, email):
    """Detect CF identity verification popup, send OTP, fetch from Ammail, submit."""
    try:
        # Use multiple selectors to detect the popup
        popup_visible = False
        for sel in [
            "h2:has-text('Verify Your Identity')",
            "h1:has-text('Verify Your Identity')",
            "div:has-text('Verify Your Identity')",
            "button:has-text('Send Verification Code')",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    popup_visible = True
                    break
            except Exception:
                continue

        if not popup_visible:
            return True  # No popup, all good

        log_step("Popup 'Verify Your Identity' terdeteksi!")

        # Click "Send Verification Code"
        send_btn = page.locator("button:has-text('Send Verification Code')").first
        if send_btn.is_visible(timeout=2000):
            send_btn.click()
            log_step("Klik Send Verification Code...")
            time.sleep(3)
        else:
            # Try clicking Cancel and skip
            cancel = page.locator("button:has-text('Cancel')").first
            if cancel.is_visible(timeout=1000):
                cancel.click()
            return False

        # Fetch OTP from Ammail
        if not ammail_base_url or not ammail_api_key:
            log_step("Ammail tidak dikonfigurasi, tidak bisa ambil OTP")
            return False

        log_step("Menunggu OTP di Ammail...")
        otp_code = None
        for attempt in range(20):  # 60 seconds
            time.sleep(3)
            try:
                msgs = ammail_request(ammail_base_url, ammail_api_key, f"/inboxes/{email.split('@')[0]}/messages")
                for msg in msgs.get("messages", []):
                    # Get full body
                    msg_detail = ammail_request(ammail_base_url, ammail_api_key, f"/messages/{msg['id']}")
                    body = msg_detail.get("body", "") or msg_detail.get("html", "") or msg.get("snippet", "")
                    # CF OTP is typically 6 digits
                    import re as _re
                    otp_match = _re.search(r'\b(\d{6})\b', body)
                    if otp_match:
                        otp_code = otp_match.group(1)
                        log_step(f"OTP ditemukan: {otp_code}")
                        break
            except Exception as e:
                log_step(f"Ammail OTP fetch error: {e}")
            if otp_code:
                break

        if not otp_code:
            log_step("OTP tidak diterima dalam 60 detik")
            return False

        # Enter OTP
        otp_input = page.locator("input[type='text'][maxlength='6'], input[placeholder*='code'], input[name*='code'], input[type='number']").first
        if otp_input.is_visible(timeout=5000):
            otp_input.fill(otp_code)
            time.sleep(0.5)
            log_step("OTP diisi!")

            # Submit
            for sel in ["button:has-text('Verify')", "button:has-text('Submit')", "button:has-text('Confirm')", "button[type='submit']"]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=1000):
                        btn.click()
                        time.sleep(2)
                        log_step("OTP submitted!")
                        return True
                except Exception:
                    continue
        else:
            log_step("OTP input field tidak ditemukan")
    except Exception as e:
        log_step(f"handle_identity_verification error: {e}")
    return False

# ── Extract Global API Key from dashboard page ─────────────────────────────────
def extract_global_api_key(page, password, ammail_base_url="", ammail_api_key="", email=""):
    """Navigate to API tokens page and extract Global API Key."""
    log_step("Membuka halaman API Tokens...")
    try:
        page.goto("https://dash.cloudflare.com/profile/api-tokens", wait_until="domcontentloaded", timeout=30000)
        wait_for_cf_clearance(page, timeout=20)
        time.sleep(3)

        # ── Handle "Verify Your Identity" popup ─────────────────────────────
        handle_identity_verification(page, ammail_base_url, ammail_api_key, email)
        time.sleep(1)

        # Find "View" button for Global API Key
        view_selectors = [
            "button:has-text('View')",
            "button:has-text('Reveal')",
            "span:has-text('View'):visible",
        ]
        for sel in view_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    time.sleep(1)
                    break
            except Exception:
                continue

        # Password confirmation modal
        pw_input = page.locator("input[type='password']").first
        if pw_input.is_visible(timeout=3000):
            log_step("Mengisi password konfirmasi...")
            pw_input.fill(password)
            time.sleep(0.5)
            # Click confirm button
            for sel in ["button:has-text('View')", "button[type='submit']", "button:has-text('Confirm')"]:
                try:
                    btn = page.locator(sel).last
                    if btn.is_visible(timeout=1000):
                        btn.click()
                        break
                except Exception:
                    continue
            time.sleep(2)

        # Extract the key value
        for sel in [
            "input[data-testid='global-api-key']",
            "input[readonly][type='text']",
            "code",
            ".cf-input-code",
            "input[class*='code']",
            "input[class*='api']",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    val = el.input_value() if sel.startswith("input") else el.text_content()
                    if val and len(val) > 20:
                        return val.strip()
            except Exception:
                continue

        # Take screenshot to debug
        try:
            page.screenshot(path="/tmp/cf_api_key_page.png")
            log_step("Screenshot saved: /tmp/cf_api_key_page.png")
        except Exception:
            pass

    except Exception as e:
        log_step(f"extract_global_api_key error: {e}")
    return None

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--ammail-base-url", default="")
    parser.add_argument("--ammail-api-key", default="")
    parser.add_argument("--ammail-domain", default="")
    parser.add_argument("--profiles-dir", default="profiles/cloudflare")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--proxy-server")
    parser.add_argument("--proxy-user")
    parser.add_argument("--proxy-pass")
    parser.add_argument("--2captcha-key", default="", dest="captcha_key")
    # ── Manual override: skip automation, paste token directly ────────────────
    parser.add_argument("--token", default="",
                        help="Paste CF API token manual — skip seluruh automation")
    parser.add_argument("--account-id", default="", dest="account_id_arg",
                        help="Cloudflare Account ID (wajib jika pakai --token)")
    parser.add_argument("--stagger-delay", type=int, default=0, dest="stagger_delay",
                        help="Delay (detik) sebelum launch browser, untuk stagger concurrent instances")
    args = parser.parse_args()

    # ── Shortcut: jika user paste token manual, langsung simpan ──────────────
    if args.token:
        if not args.account_id_arg:
            die("--token butuh --account-id juga")
        log_step(f"Mode manual token: {args.token[:12]}...")
        success(args.token.strip(), args.account_id_arg.strip(), args.email)
        return

    # Import Camoufox (same as weavy_signup.py)
    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        die("Camoufox tidak terinstall. Jalankan: pip install camoufox && python -m camoufox fetch")

    profiles_dir = Path(args.profiles_dir)
    profiles_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create Ammail inbox if we have credentials
    ammail_ok = bool(args.ammail_base_url and args.ammail_api_key and args.ammail_domain)
    if ammail_ok:
        log_step(f"Membuat inbox Ammail untuk {args.email}...")
        try:
            create_ammail_inbox(args.ammail_base_url, args.ammail_api_key, args.email)
        except Exception as e:
            log_step(f"Ammail inbox warning: {e}")

    log_step("Meluncurkan browser Camoufox (anti-fingerprint)...")

    # Stagger delay — when running concurrent instances, delay launch to avoid
    # resource contention and Cloudflare rate-limit detection
    if args.stagger_delay > 0:
        log_step(f"Stagger delay {args.stagger_delay}s...")
        time.sleep(args.stagger_delay)

    proxy_dict = None
    if args.proxy_server:
        proxy_dict = {"server": args.proxy_server}
        if args.proxy_user:
            proxy_dict["username"] = args.proxy_user
        if args.proxy_pass:
            proxy_dict["password"] = args.proxy_pass

    launch_kwargs = dict(
        headless=args.headless,
        os="windows",
        locale="en-US",
    )
    if proxy_dict:
        launch_kwargs["proxy"] = proxy_dict

    try:
        browser_ctx = Camoufox(**launch_kwargs)
    except TypeError:
        launch_kwargs.pop("os", None)
        try:
            browser_ctx = Camoufox(**launch_kwargs)
        except TypeError:
            launch_kwargs.pop("locale", None)
            browser_ctx = Camoufox(**launch_kwargs)

    with browser_ctx as browser:
        page = browser.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})

        # ── Step 1: Open Cloudflare signup ────────────────────────────────────
        log_step("Membuka halaman registrasi Cloudflare...")
        try:
            page.goto("https://dash.cloudflare.com/sign-up", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            page.goto("https://dash.cloudflare.com/sign-up", wait_until="load", timeout=30000)

        wait_for_cf_clearance(page, timeout=30)
        time.sleep(random.uniform(1.5, 2.5))

        # ── Step 2: Fill email ────────────────────────────────────────────────
        log_step("Menunggu form signup muncul...")
        form_found = False
        for attempt in range(3):
            try:
                page.wait_for_selector("input[name='email'], input[autocomplete='email']", timeout=20000)
                form_found = True
                break
            except Exception:
                log_step(f"Form belum muncul (attempt {attempt+1}), reload...")
                try:
                    page.reload(wait_until="load", timeout=20000)
                    wait_for_cf_clearance(page, timeout=15)
                    time.sleep(3)
                except Exception:
                    pass
        if not form_found:
            die("Form signup tidak muncul setelah 3 percobaan")

        log_step("Mengisi email...")
        email_sel = [
            "input[name='email']",
            "input[autocomplete='email']",
            "input[type='email']",
        ]
        email_filled = False
        for sel in email_sel:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    time.sleep(0.3)
                    el.fill(args.email)
                    email_filled = True
                    break
            except Exception:
                continue
        if not email_filled:
            die("Tidak bisa menemukan input email di halaman signup Cloudflare")

        # ── Step 3: Fill password ─────────────────────────────────────────────
        log_step("Mengisi password...")
        pw_inputs = page.locator("input[name='password'], input[type='password']")
        pw_count = pw_inputs.count()
        if pw_count >= 1:
            pw_inputs.nth(0).fill(args.password)
            time.sleep(0.3)
        if pw_count >= 2:
            pw_inputs.nth(1).fill(args.password)
            time.sleep(0.3)

        # ── Step 4: Handle Turnstile ──────────────────────────────────────────
        log_step("Menangani Turnstile captcha...")
        time.sleep(3)

        # First try auto-solve (works sometimes in non-headless)
        turnstile_solved = False
        wait_for_cf_clearance(page, timeout=10)

        # Check if already solved
        try:
            token_val = page.evaluate("() => { const el = document.getElementsByName('cf_challenge_response')[0]; return el ? el.value : ''; }")
            if token_val and len(token_val.strip()) > 10:
                turnstile_solved = True
                log_step("Turnstile auto-solved!")
        except Exception:
            pass

        # Fallback: 2Captcha
        if not turnstile_solved and args.captcha_key:
            log_step("Turnstile belum solved, pakai 2Captcha...")
            token_2c = solve_turnstile_2captcha(
                args.captcha_key,
                CF_SIGNUP_PAGE_URL,
                CF_SIGNUP_TURNSTILE_SITEKEY,
                timeout=120,
            )
            if token_2c:
                inject_turnstile_token(page, token_2c)
                turnstile_solved = True
                time.sleep(1)
            else:
                log_step("2Captcha gagal, tetap coba submit...")
        elif not turnstile_solved:
            log_step("Tidak ada 2Captcha key, lanjut submit tanpa solve...")

        # ── Step 5: Submit form ───────────────────────────────────────────────
        log_step("Submit form registrasi...")
        submit_selectors = [
            "button[type='submit']",
            "button:has-text('Create Account')",
            "button:has-text('Sign up')",
            "button:has-text('Get started')",
            "input[type='submit']",
        ]
        submitted = False
        for sel in submit_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    submitted = True
                    break
            except Exception:
                continue
        if not submitted:
            die("Tidak bisa menemukan tombol submit registrasi")

        time.sleep(3)

        # Check for errors (email already registered, etc.)
        for err_sel in ["text=already registered", "text=already exists", "text=taken", ".error-message"]:
            try:
                if page.locator(err_sel).first.is_visible(timeout=1000):
                    die(f"Email sudah terdaftar atau error: {args.email}")
            except Exception:
                pass

        # ── Step 6: Email verification ────────────────────────────────────────
        if ammail_ok:
            verify_link = wait_for_cf_verify_email(
                args.ammail_base_url,
                args.ammail_api_key,
                args.email,
                timeout=180,
            )
            if verify_link:
                log_step(f"Membuka link verifikasi...")
                try:
                    page.goto(verify_link, wait_until="domcontentloaded", timeout=30000)
                    wait_for_cf_clearance(page, timeout=20)
                    time.sleep(3)
                except Exception as e:
                    log_step(f"Warning navigasi verify link: {e}")
            else:
                log_step("Email verifikasi tidak diterima dalam 2 menit, lanjut coba login...")
        else:
            log_step("Ammail tidak dikonfigurasi — skip email verification, lanjut login manual...")
            time.sleep(5)

        # ── Step 7: Login if needed ───────────────────────────────────────────
        # After verify link, CF might already redirect to dashboard
        _early_account_id = ""
        _post_verify_url = page.url
        _m_verify = re.search(r"/([a-f0-9]{32})(?:/|$)", _post_verify_url)
        if _m_verify:
            _early_account_id = _m_verify.group(1)
            log_step(f"Sudah di dashboard setelah verify! Account ID: {_early_account_id[:8]}...")
        else:
            log_step("Login ke Cloudflare Dashboard...")
            try:
                page.goto("https://dash.cloudflare.com/login", wait_until="domcontentloaded", timeout=20000)
                time.sleep(2)

                # Check if already redirected to dashboard
                _m_redir = re.search(r"/([a-f0-9]{32})(?:/|$)", page.url)
                if _m_redir:
                    _early_account_id = _m_redir.group(1)
                    log_step(f"Redirect otomatis ke dashboard: {_early_account_id[:8]}...")
                else:
                    # Wait for login form
                    try:
                        page.wait_for_selector("input[name='email'], input[autocomplete='email']", timeout=8000)
                    except Exception:
                        log_step("Login form tidak muncul, cek URL...")
                        _m2 = re.search(r"/([a-f0-9]{32})(?:/|$)", page.url)
                        if _m2:
                            _early_account_id = _m2.group(1)

                    if not _early_account_id:
                        # Fill email
                        for sel in ["input[name='email']", "input[autocomplete='email']", "input[type='email']"]:
                            try:
                                el = page.locator(sel).first
                                if el.is_visible(timeout=1500):
                                    el.fill(args.email)
                                    break
                            except Exception:
                                continue

                        # Fill password
                        pw_el = page.locator("input[name='password'], input[type='password']").first
                        if pw_el.is_visible(timeout=3000):
                            pw_el.fill(args.password)
                        time.sleep(1)

                        # Solve Turnstile - try click approach first (same as signup)
                        log_step("Menyelesaikan Turnstile login...")
                        wait_for_cf_clearance(page, timeout=5)  # quick auto-solve check
                        try_click_turnstile_checkbox(page)  # click approach
                        time.sleep(3)

                        # Check if auto-solved, else try 2Captcha
                        login_turnstile_solved = False
                        try:
                            token_val = page.evaluate("() => { const el = document.getElementsByName('cf_challenge_response')[0]; return el ? el.value : ''; }")
                            if token_val and len(token_val.strip()) > 10:
                                login_turnstile_solved = True
                                log_step("Turnstile login auto-solved!")
                        except Exception:
                            pass

                        if not login_turnstile_solved and args.captcha_key:
                            log_step("Solve Turnstile login via 2Captcha...")
                            login_token = solve_turnstile_2captcha(
                                args.captcha_key,
                                "https://dash.cloudflare.com/login",
                                CF_SIGNUP_TURNSTILE_SITEKEY,
                                timeout=120,
                            )
                            if login_token:
                                inject_turnstile_token(page, login_token)
                                login_turnstile_solved = True
                                time.sleep(1)
                                log_step("Turnstile login injected via 2Captcha!")

                        # Submit
                        for sel in ["button[type='submit']", "button:has-text('Sign in')", "button:has-text('Log in')"]:
                            try:
                                btn = page.locator(sel).first
                                if btn.is_visible(timeout=1000):
                                    btn.click()
                                    break
                            except Exception:
                                continue

                        log_step("Menunggu redirect ke dashboard...")
                        time.sleep(8)

                        current_url = page.url
                        log_step(f"After login URL: {current_url}")
                        _m_after = re.search(r"/([a-f0-9]{32})(?:/|$)", current_url)
                        if _m_after:
                            _early_account_id = _m_after.group(1)
                            log_step(f"Account ID from login URL: {_early_account_id[:8]}...")

            except Exception as e:
                log_step(f"Login error: {e}")

        # ── Step 8: Get to dashboard and extract account ID ───────────────────
        # If we already got account_id from login URL, skip navigation
        if _early_account_id:
            log_step(f"Sudah punya Account ID dari login URL, skip re-navigate.")
        else:
            log_step("Memuat Cloudflare Dashboard...")
            try:
                page.goto("https://dash.cloudflare.com/home", wait_until="domcontentloaded", timeout=30000)
                wait_for_cf_clearance(page, timeout=20)
                time.sleep(3)
            except Exception as e:
                log_step(f"Dashboard load warning: {e}")
                try:
                    page = browser.new_page()
                    page.goto("https://dash.cloudflare.com/home", wait_until="domcontentloaded", timeout=30000)
                    wait_for_cf_clearance(page, timeout=20)
                    time.sleep(3)
                except Exception as e2:
                    log_step(f"New page also failed: {e2}")


        # Extract account_id — try multiple methods
        account_id = ""

        # Method 0: from login URL (already captured above)
        if _early_account_id:
            account_id = _early_account_id
            log_step(f"Account ID (from login): {account_id[:8]}...")

        # Method 1: from current page URL
        if not account_id:
            try:
                for _ in range(8):
                    url_match = re.search(r"/([a-f0-9]{32})(?:/|$)", page.url)
                    if url_match:
                        account_id = url_match.group(1)
                        log_step(f"Account ID from URL: {account_id[:8]}...")
                        break
                    time.sleep(1)
            except Exception as e:
                log_step(f"account_id from URL error: {e}")

        # Method 2: from JS window/React state
        if not account_id:
            try:
                acct_js = page.evaluate("""
                () => {
                    try {
                        // Try window.__INITIAL_STATE__ or similar
                        if (window.__BOOTSTRAP_DATA__) return window.__BOOTSTRAP_DATA__.account_id || '';
                        if (window.__cf_data__) return window.__cf_data__.accountId || '';
                        // Try from meta tags
                        var m = document.querySelector('meta[name="account-id"]');
                        if (m) return m.content;
                        // Try URL one more time
                        var m2 = window.location.pathname.match(/\\/([a-f0-9]{32})(?:\\/|$)/);
                        if (m2) return m2[1];
                    } catch(e) {}
                    return '';
                }
                """)
                if acct_js and len(acct_js) == 32:
                    account_id = acct_js
                    log_step(f"Account ID from JS: {account_id[:8]}...")
            except Exception as e:
                log_step(f"account_id from JS error: {e}")
        # Method 3: CF API /accounts using session cookies
        if not account_id:
            try:
                log_step("Mengambil Account ID via CF API dengan session cookie...")
                cookies = page.context.cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if "cloudflare" in c.get("domain",""))
                req = urllib.request.Request("https://api.cloudflare.com/client/v4/accounts?per_page=1")
                req.add_header("Cookie", cookie_str)
                req.add_header("User-Agent", "Mozilla/5.0 Chrome/125.0")
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read())
                    if data.get("success") and data.get("result"):
                        account_id = data["result"][0]["id"]
                        log_step(f"Account ID via API: {account_id[:8]}...")
            except Exception as e:
                log_step(f"account_id via API error: {e}")

        # ── Step 9/10: Buat Workers AI Token via Session API ─────────────────
        global_key = None
        workers_ai_token = None

        if not account_id:
            die("Tidak bisa membuat API Token: account_id tidak ditemukan")

        log_step("Membuat Workers AI API Token...")

        # ── Strategy A: Get Global API Key → create token via CF API ────────────
        def create_token_via_global_key(page):
            """Navigate to API Keys page, get Global API Key, use CF API to create token."""
            import requests as _req
            log_step("Mencoba ambil Global API Key dari dashboard...")
            try:
                # Navigate to API keys page
                page.goto("https://dash.cloudflare.com/profile/api-tokens", wait_until="domcontentloaded", timeout=20000)
                time.sleep(2)

                # Click on "Global API Key" > "View" button
                for sel in ["button:has-text('View')", "a:has-text('View')"]:
                    try:
                        b = page.locator(sel).first
                        if b.count() > 0 and b.is_visible(timeout=3000):
                            b.click()
                            time.sleep(2)
                            log_step("Clicked View Global API Key")
                            break
                    except Exception:
                        continue

                # CF shows "Verify Your Identity" modal — click Send Verification Code → enter OTP from email
                try:
                    send_btn = page.locator("button:has-text('Send Verification Code')").first
                    if send_btn.count() > 0 and send_btn.is_visible(timeout=3000):
                        send_btn.click()
                        time.sleep(2)
                        log_step("Sent verification code for Global API Key")

                        # Poll ammail for the OTP
                        otp_code = None
                        for _ in range(20):
                            time.sleep(5)
                            try:
                                msgs = ammail_request(ammail_base_url, ammail_api_key,
                                                      f"/inboxes/{urllib.parse.quote(email.split('@')[0])}/messages")
                                for msg in (msgs if isinstance(msgs, list) else []):
                                    if 'cloudflare' in str(msg.get('from', '')).lower() or 'cloudflare' in str(msg.get('subject', '')).lower():
                                        mid = msg.get('id', '')
                                        full = ammail_request(ammail_base_url, ammail_api_key, f"/messages/{urllib.parse.quote(str(mid))}")
                                        body = str(full.get('body', '') or full.get('html', '') or full.get('text', ''))
                                        m = re.search(r'\b(\d{6})\b', body)
                                        if m:
                                            otp_code = m.group(1)
                                            log_step(f"OTP untuk Global API Key: {otp_code}")
                                            break
                            except Exception:
                                pass
                            if otp_code:
                                break

                        if otp_code:
                            otp_input = page.locator("input[type='text'], input[placeholder*='code'], input[placeholder*='Code']").first
                            if otp_input.count() > 0:
                                otp_input.fill(otp_code)
                                time.sleep(0.5)
                                for btn_sel in ["button:has-text('Verify')", "button:has-text('Continue')", "button:has-text('Submit')", "button[type='submit']"]:
                                    try:
                                        b = page.locator(btn_sel).first
                                        if b.count() > 0:
                                            b.click()
                                            time.sleep(3)
                                            log_step(f"Entered OTP, clicked {btn_sel}")
                                            break
                                    except Exception:
                                        continue
                except Exception as e:
                    log_step(f"OTP verify step: {e}")

                # CF shows a password confirmation dialog after OTP
                try:
                    pwd_input = page.locator("input[type='password']").first
                    if pwd_input.is_visible(timeout=3000):
                        pwd_input.fill(password)
                        time.sleep(0.5)
                        for btn_sel in ["button:has-text('Continue')", "button[type='submit']", "button:has-text('View')"]:
                            try:
                                b = page.locator(btn_sel).first
                                if b.count() > 0:
                                    b.click()
                                    time.sleep(2)
                                    log_step("Submitted password for Global API Key")
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass

                # Extract Global API Key value
                global_key = None
                page.screenshot(path="/tmp/cf_globalkey_page.png")
                body_text = page.inner_text("body")
                # CF global key is 37 chars hex-ish
                import re as _re2
                gk_match = _re2.search(r'\b([a-f0-9]{37})\b', body_text)
                if gk_match:
                    global_key = gk_match.group(1)
                    log_step(f"Global API Key (from body): {global_key[:8]}...")

                if not global_key:
                    for sel in ["input[readonly]", "code", "[class*='api-key']", "input[type='text']"]:
                        try:
                            el = page.locator(sel).first
                            if el.count() > 0 and el.is_visible(timeout=2000):
                                val = el.input_value() if "input" in sel else el.text_content()
                                val = (val or "").strip()
                                if val and len(val) > 20 and ' ' not in val:
                                    global_key = val
                                    log_step(f"Global API Key: {val[:8]}...")
                                    break
                        except Exception:
                            continue

                if not global_key:
                    log_step("Global API Key tidak ditemukan")
                    return None

                # Use Global API Key to create Workers AI token via CF API
                api_email_header = email  # email dari outer scope
                headers = {
                    "X-Auth-Email": api_email_header,
                    "X-Auth-Key": global_key,
                    "Content-Type": "application/json",
                }
                base_api = "https://api.cloudflare.com/client/v4"

                # Get Workers AI permission group ID
                r = _req.get(f"{base_api}/user/tokens/permission_groups", headers=headers, timeout=15)
                pg_data = r.json()
                workers_ai_id = None
                for pg in pg_data.get('result', []):
                    if 'Workers AI' in pg.get('name', ''):
                        workers_ai_id = pg['id']
                        log_step(f"Workers AI permission group id: {workers_ai_id}")
                        break

                if not workers_ai_id:
                    log_step(f"Workers AI group not found. Available: {[p['name'] for p in pg_data.get('result', [])[:10]]}")
                    return None

                # Create the scoped token
                payload = {
                    "name": "9router-workers-ai",
                    "policies": [{
                        "effect": "allow",
                        "resources": {f"com.cloudflare.api.account.{account_id}": "*"},
                        "permission_groups": [{"id": workers_ai_id}]
                    }]
                }
                r2 = _req.post(f"{base_api}/user/tokens", json=payload, headers=headers, timeout=15)
                resp2 = r2.json()
                log_step(f"Token create via Global Key: {str(resp2)[:200]}")
                if resp2.get('success'):
                    return resp2['result'].get('value')
            except Exception as e:
                log_step(f"Global API Key approach failed: {e}")
            return None

        def create_token_via_session(page):
            """Fallback: browser JS fetch with proper headers."""
            log_step("Mencoba buat token via browser fetch API...")
            try:
                result = page.evaluate(f"""
                    async () => {{
                        const pgResp = await fetch('https://dash.cloudflare.com/api/v4/user/tokens/permission_groups', {{
                            credentials: 'include',
                            headers: {{'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}}
                        }});
                        const pgText = await pgResp.text();
                        let pgData;
                        try {{ pgData = JSON.parse(pgText); }} catch(e) {{ return {{error: 'non-json: ' + pgText.slice(0,100)}}; }}
                        const groups = pgData.result || [];
                        let workersAiId = groups.find(g => g.name && g.name.includes('Workers AI'))?.id;
                        if (!workersAiId) return {{error: 'no Workers AI group', groups: groups.map(g=>g.name).slice(0,10)}};
                        const tokenResp = await fetch('https://dash.cloudflare.com/api/v4/user/tokens', {{
                            method: 'POST', credentials: 'include',
                            headers: {{'Accept': 'application/json', 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}},
                            body: JSON.stringify({{name:'9router-workers-ai',policies:[{{effect:'allow',resources:{{'com.cloudflare.api.account.{account_id}':'*'}},permission_groups:[{{id:workersAiId}}]}}]}})
                        }});
                        const tokenData = await tokenResp.json();
                        return {{success: tokenData.success, value: tokenData.result?.value, error: tokenData.errors?.[0]?.message}};
                    }}
                """)
                log_step(f"Browser fetch result: {str(result)[:300]}")
                if result and result.get('value'):
                    return result['value']
            except Exception as e:
                log_step(f"Browser fetch exception: {e}")
            return None

        try:
            workers_ai_token = create_token_via_global_key(page)
            if workers_ai_token:
                log_step(f"Token via Global API Key: {workers_ai_token[:12]}...")
        except Exception as e:
            log_step(f"Global key token failed: {e}")

        if not workers_ai_token:
            try:
                workers_ai_token = create_token_via_session(page)
                if workers_ai_token:
                    log_step(f"Token via session fetch: {workers_ai_token[:12]}...")
            except Exception as e:
                log_step(f"Session API token failed: {e}")

        # ── Strategy B: Browser UI — /profile/api-tokens/create (dropdown form)
        if not workers_ai_token:
            log_step("Fallback: browser UI token creation")
            for create_url in [
                "https://dash.cloudflare.com/profile/api-tokens/create",
                f"https://dash.cloudflare.com/{account_id}/api-tokens/create",
            ]:
                try:
                    page.goto(create_url, wait_until="domcontentloaded", timeout=25000)
                    wait_for_cf_clearance(page, timeout=15)
                    time.sleep(4)
                    current = page.url
                    log_step(f"Create token URL: {current}")
                    if "api-tokens/create" not in current:
                        log_step("Redirected away, try next...")
                        continue
                    break
                except Exception as e:
                    log_step(f"Nav error: {e}")
                    continue

        # Method 4: navigate to /accounts and parse
        if not account_id:
            try:
                page.goto("https://dash.cloudflare.com/?to=/:account/home", wait_until="domcontentloaded", timeout=15000)
                time.sleep(3)
                url_match = re.search(r"/([a-f0-9]{32})(?:/|$)", page.url)
                if url_match:
                    account_id = url_match.group(1)
                    log_step(f"Account ID via redirect: {account_id[:8]}...")
            except Exception as e:
                log_step(f"account_id method4 error: {e}")

        if account_id:
            log_step(f"Account ID confirmed: {account_id[:8]}...")
        else:
            log_step("WARN: Account ID tidak ditemukan, lanjut tanpa account_id")

        # ── Step 9: Skip Global API Key (needs OTP) — buat Account API Token langsung ──
        global_key = None
        workers_ai_token = None

        if not account_id:
            die("Tidak bisa membuat API Token: account_id tidak ditemukan")

        # ── Step 10: Create Workers AI Token — proper CF UI flow ──────────────
        log_step("Membuat Workers AI API Token via browser...")
        try:
            # 1. Navigate to profile/api-tokens (not account-specific)
            page.goto("https://dash.cloudflare.com/profile/api-tokens", wait_until="domcontentloaded", timeout=25000)
            wait_for_cf_clearance(page, timeout=15)
            time.sleep(3)
            log_step(f"API Tokens page: {page.url}")
            page.screenshot(path="/tmp/cf_tokens_page.png")

            # 2. Click "+ Create Token" button
            for sel in ["button:has-text('Create Token')", "a:has-text('Create Token')", "[href*='create']"]:
                try:
                    b = page.locator(sel).first
                    if b.count() > 0 and b.is_visible(timeout=3000):
                        b.click()
                        time.sleep(2)
                        log_step(f"Clicked Create Token via: {sel}")
                        break
                except Exception:
                    continue

            time.sleep(2)
            page.screenshot(path="/tmp/cf_create_token_page.png")
            log_step(f"After Create Token click: {page.url}")

            # 3. Click "Get started" for Custom Token
            for sel in ["button:has-text('Get started')", "a:has-text('Get started')"]:
                try:
                    b = page.locator(sel).first
                    if b.count() > 0 and b.is_visible(timeout=3000):
                        b.click()
                        time.sleep(2)
                        log_step(f"Clicked Get started via: {sel}")
                        break
                except Exception:
                    continue

            time.sleep(2)
            page.screenshot(path="/tmp/cf_custom_token_form.png")
            log_step(f"Custom token form: {page.url}")

            # 4. Fill Token name
            name_input = page.locator("input").first
            for name_sel in ["input[placeholder*='name' i]", "input[name*='name' i]", "input[aria-label*='name' i]", "input:first-of-type"]:
                try:
                    el = page.locator(name_sel).first
                    if el.count() > 0 and el.is_visible(timeout=2000):
                        el.click()
                        el.fill("9router-workers-ai")
                        time.sleep(0.5)
                        log_step("Token name filled: 9router-workers-ai")
                        break
                except Exception:
                    continue

            # 5. Select Workers AI permission from dropdown
            # The form has 3 dropdowns in Permissions row: [Account] [Select/search] [Select...]
            # Wait for React form to fully hydrate before searching
            try:
                page.wait_for_selector(
                    "input[aria-autocomplete], [role='combobox'], input[placeholder*='Select' i]",
                    timeout=10000
                )
                time.sleep(1)
                log_step("React form loaded, searching dropdowns")
            except Exception as e:
                log_step(f"Wait for form timeout: {e}, continuing anyway")
                time.sleep(2)

            workers_ai_permission_set = False

            # Strategy A: click the second select in the permissions row
            try:
                # Find all select-like elements (combobox/select)
                perm_dropdowns = page.locator("select, [role='combobox'], [role='listbox']").all()
                log_step(f"Found {len(perm_dropdowns)} dropdowns")

                # The second dropdown in the permissions row is for permission type
                # Try typing in a searchable dropdown
                for sel in [
                    "[class*='select'] input",
                    "input[role='combobox']",
                    "input[aria-autocomplete]",
                    "[aria-label*='permission' i] input",
                    "[placeholder*='Select' i]",
                ]:
                    try:
                        els = page.locator(sel).all()
                        for el in els:
                            if el.is_visible():
                                el.click()
                                time.sleep(0.5)
                                el.fill("Workers AI")
                                time.sleep(1)
                                # Click on "Workers AI" option in dropdown
                                wa_opt = page.locator("text=Workers AI").first
                                if wa_opt.count() > 0 and wa_opt.is_visible(timeout=2000):
                                    wa_opt.click()
                                    time.sleep(0.5)
                                    log_step(f"Workers AI selected via: {sel}")
                                    workers_ai_permission_set = True
                                    break
                        if workers_ai_permission_set:
                            break
                    except Exception:
                        continue
            except Exception as e:
                log_step(f"Dropdown strategy A: {e}")

            # Strategy B: use keyboard Tab to navigate to permission select, type Workers AI
            if not workers_ai_permission_set:
                try:
                    # Find native <select> elements
                    selects = page.locator("select").all()
                    log_step(f"Native selects: {len(selects)}")
                    for i, sel_el in enumerate(selects):
                        try:
                            opts = sel_el.evaluate("el => Array.from(el.options).map(o => o.text)")
                            log_step(f"Select {i} options: {opts[:5]}")
                            if any('Workers AI' in o for o in opts):
                                sel_el.select_option(label="Workers AI")
                                time.sleep(0.5)
                                log_step(f"Workers AI selected via native select {i}")
                                workers_ai_permission_set = True
                                break
                        except Exception:
                            continue
                except Exception as e:
                    log_step(f"Strategy B selects: {e}")

            # Strategy C: JS evaluate — find the select with Workers AI and set it
            if not workers_ai_permission_set:
                try:
                    result = page.evaluate("""
                        () => {
                            // Find all select elements
                            const selects = Array.from(document.querySelectorAll('select'));
                            for (const sel of selects) {
                                const opts = Array.from(sel.options);
                                const waOpt = opts.find(o => o.text.trim() === 'Workers AI');
                                if (waOpt) {
                                    sel.value = waOpt.value;
                                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                                    return 'Workers AI set on select: ' + (sel.name || sel.id || 'unnamed');
                                }
                            }
                            return 'Workers AI option not found in any select';
                        }
                    """)
                    log_step(f"JS select: {result}")
                    if 'Workers AI set' in str(result):
                        workers_ai_permission_set = True
                        time.sleep(0.5)
                except Exception as e:
                    log_step(f"Strategy C JS: {e}")

            page.screenshot(path="/tmp/cf_after_perm_select.png")
            log_step(f"After permission selection (set={workers_ai_permission_set})")
            time.sleep(1)

            # 5b. Select "Read" from the third dropdown (React custom dropdown)
            # The "Select..." dropdown is a React custom dropdown — need to click it first
            read_set = False
            time.sleep(0.5)

            # Strategy: click the "Select..." dropdown button, then click "Read"
            try:
                # Find the "Select..." placeholder dropdown and click it
                for sel in [
                    "button:has-text('Select...')",
                    "[class*='select']:has-text('Select...')",
                    "div[class*='control']:has-text('Select...')",
                    "*:has-text('Select...')",
                ]:
                    try:
                        el = page.locator(sel).last  # use .last since "Select" appears in many places
                        if el.count() > 0 and el.is_visible(timeout=2000):
                            el.click()
                            time.sleep(1)
                            log_step(f"Clicked 'Select...' via: {sel}")
                            # Now click "Read" from the opened dropdown
                            for read_sel in ["text='Read'", "[role='option']:has-text('Read')", "li:has-text('Read')"]:
                                try:
                                    r = page.locator(read_sel).first
                                    if r.count() > 0 and r.is_visible(timeout=2000):
                                        r.click()
                                        time.sleep(0.5)
                                        log_step(f"Read selected via: {read_sel}")
                                        read_set = True
                                        break
                                except Exception:
                                    continue
                            if read_set:
                                break
                    except Exception:
                        continue
            except Exception as e:
                log_step(f"Read dropdown click: {e}")

            # Strategy B: use bounding box — the "Select..." is the 3rd dropdown, find by position
            if not read_set:
                try:
                    # Workers AI dropdown bbox → the Select... is to its right
                    wa_loc = page.locator("*:has-text('Workers AI')").first
                    wa_box = wa_loc.bounding_box()
                    if wa_box:
                        # "Select..." dropdown is to the right of Workers AI
                        select_x = wa_box['x'] + wa_box['width'] + 120  # offset right
                        select_y = wa_box['y'] + wa_box['height'] / 2
                        page.mouse.click(select_x, select_y)
                        time.sleep(1)
                        log_step(f"Clicked Select... at estimated position ({select_x:.0f},{select_y:.0f})")
                        page.screenshot(path="/tmp/cf_after_select_click.png")
                        # Find Read option
                        for read_sel in ["text='Read'", "[role='option']:has-text('Read')", "li:has-text('Read')"]:
                            try:
                                r = page.locator(read_sel).first
                                if r.count() > 0 and r.is_visible(timeout=2000):
                                    r.click()
                                    time.sleep(0.5)
                                    log_step(f"Read selected (positional) via: {read_sel}")
                                    read_set = True
                                    break
                            except Exception:
                                continue
                except Exception as e:
                    log_step(f"Read positional: {e}")

            log_step(f"Read access level set: {read_set}")
            page.screenshot(path="/tmp/cf_after_read_select.png")

            # Log all form inputs/selects for debugging
            try:
                form_state = page.evaluate("""
                    () => {
                        const inputs = Array.from(document.querySelectorAll('input, select, textarea'));
                        return inputs.map(el => ({
                            tag: el.tagName, type: el.type, name: el.name,
                            value: el.value, placeholder: el.placeholder
                        })).filter(el => el.value || el.placeholder);
                    }
                """)
                log_step(f"Form state: {str(form_state)[:500]}")
            except Exception:
                pass

            # 6. Scroll down and click "Continue to summary"
            time.sleep(1)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            page.screenshot(path="/tmp/cf_before_continue.png")

            continue_clicked = False
            for sel in [
                "button:has-text('Continue to summary')",
                "input[value*='Continue']",
                "button:has-text('Continue')",
                "button:has-text('Review')",
                "button[type='submit']",
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=3000):
                        loc.scroll_into_view_if_needed()
                        time.sleep(0.3)
                        url_before = page.url
                        bbox = loc.bounding_box()
                        if bbox:
                            page.mouse.move(bbox['x'] + bbox['width']/2, bbox['y'] + bbox['height']/2)
                            time.sleep(0.2)
                            page.mouse.click(bbox['x'] + bbox['width']/2, bbox['y'] + bbox['height']/2)
                            log_step(f"Mouse.click Continue via: {sel}")
                        else:
                            loc.click()
                        time.sleep(3)
                        page.screenshot(path="/tmp/cf_after_continue.png")
                        if page.url != url_before:
                            log_step(f"Navigated to summary: {page.url[:60]}")
                            continue_clicked = True
                            break
                        log_step(f"'{sel}' clicked but URL unchanged")
                        # Check error message
                        try:
                            err = page.evaluate("Array.from(document.querySelectorAll('[class*=error],[class*=alert],[role=alert]')).map(e=>e.innerText).join(' ')")
                            if err:
                                log_step(f"Form error after continue: {err[:200]}")
                        except Exception:
                            pass
                except Exception as e:
                    log_step(f"Continue selector '{sel}' failed: {e}")
                    continue

            log_step(f"Continue to summary: {continue_clicked}")

            # 7. On summary page, click "Create Token"
            time.sleep(2)
            page.screenshot(path="/tmp/cf_summary_page.png")
            for sel in ["button:has-text('Create Token')", "input[value*='Create Token']", "button[type='submit']"]:
                try:
                    b = page.locator(sel).first
                    if b.count() > 0 and b.is_visible(timeout=5000):
                        b.scroll_into_view_if_needed()
                        time.sleep(0.3)
                        b.click()
                        time.sleep(5)
                        log_step(f"Create Token clicked via: {sel}")
                        break
                except Exception:
                    continue

            # 8. Extract token from result page
            page.screenshot(path="/tmp/cf_token_result.png")
            log_step("Screenshot token result saved")

            for sel in ["code", "input[readonly]", "input[type='text'][readonly]",
                        "[data-testid='token-value']", ".cf-input-code",
                        "input[class*='token']", "input[class*='code']", "input[class*='api']"]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=4000):
                        val = el.input_value() if "input" in sel else el.text_content()
                        val = (val or "").strip()
                        if val and len(val) > 10:
                            workers_ai_token = val
                            log_step(f"Workers AI Token berhasil! ({len(val)} chars): {val[:12]}...")
                            break
                except Exception:
                    continue

            # Fallback: extract token-like string from body (cfp_ or similar)
            if not workers_ai_token:
                try:
                    body = page.inner_text("body")
                    import re as _re
                    # CF tokens start with cfut_ or similar
                    for pattern in [r'\b(cfut_[A-Za-z0-9_\-]{30,})\b', r'\b([A-Za-z0-9_\-]{40,})\b']:
                        tok_match = _re.search(pattern, body)
                        if tok_match:
                            workers_ai_token = tok_match.group(1)
                            log_step(f"Token dari body: {workers_ai_token[:12]}...")
                            break
                except Exception:
                    pass

        except Exception as e:
            log_step(f"Token creation error: {e}")
            try:
                page.screenshot(path="/tmp/cf_create_token_err.png")
            except Exception:
                pass


        # Final API key to save
        final_api_key = workers_ai_token or global_key or ""

        if not final_api_key:
            die("Tidak ada API key yang bisa digunakan")

        log_step("Selesai! Menyimpan kredensial ke 9router...")
        success(final_api_key, account_id, args.email)


if __name__ == "__main__":
    main()
