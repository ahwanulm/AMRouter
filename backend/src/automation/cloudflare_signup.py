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
        launch_kwargs["geoip"] = True  # match geolocation to proxy IP (suppresses LeakWarning)

    def _make_camoufox(kw):
        """Launch Camoufox, stripping unsupported kwargs one by one."""
        try:
            return Camoufox(**kw)
        except TypeError:
            kw.pop("os", None)
            try:
                return Camoufox(**kw)
            except TypeError:
                kw.pop("locale", None)
                return Camoufox(**kw)

    try:
        browser_ctx = _make_camoufox(dict(launch_kwargs))
    except Exception as _pe:
        _ps = str(_pe)
        if proxy_dict and any(k in _ps for k in ("InvalidProxy","Tunnel connection","Failed to connect to proxy","ProxyError")):
            log_step(f"Proxy dead ({proxy_dict.get('server','?')}) — fallback tanpa proxy")
            launch_kwargs.pop("proxy", None)
            launch_kwargs.pop("geoip", None)
            browser_ctx = _make_camoufox(dict(launch_kwargs))
        else:
            raise

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
        # Use JS to get all visible text — catch any wording CF uses
        email_already_registered = False
        try:
            page_text_lower = page.evaluate("document.body.innerText").lower()
            log_step(f"Post-signup page snippet: {page_text_lower[:200]}")
            already_kw = [
                "already registered", "already exists", "already in use",
                "already taken", "account exists", "email exists",
                "sudah terdaftar", "already have an account",
                "email address is already"
            ]
            for kw in already_kw:
                if kw in page_text_lower:
                    log_step(f"Email sudah terdaftar ({args.email}) — detected: '{kw}'")
                    email_already_registered = True
                    break
        except Exception as e:
            log_step(f"Post-signup check error: {e}")

        # Also check: if no "check your email" / "verify" success message → assume registered
        if not email_already_registered:
            try:
                success_kw = ["check your email", "verify your email", "verification email", "link has been sent"]
                if not any(kw in page_text_lower for kw in success_kw):
                    # Not success AND not "already registered" → check URL
                    # If still on signup page (no redirect), likely email exists (CF resent silently)
                    if "sign-up" in page.url or "register" in page.url or page.url.endswith("/sign-up"):
                        log_step(f"Still on signup URL after submit — treating as email_already_registered")
                        email_already_registered = True
            except Exception:
                pass

        if email_already_registered:
            # Navigate FRESH to /login (don't carry stale security_token from verify link)
            page.goto("https://dash.cloudflare.com/login", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)


        # ── Step 6: Email verification ────────────────────────────────────────
        if ammail_ok and not email_already_registered:
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
        elif email_already_registered:
            log_step("Email sudah terdaftar — skip verifikasi, langsung ke login form")
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
                        # Take screenshot to see login page state
                        page.screenshot(path="/tmp/cf_login_page.png")

                        # Screenshot login page state
                        page.screenshot(path="/tmp/cf_login_state.png")

                        # Diagnostic: log all inputs on login page
                        try:
                            login_inputs = page.evaluate("""
                                () => Array.from(document.querySelectorAll('input')).map(i => ({
                                    type: i.type, name: i.name, id: i.id,
                                    autocomplete: i.autocomplete, placeholder: i.placeholder,
                                    visible: i.offsetParent !== null
                                }))
                            """)
                            log_step(f"Login page inputs: {login_inputs}")
                        except Exception as e:
                            log_step(f"Login inputs diagnostic: {e}")

                        # CF login is ONE-STEP form (email + password on same page)
                        # Screenshot showed: Email input + Password input + Sign in button
                        # Use broad selectors to find the email/password fields
                        log_step("Menyelesaikan Turnstile login (if any)...")
                        wait_for_cf_clearance(page, timeout=3)
                        try_click_turnstile_checkbox(page)
                        time.sleep(1)

                        # CF Login Step 1: Fill email
                        email_filled = False
                        for sel in [
                            "input[name='email']",
                            "input[type='email']",
                            "input[autocomplete='email']",
                            "input[autocomplete='username']",
                            "input[id*='email' i]",
                            "input[placeholder*='email' i]",
                            # First visible non-hidden, non-password input
                            "form input:not([type='password']):not([type='hidden']):not([type='checkbox'])",
                        ]:
                            try:
                                el = page.locator(sel).first
                                cnt = el.count()
                                if cnt > 0 and el.is_visible(timeout=2000):
                                    el.click(click_count=3)
                                    el.fill(args.email)
                                    email_filled = True
                                    log_step(f"Login email filled via: {sel}")
                                    break
                            except Exception as ex:
                                log_step(f"Login email try {sel}: {type(ex).__name__}")
                                continue

                        if not email_filled:
                            log_step("Email field not found on login page")
                            page.screenshot(path="/tmp/cf_login_noemail.png")

                        # CF Login Step 2: Fill password (same form as email — no Continue needed)
                        pw_filled = False
                        for pw_sel in [
                            "input[type='password']",
                            "input[name='password']",
                            "input[autocomplete='current-password']",
                            "input[autocomplete='new-password']",
                        ]:
                            try:
                                pw_el = page.locator(pw_sel).first
                                if pw_el.count() > 0 and pw_el.is_visible(timeout=2000):
                                    pw_el.click(click_count=3)
                                    pw_el.fill(args.password)
                                    pw_filled = True
                                    log_step(f"Login password filled via: {pw_sel}")
                                    break
                            except Exception:
                                continue

                        if not pw_filled:
                            log_step("Password field not found")
                            page.screenshot(path="/tmp/cf_login_nopw.png")

                        # Solve Turnstile (if any appeared after filling form)
                        wait_for_cf_clearance(page, timeout=3)
                        try_click_turnstile_checkbox(page)
                        time.sleep(1)


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
                        time.sleep(10)
                        page.screenshot(path="/tmp/cf_after_login_submit.png")

                        current_url = page.url
                        log_step(f"After login URL: {current_url}")
                        # If still on login, log error text
                        if "/login" in current_url:
                            try:
                                err_txt = page.evaluate("document.body.innerText")
                                log_step(f"Login page text (first 300): {err_txt[:300]}")
                            except Exception:
                                pass
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
                # Navigate to profile page — CF redirects to /{account_id}/... URL
                page.goto("https://dash.cloudflare.com/profile", wait_until="domcontentloaded", timeout=30000)
                wait_for_cf_clearance(page, timeout=10)
                time.sleep(3)
                # If URL has account_id, capture it now
                _m_profile = re.search(r"/([a-f0-9]{32})(?:/|$)", page.url)
                if _m_profile:
                    _early_account_id = _m_profile.group(1)
                    log_step(f"Account ID from profile URL: {_early_account_id[:8]}...")
                    account_id = _early_account_id
            except Exception as e:
                log_step(f"Dashboard load warning: {e}")


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
        # Method 3: CF API /accounts using page.request.fetch (carries browser session cookies)
        if not account_id:
            try:
                log_step("Mengambil Account ID via CF API (page.request.fetch)...")
                api_resp = page.request.fetch(
                    "https://api.cloudflare.com/client/v4/accounts?per_page=1",
                    method="GET",
                    headers={"Accept": "application/json"}
                )
                log_step(f"CF /accounts status: {api_resp.status}")
                if api_resp.status == 200:
                    data = api_resp.json()
                    if data.get("success") and data.get("result"):
                        account_id = data["result"][0]["id"]
                        log_step(f"Account ID via API: {account_id[:8]}...")
                else:
                    log_step(f"CF /accounts response: {api_resp.text()[:200]}")
            except Exception as e:
                log_step(f"account_id via API error: {e}")

        # Method 4: Navigate to /home and wait for account_id in URL redirect
        if not account_id:
            try:
                log_step("Navigasi /home untuk dapat account_id dari URL...")
                page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded", timeout=20000)
                for _ in range(10):
                    time.sleep(1)
                    m4 = re.search(r"/([a-f0-9]{32})(?:/|$)", page.url)
                    if m4:
                        account_id = m4.group(1)
                        log_step(f"Account ID from / redirect: {account_id[:8]}...")
                        break
                if not account_id:
                    log_step(f"/ redirect final URL: {page.url}")
            except Exception as e:
                log_step(f"Method 4 error: {e}")

        # ── Step 9/10: Buat Workers AI Token via Session API ─────────────────
        global_key = None
        workers_ai_token = None

        if not account_id:
            die("Tidak bisa membuat API Token: account_id tidak ditemukan")

        log_step("Membuat Workers AI API Token...")

        # ── Strategy A: Get Global API Key → create token via CF API ────────────
        # Capture ammail vars into local scope for nested function closure
        _ammail_base_url = args.ammail_base_url or ""
        _ammail_api_key = args.ammail_api_key or ""

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
                                msgs_resp = ammail_request(_ammail_base_url, _ammail_api_key,
                                                      f"/inboxes/{urllib.parse.quote(args.email.split('@')[0])}/messages")
                                # ammail_request returns dict {"messages": [...]} not a list directly
                                msgs_list = msgs_resp.get("messages", []) if isinstance(msgs_resp, dict) else (msgs_resp if isinstance(msgs_resp, list) else [])
                                for msg in msgs_list:
                                    if 'cloudflare' in str(msg.get('from', '')).lower() or 'cloudflare' in str(msg.get('subject', '')).lower():
                                        mid = msg.get('id', '')
                                        full = ammail_request(_ammail_base_url, _ammail_api_key, f"/messages/{urllib.parse.quote(str(mid))}")
                                        msg_body = full.get("message", full) if isinstance(full, dict) else {}
                                        body = str(msg_body.get('body', '') or msg_body.get('html', '') or msg_body.get('text', '') or full.get('body', '') or full.get('html', '') or full.get('text', ''))
                                        # Find ALL 6-digit codes in body, prefer ones near "verification" or "code"
                                        import re as _re_otp
                                        # Prefer code near context words
                                        ctx_m = _re_otp.search(r'(?:verification|verify|code|token)[^\d]{0,20}(\d{6})', body, _re_otp.I)
                                        if not ctx_m:
                                            ctx_m = _re_otp.search(r'(\d{6})(?:[^\d]{0,20}(?:verification|verify|code|token))', body, _re_otp.I)
                                        if not ctx_m:
                                            # Fallback: any 6-digit code that isn't 000000 or repeated digits
                                            for mm in _re_otp.finditer(r'\b(\d{6})\b', body):
                                                cand = mm.group(1)
                                                if cand != '000000' and len(set(cand)) > 2:
                                                    ctx_m = mm
                                                    break
                                        if ctx_m:
                                            otp_code = ctx_m.group(1) if hasattr(ctx_m, 'group') and ctx_m.lastindex else ctx_m.group(0)
                                            # Use group 1 if present (context match), else group 0
                                            try: otp_code = ctx_m.group(1)
                                            except: otp_code = ctx_m.group(0)
                                            if otp_code != '000000' and len(set(otp_code)) > 2:
                                                log_step(f"OTP untuk Global API Key: {otp_code}")
                                                break
                            except Exception as _otp_e:
                                log_step(f"OTP poll error: {_otp_e}")
                            if otp_code:
                                break

                        if otp_code:
                            # Try dialog-specific selectors first to avoid cookie consent inputs
                            otp_input = None
                            for otp_sel in [
                                "[role='dialog'] input[type='text']",
                                "[aria-modal='true'] input[type='text']",
                                "input[autocomplete='one-time-code']",
                                "input[maxlength='6']",
                                "input[placeholder*='code' i]",
                                "input[placeholder*='verification' i]",
                                "input[id*='code' i]",
                            ]:
                                try:
                                    el = page.locator(otp_sel).first
                                    if el.count() > 0 and el.is_visible(timeout=2000):
                                        otp_input = el
                                        log_step(f"OTP input found: {otp_sel}")
                                        break
                                except Exception:
                                    continue
                            if otp_input:
                                otp_input.fill(otp_code)
                                time.sleep(0.5)
                            else:
                                log_step("OTP input not found via dialog selectors")
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
                        pwd_input.fill(args.password)
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
                api_email_header = args.email  # email dari outer scope
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
            """Fallback: use page.request.fetch() (Playwright internal HTTP).
            Carries browser session cookies but bypasses browser CORS + proxy
            restrictions that cause NetworkError in page.evaluate fetch().
            """
            log_step("Mencoba buat token via Playwright request API...")
            try:
                # Try api.cloudflare.com with session cookies (may include CF_Authorization)
                base = "https://api.cloudflare.com/client/v4"
                common_headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Referer": "https://dash.cloudflare.com/",
                    "Origin": "https://dash.cloudflare.com",
                }

                # Also try from within page context (sends CF_Authorization cookie as same-site)
                try:
                    cookie_info = page.evaluate("""
                        async () => {
                            try {
                                const r = await fetch('https://api.cloudflare.com/client/v4/accounts?per_page=1', {
                                    credentials: 'include'
                                });
                                const d = await r.json();
                                return {status: r.status, ok: d.success, count: (d.result||[]).length};
                            } catch(e) { return {error: e.message}; }
                        }
                    """)
                    log_step(f"CF accounts via page.evaluate: {cookie_info}")
                    if isinstance(cookie_info, dict) and cookie_info.get('ok') and cookie_info.get('count', 0) > 0:
                        log_step("Session auth works via evaluate — proceeding with token create")
                except Exception as cke:
                    log_step(f"page.evaluate accounts check: {cke}")

                # Step 1: get Workers AI permission group id
                pg_resp = page.request.fetch(
                    f"{base}/user/tokens/permission_groups",
                    method="GET",
                    headers=common_headers,
                )
                if not pg_resp.ok:
                    log_step(f"permission_groups HTTP {pg_resp.status}")
                    return None
                pg_data = pg_resp.json()
                groups = pg_data.get("result") or []
                workers_ai_id = next(
                    (g["id"] for g in groups if "Workers AI" in g.get("name", "")), None
                )
                if not workers_ai_id:
                    log_step(f"Workers AI group not found. Available: {[g['name'] for g in groups[:10]]}")
                    return None
                log_step(f"Workers AI permission group id: {workers_ai_id}")

                # Step 2: create scoped API token
                payload = {
                    "name": "9router-workers-ai",
                    "policies": [{
                        "effect": "allow",
                        "resources": {f"com.cloudflare.api.account.{account_id}": "*"},
                        "permission_groups": [{"id": workers_ai_id}],
                    }],
                }
                tok_resp = page.request.fetch(
                    f"{base}/user/tokens",
                    method="POST",
                    headers=common_headers,
                    data=__import__("json").dumps(payload),
                )
                tok_data = tok_resp.json()
                log_step(f"Token create result: {str(tok_data)[:300]}")
                if tok_data.get("success"):
                    return tok_data["result"].get("value")
                err = tok_data.get("errors", [{}])[0].get("message", "unknown")
                log_step(f"Token create failed: {err}")
            except Exception as e:
                log_step(f"Playwright request exception: {e}")
            return None

        # Try session API first (fast, no OTP needed)
        try:
            workers_ai_token = create_token_via_session(page)
            if workers_ai_token:
                log_step(f"Token via session fetch: {workers_ai_token[:12]}...")
        except Exception as e:
            log_step(f"Session API token failed: {e}")

        # ── Strategy B: Browser UI — /profile/api-tokens/create (dropdown form)
        if not workers_ai_token:
            log_step("Trying browser UI token creation")

            # Setup route interception BEFORE navigating — capture CF's own token API call
            token_from_route = []
            def _token_route_handler(route):
                req = route.request
                try:
                    resp = route.fetch()
                    if req.method == "POST" and "tokens" in req.url:
                        log_step(f"Route: POST {req.url} → {resp.status}")
                        if resp.status in (200, 201):
                            try:
                                d = resp.json()
                                if d.get("result", {}).get("value"):
                                    token_from_route.append(d["result"]["value"])
                                    log_step(f"TOKEN via route: {d['result']['value'][:10]}...")
                            except Exception:
                                pass
                    route.fulfill(response=resp)
                except Exception as route_err:
                    try: route.continue_()
                    except Exception: pass
            try:
                page.route("**/user/tokens**", _token_route_handler)
            except Exception as route_err:
                log_step(f"Route setup: {re}")

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
            # Helper: dismiss any OneTrust / GDPR cookie consent dialogs
            def dismiss_consent_dialogs(page):
                """Dismiss OneTrust, cookie consent, GDPR popups that block the page."""
                dismissed = False
                for sel in [
                    "button#onetrust-accept-btn-handler",
                    "button#accept-recommended-btn-handler",
                    "#onetrust-accept-btn-handler",
                    "button:has-text('Accept all')",
                    "button:has-text('Accept All')",
                    "button:has-text('Accept All Cookies')",
                    "button:has-text('I Accept')",
                    "button:has-text('Accept')",
                    "button:has-text('Agree')",
                    "button:has-text('Confirm')",
                    "button:has-text('Save Preferences')",
                    "[id*='accept'][id*='cookie']",
                    ".ot-sdk-btn-floating",
                    "[class*='onetrust'] button[class*='accept']",
                    "[class*='onetrust'] button[class*='confirm']",
                ]:
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible(timeout=800):
                            el.click()
                            log_step(f"Dismissed consent via: {sel}")
                            time.sleep(0.5)
                            dismissed = True
                            break
                    except Exception:
                        continue
                # Also try JS dismiss as backup (covers any modal/overlay)
                if not dismissed:
                    try:
                        result = page.evaluate("""
                            () => {
                                // Try standard OneTrust dismiss
                                const btns = Array.from(document.querySelectorAll('button'));
                                for (const btn of btns) {
                                    const txt = btn.textContent.trim().toLowerCase();
                                    if (txt === 'accept all' || txt === 'accept all cookies' ||
                                        txt === 'i accept' || txt === 'save preferences' ||
                                        btn.id === 'onetrust-accept-btn-handler') {
                                        btn.click();
                                        return 'JS dismissed: ' + btn.textContent.trim();
                                    }
                                }
                                // Hide OneTrust overlay if present
                                const ot = document.querySelector('#onetrust-consent-sdk, .onetrust-pc-dark-filter');
                                if (ot) { ot.style.display = 'none'; return 'hidden onetrust overlay'; }
                                return 'no consent dialog found';
                            }
                        """)
                        if "dismissed" in result or "hidden" in result:
                            log_step(f"Consent JS: {result}")
                    except Exception:
                        pass

            # 1. Navigate to profile/api-tokens (not account-specific)
            page.goto("https://dash.cloudflare.com/profile/api-tokens", wait_until="domcontentloaded", timeout=25000)
            wait_for_cf_clearance(page, timeout=15)
            time.sleep(3)
            dismiss_consent_dialogs(page)
            log_step(f"API Tokens page: {page.url}")
            page.screenshot(path="/tmp/cf_tokens_page.png")

            # 2. Click "Create Token" button → wait for template page to render
            for btn_sel in ["button:has-text('Create Token')", "a:has-text('Create Token')"]:
                try:
                    b = page.locator(btn_sel).first
                    if b.count() > 0 and b.is_visible(timeout=3000):
                        b.click()
                        log_step(f"Clicked Create Token via: {btn_sel}")
                        break
                except Exception:
                    continue

            # Wait for template page content (React routing — URL stays same)
            # OneTrust GDPR consent dialog can appear AFTER navigating to template page
            # — retry dismiss up to 3x while waiting for template buttons
            workers_ai_template_used = False
            template_page_ready = False
            for _wait_attempt in range(3):
                try:
                    page.wait_for_selector("button:has-text('Use template')", timeout=5000)
                    template_page_ready = True
                    log_step(f"Template page ready (attempt {_wait_attempt+1})")
                    break
                except Exception:
                    log_step(f"Template wait timeout attempt {_wait_attempt+1} — dismissing consent")
                    dismiss_consent_dialogs(page)
                    time.sleep(2)

            dismiss_consent_dialogs(page)
            page.screenshot(path="/tmp/cf_create_token_page.png")
            log_step(f"After Create Token click: {page.url}")

            try:
                # Find the "Workers AI" template row and click its "Use template" button
                # Structure: <tr> or <div> containing "Workers AI" text + "Use template" button
                wa_row = page.locator("tr:has-text('Workers AI'), li:has-text('Workers AI'), [class*='row']:has-text('Workers AI')").first
                if wa_row.count() > 0 and wa_row.is_visible(timeout=3000):
                    use_btn = wa_row.locator("button:has-text('Use template'), a:has-text('Use template')")
                    if use_btn.count() > 0 and use_btn.is_visible(timeout=2000):
                        use_btn.click()
                        time.sleep(3)
                        log_step("Workers AI template clicked via row")
                        workers_ai_template_used = True
            except Exception as e:
                log_step(f"Template row approach: {e}")

            # Fallback: find "Use template" button next to "Workers AI" text using JS
            if not workers_ai_template_used:
                try:
                    # Get all "Use template" buttons and find the one near "Workers AI" text
                    use_btns = page.locator("button:has-text('Use template')").all()
                    log_step(f"Found {len(use_btns)} Use template buttons")
                    # Workers AI is typically the 5th template (index 4)
                    # Find by evaluating each button's nearby text
                    result = page.evaluate("""
                        () => {
                            const btns = Array.from(document.querySelectorAll('button'));
                            const useTemplateBtns = btns.filter(b => b.textContent.trim() === 'Use template');
                            for (const btn of useTemplateBtns) {
                                // Check if the parent row/section contains "Workers AI"
                                let el = btn.parentElement;
                                for (let i = 0; i < 5; i++) {
                                    if (el && el.textContent.includes('Workers AI') && !el.textContent.includes('Cloudflare Workers')) {
                                        btn.click();
                                        return 'clicked Workers AI template: ' + el.textContent.substring(0, 50);
                                    }
                                    el = el ? el.parentElement : null;
                                }
                            }
                            return 'Workers AI template button not found';
                        }
                    """)
                    log_step(f"JS template click: {result}")
                    if "clicked" in result:
                        workers_ai_template_used = True
                        time.sleep(3)
                except Exception as e:
                    log_step(f"JS template fallback: {e}")

            if workers_ai_template_used:
                # Workers AI template pre-fills the form — just rename the token and submit
                log_step(f"Template form URL: {page.url}")
                page.screenshot(path="/tmp/cf_template_form.png")

                # Rename token from default to "9router-workers-ai"
                try:
                    for name_sel in ["input[name*='name' i]", "input[placeholder*='name' i]", "input[type='text']:first-of-type"]:
                        try:
                            el = page.locator(name_sel).first
                            if el.count() > 0 and el.is_visible(timeout=2000):
                                el.click(click_count=3)
                                el.fill("9router-workers-ai")
                                log_step("Token name renamed: 9router-workers-ai")
                                break
                        except Exception:
                            continue
                except Exception as e:
                    log_step(f"Rename token: {e}")

                workers_ai_permission_set = True  # template already has Workers AI + Read
            else:
                # Fallback to custom token form
                log_step("Template not found, trying custom token form")
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

                # 4. Fill Token name
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

                # 5. Select Workers AI permission
                try:
                    page.wait_for_selector("input[aria-autocomplete]", timeout=8000)
                    time.sleep(1)
                    log_step("React form loaded, searching dropdowns")
                except Exception as e:
                    log_step(f"Wait for form timeout: {e}")
                    time.sleep(2)

                workers_ai_permission_set = False

                # Find all select-like elements
                try:
                    perm_dropdowns = page.locator("select, [role='combobox'], [role='listbox']").all()
                    log_step(f"Found {len(perm_dropdowns)} dropdowns")
                    for sel in ["input[aria-autocomplete]", "[class*='select'] input", "[placeholder*='Select' i]"]:
                        try:
                            els = page.locator(sel).all()
                            for el in els:
                                if el.is_visible():
                                    el.click()
                                    time.sleep(0.5)
                                    el.fill("Workers AI")
                                    time.sleep(1)
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
                    log_step(f"Workers AI dropdown: {e}")

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

            # 5b. Select "Edit" — ONLY for custom form, NOT template
            # Template already has Workers AI:Read; adding again = duplicate permission = validation fail
            read_set = workers_ai_template_used  # template = already done
            if workers_ai_template_used:
                log_step("Template used — skip Read/Edit dropdown (Workers AI:Read already set)")
            else:
                time.sleep(0.5)

                # Strategy A: JS — find all React-Select containers, click the one showing "Select..."
                try:
                    result = page.evaluate("""
                        () => {
                            // Find all elements with placeholder "Select..."
                            const all = Array.from(document.querySelectorAll('*'));
                            for (const el of all) {
                                if (el.children.length === 0 && el.textContent.trim() === 'Select...') {
                                    el.click();
                                    return 'clicked placeholder: ' + el.tagName + ' ' + el.className;
                                }
                            }
                            return 'placeholder not found';
                        }
                    """)
                    log_step(f"JS click Select...: {result}")
                    time.sleep(1)
                    # Now look for Edit or Read option in dropdown
                    for perm_label in ["Edit", "Read"]:
                        for read_sel in [f"text='{perm_label}'", f"[role='option']:has-text('{perm_label}')", f"li:has-text('{perm_label}')"]:
                            try:
                                r = page.locator(read_sel).first
                                if r.count() > 0 and r.is_visible(timeout=1500):
                                    r.click()
                                    time.sleep(0.5)
                                    log_step(f"{perm_label} selected via: {read_sel}")
                                    read_set = True
                                    break
                            except Exception:
                                continue
                        if read_set:
                            break
                except Exception as e:
                    log_step(f"Strategy A JS click: {e}")

                # Strategy B: bounding box — the Select... is to the right of Workers AI row
                if not read_set:
                    try:
                        # Find the Workers AI input in the permissions row
                        wa_inputs = page.locator("input[aria-autocomplete]").all()
                        for wa_inp in wa_inputs:
                            try:
                                if "Workers AI" in (wa_inp.input_value() or ""):
                                    wa_box = wa_inp.bounding_box()
                                    if wa_box:
                                        # The Select... dropdown is to the right
                                        select_x = wa_box["x"] + wa_box["width"] + 200
                                        select_y = wa_box["y"] + wa_box["height"] / 2
                                        page.mouse.click(select_x, select_y)
                                        time.sleep(1)
                                        log_step(f"Positional click Select... at ({select_x:.0f},{select_y:.0f})")
                                        page.screenshot(path="/tmp/cf_after_select_click.png")
                                        for perm_label in ["Edit", "Read"]:
                                            for read_sel in [f"text='{perm_label}'", f"[role='option']:has-text('{perm_label}')"]:
                                                try:
                                                    r = page.locator(read_sel).first
                                                    if r.count() > 0 and r.is_visible(timeout=1500):
                                                        r.click()
                                                        time.sleep(0.5)
                                                        log_step(f"{perm_label} selected (positional)")
                                                        read_set = True
                                                        break
                                                except Exception:
                                                    continue
                                            if read_set:
                                                break
                                        break
                            except Exception:
                                continue
                    except Exception as e:
                        log_step(f"Strategy B positional: {e}")

                # Strategy C: keyboard Tab navigation
                if not read_set:
                    try:
                        page.keyboard.press("Tab")
                        time.sleep(0.5)
                        page.keyboard.press("Tab")
                        time.sleep(0.5)
                        page.keyboard.press("Enter")
                        time.sleep(0.8)
                        # Try arrow down to navigate options
                        page.keyboard.press("ArrowDown")
                        time.sleep(0.3)
                        page.keyboard.press("Enter")
                        time.sleep(0.5)
                        log_step("Read/Edit via Tab+Enter keyboard")
                        read_set = True
                    except Exception as e:
                        log_step(f"Strategy C keyboard: {e}")

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

            # 6. Fill Account Resources then click "Continue to summary"
            time.sleep(1)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)

            # Account Resources — [Include ▼][Select... ▼] — REQUIRED (red error if empty)
            # "Select..." is a React Select — click .react-select__control or .react-select__dropdown-indicator
            try:
                ar_opened = page.evaluate("""
                    () => {
                        // Find react-select__control that has "Select..." placeholder (= Account Resources)
                        const ctrls = Array.from(document.querySelectorAll('[class*="react-select__control"]'));
                        for (const ctrl of ctrls) {
                            const ph = ctrl.querySelector('[class*="react-select__placeholder"]');
                            if (ph && ph.textContent.trim() === 'Select...') {
                                // Click the dropdown indicator (the ▼ arrow)
                                const ind = ctrl.querySelector('[class*="react-select__dropdown-indicator"]');
                                if (ind) { ind.click(); return 'indicator clicked'; }
                                ctrl.click();
                                return 'control clicked';
                            }
                        }
                        return 'no Select... control found';
                    }
                """)
                log_step(f"Account Resources React Select: {ar_opened}")

                if "clicked" in ar_opened:
                    # Take screenshot to see dropdown state
                    page.screenshot(path="/tmp/cf_ar_dropdown.png")
                    time.sleep(2)  # Wait for async option load

                    # Step 1: Check options WITHOUT typing (initial load)
                    opts_initial = page.evaluate("""
                        () => {
                            const opts = Array.from(document.querySelectorAll('[class*="react-select__option"], [class*="option"]'));
                            const menu = document.querySelector('[class*="react-select__menu"]');
                            const menuHtml = menu ? menu.innerHTML.substring(0, 300) : 'NO MENU';
                            return {
                                opts: opts.filter(o => o.offsetParent !== null).map(o => o.textContent.trim()),
                                menuHtml: menuHtml
                            };
                        }
                    """)
                    log_step(f"AR initial options: {opts_initial.get('opts', [])} | menu: {opts_initial.get('menuHtml','')[:100]}")

                    # Take screenshot to see what menu looks like
                    page.screenshot(path="/tmp/cf_ar_menu.png")

                    ar_selected = False
                    if opts_initial.get('opts'):
                        first = page.locator("[class*='react-select__option']").first
                        if first.count() > 0 and first.is_visible(timeout=1000):
                            txt = first.text_content() or "?"
                            first.click()
                            time.sleep(0.5)
                            log_step(f"Account Resources selected (initial): {txt[:60]}")
                            ar_selected = True

                    # Step 2: If still empty, try typing "all"
                    if not ar_selected:
                        page.keyboard.type("all", delay=80)
                        time.sleep(2)
                        opts_all = page.evaluate("""() => {
                            const opts = Array.from(document.querySelectorAll('[class*="react-select__option"]'));
                            return opts.filter(o => o.offsetParent !== null).map(o => o.textContent.trim());
                        }""")
                        log_step(f"AR after 'all': {opts_all}")
                        if opts_all:
                            page.locator("[class*='react-select__option']").first.click()
                            ar_selected = True
                            log_step(f"AR selected after 'all': {opts_all[0][:50]}")

                    # Step 3: Try account_id prefix
                    if not ar_selected:
                        page.keyboard.press("Control+a")
                        page.keyboard.type(account_id[:8], delay=80)
                        time.sleep(2)
                        opts_id = page.evaluate("""() => {
                            const opts = Array.from(document.querySelectorAll('[class*="react-select__option"]'));
                            return opts.filter(o => o.offsetParent !== null).map(o => o.textContent.trim());
                        }""")
                        log_step(f"AR after acct_id: {opts_id}")
                        if opts_id:
                            page.locator("[class*='react-select__option']").first.click()
                            ar_selected = True
                            log_step(f"AR selected after acct_id: {opts_id[0][:50]}")

                    if not ar_selected:
                        page.keyboard.press("Escape")
                        log_step(f"Account Resources: no options found — trying React inject")

                        # React fiber inject: walk up fiber tree to find onChange handler
                        inject_result = page.evaluate(f"""
                            () => {{
                                const ctrls = Array.from(document.querySelectorAll('[class*="react-select__control"]'));
                                const arCtrl = ctrls.find(c => {{
                                    const ph = c.querySelector('[class*="react-select__placeholder"]');
                                    return ph && ph.textContent.trim() === 'Select...';
                                }});
                                if (!arCtrl) return 'No Select... control';
                                const input = arCtrl.querySelector('input');
                                if (!input) return 'No input';
                                const fk = Object.keys(input).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance') || k.startsWith('__reactProps'));
                                if (!fk) {{
                                    const allKeys = Object.keys(input).filter(k=>k.startsWith('__')).join(',');
                                    return 'No fiber. React keys: ' + allKeys;
                                }}
                                let fiber = input[fk];
                                for (let i = 0; i < 25; i++) {{
                                    if (!fiber || !fiber.return) break;
                                    fiber = fiber.return;
                                    const p = fiber.memoizedProps;
                                    if (p && typeof p.onChange === 'function') {{
                                        try {{
                                            p.onChange(
                                                {{value: '{account_id}', label: 'My Account'}},
                                                {{action: 'select-option'}}
                                            );
                                            return 'React onChange called OK';
                                        }} catch(e) {{ return 'onChange error: ' + e.message; }}
                                    }}
                                }}
                                return 'onChange not found';
                            }}
                        """)
                        log_step(f"Account Resources React inject: {inject_result}")
                        time.sleep(1)

            except Exception as e:
                log_step(f"Account Resources error: {e}")

            page.screenshot(path="/tmp/cf_before_continue.png")

            def _is_summary_page():
                """CF uses React SPA — URL never changes. Detect summary by content.
                IMPORTANT: use 'token will affect' only — NOT 'summary' which matches
                the 'Continue to summary' BUTTON TEXT on the form page (false positive)."""
                try:
                    txt = page.inner_text("body")
                    # "token will affect" only appears on the actual summary page
                    # "Workers AI API token summary" also works
                    return ("token will affect" in txt or "API token summary" in txt)
                except Exception:
                    return False

            continue_clicked = False  # always try clicking Continue first
            if not continue_clicked:
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
                            if _is_summary_page():
                                log_step("Summary page detected (React routing)")
                                continue_clicked = True
                                break
                            log_step(f"'{sel}' clicked, not on summary yet")
                            try:
                                err = page.evaluate("Array.from(document.querySelectorAll('[class*=error],[class*=alert],[role=alert]')).map(e=>e.innerText).join(' ')")
                                if err:
                                    log_step(f"Form error: {err[:200]}")
                            except Exception:
                                pass
                    except Exception as e:
                        log_step(f"Continue '{sel}' failed: {e}")
                        continue

            log_step(f"Continue to summary: {continue_clicked}")

            # 7a. If "Continue to summary" failed, try CF API via browser session cookies
            # This bypasses ALL form UI issues — uses browser session (cf_clearance + cookies)
            if not continue_clicked:
                log_step("Continue failed — trying CF API via browser session (page.evaluate fetch)")
                try:
                    # The permission group IDs are hardcoded from CF's workers-ai template:
                    # a92d2450e05d4e7bb7d0a64968f83d11 = Workers AI Read
                    # bacc64e0f6c34fc0883a1223f938a104 = Workers AI Edit  
                    # account_id is available from earlier login step
                    # page.request.fetch uses browser cookies (avoids CORS — runs outside browser JS)
                    import json as _json
                    api_payload = _json.dumps({
                        "name": "Workers AI",
                        "policies": [{
                            "effect": "allow",
                            "resources": {
                                f"com.cloudflare.api.account.{account_id}": "*"
                            },
                            "permission_groups": [
                                {"id": "a92d2450e05d4e7bb7d0a64968f83d11"},
                                {"id": "bacc64e0f6c34fc0883a1223f938a104"}
                            ]
                        }]
                    })
                    api_resp = page.request.fetch(
                        "https://api.cloudflare.com/client/v4/user/tokens",
                        method="POST",
                        headers={"Content-Type": "application/json", "Accept": "application/json"},
                        data=api_payload
                    )
                    log_step(f"CF API /user/tokens status: {api_resp.status}")
                    if api_resp.status in (200, 201):
                        api_data = api_resp.json()
                        if api_data.get("success") and api_data.get("result", {}).get("value"):
                            api_token = api_data["result"]["value"]
                            log_step(f"CF API token created: {api_token[:10]}...")
                            output_result({"status": "ok", "email": args.email, "api_key": api_token, "account_id": account_id})
                            sys.exit(0)
                        else:
                            log_step(f"CF API token create failed: {api_data.get('errors', 'unknown')}")
                    else:
                        body_text = api_resp.text()[:300]
                        log_step(f"CF API HTTP {api_resp.status}: {body_text}")
                except Exception as e:
                    log_step(f"CF API fallback error: {e}")

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

            # CF token result page shows token in a dashed-border div as plain text
            # Also check <code>, <input readonly>, etc.
            # Try cfut_ pattern directly first from page body (most reliable)
            try:
                body_text = page.inner_text("body")
                import re as _re_tok
                cfut_m = _re_tok.search(r'\b(cfut_[A-Za-z0-9_\-]{30,})\b', body_text)
                if cfut_m:
                    workers_ai_token = cfut_m.group(1)
                    log_step(f"Token dari body regex: {workers_ai_token[:12]}...")
            except Exception as _e:
                log_step(f"Body token regex: {_e}")

            # Fallback: try specific selectors
            if not workers_ai_token:
                for sel in ["code", "input[readonly]", "input[type='text'][readonly]",
                            "[data-testid='token-value']", ".cf-input-code",
                            "input[class*='token']", "input[class*='code']", "input[class*='api']"]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            val = el.input_value() if "input" in sel else el.text_content()
                            val = (val or "").strip()
                            if val and len(val) > 10 and ' ' not in val:
                                workers_ai_token = val
                                log_step(f"Token dari selector {sel}: {val[:12]}...")
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
        if not workers_ai_token and token_from_route:
            workers_ai_token = token_from_route[0]
            log_step(f"Token from route: {workers_ai_token[:10]}...")

        # Strategy A: Global API Key from dashboard UI (last resort — needs OTP from email)
        if not workers_ai_token and ammail_ok:
            log_step("Fallback: mencoba Global API Key dari dashboard UI...")
            try:
                global_key_token = create_token_via_global_key(page)
                if global_key_token:
                    workers_ai_token = global_key_token
                    log_step(f"Workers AI token via Global Key: {workers_ai_token[:10]}...")
            except Exception as gke:
                log_step(f"Global API Key fallback error: {gke}")

        if not workers_ai_token:
            die("Tidak ada API key yang bisa digunakan")


        log_step("Selesai! Menyimpan kredensial ke 9router...")
        success(workers_ai_token, account_id, args.email)


if __name__ == "__main__":
    main()
