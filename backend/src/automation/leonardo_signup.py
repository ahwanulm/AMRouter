#!/usr/bin/env python3
import sys
import json
import argparse
import time
import logging
import re
import sqlite3
import socket
import os
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

# Patch Playwright's Locator.is_visible
try:
    from playwright.sync_api import Locator
    _orig_is_visible = Locator.is_visible
    def _patched_is_visible(self, *args, **kwargs):
        timeout = kwargs.pop("timeout", None)
        if timeout is None and len(args) > 0: timeout = args[0]
        if timeout is not None:
            try:
                self.wait_for(state="visible", timeout=float(timeout))
                return True
            except Exception: return False
        return _orig_is_visible(self, *args, **kwargs)
    Locator.is_visible = _patched_is_visible
except ImportError: pass

START_TIME = time.time()
def log_step(message: str, *args):
    elapsed = int(time.time() - START_TIME)
    duration = f"{elapsed}s" if elapsed < 60 else f"{elapsed // 60}m {elapsed % 60}s"
    full_msg = message % args if args else message
    print(json.dumps({"step": f"{full_msg} [{duration}]"}), flush=True)

def safe_email_to_dirname(email: str) -> str:
    cleaned = (email or "").strip().lower().replace("@", "_at_")
    return re.sub(r"[^a-z0-9._-]+", "_", cleaned).strip("._-")

def click_first(page, selectors, timeout_ms: int = 8000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    loc.click(timeout=2000)
                    return True
            except Exception: continue
        time.sleep(0.3)
    return False

def fill_first(page, selectors, value: str, timeout_ms: int = 8000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    loc.fill(value, timeout=2000)
                    return True
            except Exception: continue
        time.sleep(0.3)
    return False

def _react_invoke_click(page, text_marker: str) -> bool:
    try:
        return page.evaluate("""(marker) => {
            const btn = Array.from(document.querySelectorAll('button, [role="button"], a')).find(el => 
                (el.innerText || '').toLowerCase().includes(marker.toLowerCase()) && !/Cancel|Batal/i.test(el.innerText)
            );
            if (!btn) return false;
            const propsKey = Object.keys(btn).find(k => k.startsWith('__reactProps$'));
            if (propsKey && btn[propsKey] && typeof btn[propsKey].onClick === 'function') {
                btn[propsKey].onClick({ preventDefault:()=>{}, stopPropagation:()=>{}, target:btn, type:'click' });
                return true;
            }
            return false;
        }""", text_marker)
    except Exception: return False

def _bypass_canva_modals(page):
    try:
        page.evaluate("""() => {
            const sels = ['[role="dialog"]', '.modal', '[aria-modal="true"]', 'div:has(> button[aria-label="Close"])'];
            sels.forEach(s => document.querySelectorAll(s).forEach(e => e.remove()));
            document.querySelectorAll('[class*="backdrop"], [class*="overlay"]').forEach(e => e.remove());
        }""")
    except Exception: pass

import urllib.request

def get_db_path() -> Path:
    return Path.home() / ".9router-v2" / "db" / "data.sqlite"

def iso_to_unix(iso_str: str) -> int:
    try:
        clean = iso_str.replace("Z", "+00:00")
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(clean)
        return int(dt.timestamp())
    except Exception:
        return int(time.time())

def load_settings_db() -> dict:
    db_path = get_db_path()
    if not db_path.exists(): return {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT data FROM settings WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        if row: return json.loads(row["data"])
    except Exception: pass
    return {}

def extract_otp_canva(text: str, html: str = "", subject: str = "") -> tuple:
    """Extract 6-digit OTP or passwordless URL from Canva email."""
    parts = [subject or "", text or "", html or ""]
    haystack = "\n".join(filter(None, parts))
    # Labeled code
    m = re.search(r'(?:verification\s*code|code\s*(?:is|:)|one[-\s]?time)\s*[:#-]?\s*([0-9]{4,8})\b', haystack, re.IGNORECASE)
    if m: return m.group(1), ""
    # Canva passwordless link
    m_url = re.search(r'https://www\.canva\.com/passwordless/[^\s"<>]+', haystack)
    if m_url: return "", m_url.group(0).replace("&amp;", "&")
    # Loose 6-digit
    m_loose = re.search(r'(?<![0-9])([0-9]{6})(?![0-9])', haystack)
    if m_loose: return m_loose.group(1), ""
    return "", ""

def sync_ammail_messages(email: str, since_ts: int, settings: dict):
    db_path = get_db_path()
    if not db_path.exists(): return
    api_key = settings.get("ammail_api_key")
    base_url = settings.get("ammail_base_url")
    fallback_url = settings.get("ammail_cf_workers_dev_url")
    if not api_key or (not base_url and not fallback_url): return
    alias = email.split("@")[0]
    domain = email.split("@")[1] if "@" in email else ""
    urls = [u.rstrip("/") for u in [base_url, fallback_url] if u]
    messages = []
    for base in urls:
        try:
            req = urllib.request.Request(f"{base}/api/inboxes/{alias}/messages",
                headers={"X-API-Key": api_key, "Accept": "application/json",
                         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8"))
                messages = data.get("messages", [])
                if messages: break
        except Exception: continue
    for msg in messages:
        msg_id = msg.get("id")
        if not msg_id: continue
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM ammailOtps WHERE messageShortId = ?", (msg_id,))
            if cursor.fetchone(): conn.close(); continue
            conn.close()
        except Exception: continue
        full_msg = None
        for base in urls:
            try:
                req = urllib.request.Request(f"{base}/api/messages/{msg_id}",
                    headers={"X-API-Key": api_key, "Accept": "application/json",
                             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
                with urllib.request.urlopen(req, timeout=10) as res:
                    data = json.loads(res.read().decode("utf-8"))
                    full_msg = data.get("message")
                    if full_msg: break
            except Exception: continue
        if not full_msg: continue
        body_text = str(full_msg.get("text") or msg.get("snippet") or "")
        body_html = str(full_msg.get("html") or "")
        from_data = full_msg.get("from") or msg.get("from") or {}
        sender = str(from_data.get("address") or from_data.get("name") or "")
        subject = str(full_msg.get("subject") or msg.get("subject") or "")
        received_at = iso_to_unix(str(full_msg.get("receivedAt") or msg.get("receivedAt") or ""))
        otp_code, verify_url = extract_otp_canva(body_text, body_html, subject)
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("""INSERT OR IGNORE INTO ammailOtps
                (address, alias, domain, sender, subject, otpCode, verifyUrl,
                 bodyText, bodyHtml, messageShortId, rawEventJson, receivedAt, usedAt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (email, alias, domain, sender, subject, otp_code, verify_url,
                 body_text, body_html, msg_id, json.dumps(full_msg), received_at))
            conn.commit(); conn.close()
        except Exception: pass

def wait_for_otp_from_db(email: str, since_ts: int, settings: dict, timeout: int = 180) -> tuple:
    db_path = get_db_path()
    if not db_path.exists():
        log_step(f"DB tidak ditemukan: {db_path}")
        return "", ""
    deadline = time.time() + timeout
    alias = email.split('@')[0]
    last_log = 0
    while time.time() < deadline:
        try: sync_ammail_messages(email, since_ts, settings)
        except Exception: pass
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, otpCode, verifyUrl FROM ammailOtps WHERE LOWER(address) = ? AND receivedAt >= ? AND usedAt = 0 ORDER BY receivedAt DESC LIMIT 1",
                (email.lower(), since_ts))
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE ammailOtps SET usedAt = ? WHERE id = ?", (int(time.time()), row["id"]))
                conn.commit(); conn.close()
                return row["otpCode"] or "", row["verifyUrl"] or ""
            conn.close()
        except Exception: pass
        # Emit heartbeat setiap 5 detik agar timer UI terus jalan
        now = time.time()
        if now - last_log >= 5:
            remaining = int(deadline - now)
            log_step(f"Menunggu OTP Canva ({alias}@...) — sisa {remaining}s")
            last_log = now
        time.sleep(2)
    return "", ""

def enroll_canva_via_email(page, invite_link: str, email: str, password: str, settings: dict = None) -> bool:
    if settings is None: settings = {}
    # Buat inbox di ammail sebelum mulai (kalau belum ada)
    api_key = settings.get("ammail_api_key")
    base_url = (settings.get("ammail_base_url") or settings.get("ammail_cf_workers_dev_url") or "").rstrip("/")
    if api_key and base_url:
        alias = email.split("@")[0]
        domain = email.split("@")[1] if "@" in email else ""
        try:
            body_bytes = json.dumps({"alias": alias, "domain": domain}).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/api/inboxes",
                data=body_bytes,
                method="POST"
            )
            req.add_header("X-API-Key", api_key)
            req.add_header("Content-Type", "application/json")
            req.add_header("Accept", "application/json")
            req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            with urllib.request.urlopen(req, timeout=10) as res:
                res_data = json.loads(res.read().decode())
                addr = (res_data.get("inbox") or {}).get("address", email)
                log_step(f"Inbox siap: {addr}")
        except Exception as e:
            log_step(f"[WARN] Inbox ammail: {e}")

    log_step("Membuka Canva invite link...")
    page.goto(invite_link, wait_until="domcontentloaded", timeout=60000)
    time.sleep(2)
    _bypass_canva_modals(page)
    click_first(page, ["button:has-text('Accept all cookies')", "button:has-text('Accept')"], timeout_ms=3000)

    log_step("Mengisi email...")
    # Step 1: Klik tombol "Continue with email" — Canva hide field di balik button ini
    email_btn_selectors = [
        "button[aria-label='Continue with email']",
        "button[aria-label='Sign up with email']",
        "button[aria-label='Log in with email']",
        "button[aria-label='Lanjutkan dengan email']",
        "button:has-text('Continue with email')",
        "button:has-text('Sign up with email')",
        "button:has-text('Use email')",
        "button:has-text('Lanjutkan dengan email')",
    ]
    email_btn = None
    deadline_btn = time.time() + 20
    while time.time() < deadline_btn and email_btn is None:
        for sel in email_btn_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=400):
                    email_btn = loc; break
            except Exception: continue
        if email_btn is None: time.sleep(0.8)

    if email_btn:
        try:
            email_btn.click(force=True, timeout=5000)
        except Exception:
            _react_invoke_click(page, "Continue with email")
    
    # Step 2: Isi email — Canva pakai input[name='username'][inputmode='email']
    email_input_selectors = [
        "input[name='username'][inputmode='email']",
        "input[autocomplete='username'][inputmode='email']",
        "input[type='email']",
        "input[name='email']",
        "input[inputmode='email']",
        "input[placeholder*='email' i]",
        "input[autocomplete='email']",
        "input[data-testid*='email' i]",
        "input[aria-label*='email' i]",
        "input[autocomplete='username']",
        "input:not([type='password']):not([type='hidden']):not([type='checkbox'])",
    ]
    email_input = None
    deadline_in = time.time() + 20
    while time.time() < deadline_in and email_input is None:
        for sel in email_input_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    email_input = loc; break
            except Exception: continue
        if email_input is None:
            time.sleep(0.8)
            log_step(f"Menunggu email input... URL: {page.url[:50]}")
    
    if email_input is None:
        log_step("Email field tidak ditemukan!")
        if "/home" in page.url: return True
    else:
        email_input.fill(email)

    # OTP timestamp SETELAH submit (dari leoapi-main)
    otp_since_ts = int(time.time())
    click_first(page, ["button[type='submit']", "button:has-text('Continue')", "button:has-text('Lanjutkan')"], timeout_ms=8000)
    
    deadline = time.time() + 150
    otp_filled = False
    
    while time.time() < deadline:
        _bypass_canva_modals(page)
        cur_url = page.url.lower()

        # SUCCESS: Dashboard reached
        if "canva.com" in cur_url and not any(x in cur_url for x in ["/signup", "/login", "/otp", "/brand/join"]):
            log_step(f"Canva page tercapai: {cur_url[:60]} [DONE]")
            return True

        # Juga sukses jika OTP sudah diisi dan sudah di canva.com (apapun page-nya)
        if otp_filled and "canva.com" in cur_url:
            log_step(f"Canva OTP sukses — page: {cur_url[:60]} [DONE]")
            return True

        # Password (existing account)
        pw_input = page.locator("input[type='password']").first
        if pw_input.count() > 0 and pw_input.is_visible(timeout=300):
            log_step("Mengisi password...")
            pw_input.fill(password)
            click_first(page, ["button[type='submit']", "button:has-text('Log in')"])
            time.sleep(5); continue

        # Name (new account) — Canva pakai input[autocomplete='name'], bukan input[name='firstName']
        name_input = page.locator(
            "input[autocomplete='name'], input[name='firstName'], input[name='name'], input[name='fullName']"
        ).first
        if name_input.count() > 0 and name_input.is_visible(timeout=300):
            log_step("Mengisi nama akun baru...")
            name_input.fill("Amstream User")
            click_first(page, ["button[type='submit']", "button:has-text('Continue')", "button:has-text('Create account')"])
            time.sleep(5); continue

        # OTP screen — adopt leoapi-main strict selectors + text marker fallback
        STRICT_OTP = [
            "input[autocomplete='one-time-code']",
            "input[name='code']", "input[name='otp']",
            "input[name='verificationCode']", "input[name='verification_code']",
            "input[id*='otp' i]:not([id*='no_otp' i])",
            "input[data-testid*='otp' i]",
            "input[aria-label*='code' i]:not([aria-label*='country' i])",
        ]
        OTP_TEXT = ["Check your email", "Enter the code", "Verification code", "We sent a code",
                    "We've sent", "Cek email", "Masukkan kode", "Enter the verification"]
        
        otp_loc = None
        otp_type = None
        # Box-style (6 single-char inputs)
        boxes = page.locator("input[maxlength='1'], input[data-testid*='code-input' i]")
        if boxes.count() >= 4 and boxes.first.is_visible(timeout=300):
            otp_type, otp_loc = "box", boxes
        else:
            for sel in STRICT_OTP:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=300):
                        otp_type, otp_loc = "single", loc; break
                except Exception: continue
        # Text marker fallback
        if not otp_loc and not otp_filled:
            try:
                body = page.inner_text("body", timeout=1000)
                if any(m.lower() in body.lower() for m in OTP_TEXT):
                    for sel in STRICT_OTP:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            otp_type, otp_loc = "single", loc; break
            except Exception: pass

        if otp_loc and not otp_filled:
            log_step(f"OTP screen terdeteksi — polling ammail ({email.split('@')[0]})...")
            otp_code, verify_url = wait_for_otp_from_db(email, otp_since_ts, settings)
            if otp_code:
                digits = re.sub(r"\D", "", otp_code)
                log_step(f"OTP didapat: {otp_code}, mengisi...")
                if otp_type == "box":
                    for i in range(min(boxes.count(), len(digits))): boxes.nth(i).fill(digits[i])
                else:
                    otp_loc.fill(digits)
                otp_filled = True
                time.sleep(1)
                click_first(page, ["button[type='submit']", "button:has-text('Verify')", "button:has-text('Continue')"])
                time.sleep(4)
            elif verify_url:
                log_step("Magic link didapat, navigasi...")
                page.goto(verify_url)
                otp_filled = True
                time.sleep(4)
            continue

        # Join Team confirmation page (AFTER auth, different from /brand/join invite page)
        # /brand/join?token=... = auth page, teams/accept or teams/join = post-auth confirmation
        if otp_filled and ("teams/accept" in cur_url or "teams/join" in cur_url or ("/teams/" in cur_url and "join" not in cur_url)):
            log_step("Halaman konfirmasi Join Team, mengklik...")
            if not click_first(page, ["button:has-text('Join team')", "button:has-text('Join the team')", "button:has-text('Gabung')", "button:has-text('Accept')"], timeout_ms=3000):
                _react_invoke_click(page, "Join")
            time.sleep(3)
        
        # Log current state setiap 10 detik — tampilkan fase + URL pendek
        elapsed = int(time.time() - START_TIME)
        if elapsed % 10 < 2:
            short = page.url.replace("https://","").replace("www.","")[:55]
            phase = "OTP dikirim" if otp_filled else ("Menunggu OTP" if otp_loc else "Inisialisasi")
            log_step(f"[{phase}] {short}")

        time.sleep(2)
    return False

def _click_canva_authorize_v2(page, email: str) -> bool:
    primary = ["button:has-text('Allow')", "button:has-text('Authorize')", "button:has-text('Continue')", "button:has-text('Allow access')"]
    target = None
    for s in primary:
        loc = page.locator(s).first
        if loc.count() > 0: target = loc; break
    
    url_before = page.url
    if target:
        try:
            target.click(timeout=3000)
            time.sleep(2)
            if page.url != url_before: return True
        except Exception: pass
    return _react_invoke_click(page, "Allow") or _react_invoke_click(page, "Continue")

def run_automation(email, password, invite_link, proxy=None, headless=True, skip_canva=False):
    settings = load_settings_db()
    if skip_canva: settings["skip_canva"] = True
    import asyncio
    from camoufox.sync_api import Camoufox

    profile_dir = Path(f"profiles/{safe_email_to_dirname(email)}")
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Fix async event loop conflicts (dari leoapi-main)
    try:
        asyncio.set_event_loop(None)
        if hasattr(asyncio, "events") and hasattr(asyncio.events, "_set_running_loop"):
            asyncio.events._set_running_loop(None)
    except Exception: pass

    kwargs = dict(
        headless=headless,
        persistent_context=True,
        user_data_dir=str(profile_dir),
        humanize=True,
        geoip=True,
        locale="en-US",
        os=("windows", "macos", "linux"),
    )

    if proxy:
        p_url = urlparse(proxy)
        kwargs["proxy"] = {"server": f"{p_url.scheme}://{p_url.hostname}:{p_url.port}"}
        if p_url.username: kwargs["proxy"]["username"] = p_url.username
        if p_url.password: kwargs["proxy"]["password"] = p_url.password

    # Graceful TypeError fallback (dari leoapi-main)
    ctx = None
    try:
        ctx = Camoufox(**kwargs)
    except TypeError:
        for drop in ("os", "geoip", "humanize"):
            kwargs.pop(drop, None)
            try:
                ctx = Camoufox(**kwargs)
                break
            except TypeError:
                continue
        if ctx is None:
            kwargs.pop("locale", None)
            ctx = Camoufox(**kwargs)

    try:
        with ctx as browser:
            page = browser.new_page()

            # 1. Canva Enroll
            if not settings.get("skip_canva"):
                if not enroll_canva_via_email(page, invite_link, email, password, settings):
                    sys.stdout.write(json.dumps({"status": "error", "message": "Gagal pendaftaran Canva — lihat step log"}) + "\n")
                    sys.stdout.flush()
                    return False
                # Emit canva_enrolled agar Node update DB
                sys.stdout.write(json.dumps({"canva_enrolled": True}) + "\n")
                sys.stdout.flush()
            else:
                log_step("Skip Canva enrollment as requested.")

            # 2. Leonardo Signup
            # Intercept /api/auth/get-session untuk capture JWT langsung dari response
            captured_jwt = {"value": ""}
            def _find_jwt_in(obj, depth=0):
                if depth > 6: return ""
                if isinstance(obj, str) and obj.startswith("eyJ") and len(obj) > 100: return obj
                if isinstance(obj, dict):
                    for v in obj.values():
                        r = _find_jwt_in(v, depth+1)
                        if r: return r
                if isinstance(obj, list):
                    for v in obj:
                        r = _find_jwt_in(v, depth+1)
                        if r: return r
                return ""
            def handle_response(response):
                try:
                    if "leonardo.ai/api/auth/get-session" not in response.url: return
                    if response.status != 200: return
                    body = response.text()
                    if not body or body.strip() == 'null': return
                    import json as _json
                    data = _json.loads(body)
                    jwt = _find_jwt_in(data)
                    if jwt:
                        captured_jwt["value"] = jwt
                        log_step(f"JWT captured dari get-session intercept!")
                except Exception: pass
            # Di Camoufox persistent_context, 'browser' IS BrowserContext
            browser.on("response", handle_response)

            log_step("Membuka Leonardo AI — login page...")
            page.goto("https://app.leonardo.ai/auth/login", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            page.screenshot(path="leonardo_login_initial.png")

            # Setup popup listener SEBELUM klik (event-driven)
            canva_popup = {"page": None}
            def on_popup(popup_page):
                canva_popup["page"] = popup_page
                log_step(f"Popup terdeteksi: {popup_page.url[:60]}")
            browser.on("page", on_popup)

            log_step("Fokus & Enter pada tombol 'Canva'...")
            sels = [
                "button:has-text('Canva')", 
                "a:has-text('Canva')",
                "button[data-slot='button']:has-text('Canva')",
                "div[role='button']:has-text('Canva')"
            ]
            focused = False
            for sel in sels:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=3000):
                        loc.focus(timeout=3000)
                        page.keyboard.press("Enter")
                        focused = True
                        break
                except: continue
            
            if not focused:
                log_step("GAGAL: Tombol login Canva tidak bisa difokus!")
                page.screenshot(path="leonardo_login_failed_focus.png")
            
            time.sleep(5)
            page.screenshot(path="leonardo_after_click.png")
            log_step(f"Interaction selesai. URL sekarang: {page.url[:60]}")
            time.sleep(10)
            log_step(f"Pages: {[p.url[:50] for p in browser.pages]}")

            # Handle berdasarkan hasilnya
            auth_page = None
            if canva_popup["page"]:
                auth_page = canva_popup["page"]
                log_step(f"OAuth popup terdeteksi: {auth_page.url[:60]}")
                # Jika about:blank, tunggu navigasi
                if "about:blank" in auth_page.url:
                    try:
                        auth_page.wait_for_load_state("domcontentloaded", timeout=10000)
                        log_step(f"Popup url berubah: {auth_page.url[:60]}")
                    except: pass
                _click_canva_authorize_v2(auth_page, email)
                time.sleep(5)
            
            # 2. Cek apakah main page redirect (inline OAuth)
            elif "canva.com" in page.url:
                auth_page = page
                log_step(f"OAuth inline di main page: {page.url[:60]}")
                _click_canva_authorize_v2(page, email)
                time.sleep(5)

            # 3. Scan semua pages (termasuk yang mungkin baru terbuka tapi telat)
            else:
                log_step("Scan pages untuk Canva...")
                for pg in browser.pages:
                    if "canva.com" in pg.url and pg != page:
                        auth_page = pg
                        log_step(f"OAuth popup via scan: {pg.url[:60]}")
                        _click_canva_authorize_v2(pg, email)
                        time.sleep(5)
                        break
                else:
                    # Coba klik lagi dengan JS jika belum ada popup
                    log_step("Belum ada redirect. Mencoba klik via JS...")
                    page.evaluate("Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Canva'))?.click()")
                    time.sleep(10)
                    for pg in browser.pages:
                        if "canva.com" in pg.url and pg != page:
                            auth_page = pg
                            log_step(f"OAuth popup via scan (setelah JS click): {pg.url[:60]}")
                            _click_canva_authorize_v2(pg, email)
                            time.sleep(5)
                            break
                    else:
                        log_step(f"PERINGATAN: Tidak ada redirect ke Canva! URL={page.url}")

            # Tunggu Leonardo dashboard atau onboarding (max 90s)
            log_step("Menunggu redirect ke Leonardo dashboard/onboarding...")
            deadline_leo = time.time() + 90
            last_log_leo = 0
            canva_btn_retry = 0
            while time.time() < deadline_leo:
                cur = page.url.lower()

                # Inline OAuth redirect di main page — handle
                if "canva.com" in cur and page != auth_page:
                    log_step(f"Main page redirect ke Canva OAuth: {page.url[:60]}")
                    _click_canva_authorize_v2(page, email)
                    auth_page = page
                    time.sleep(5)
                    continue

                # Sukses: sudah di luar /auth/
                if "app.leonardo.ai" in cur and "/auth/" not in cur:
                    log_step(f"Leonardo post-auth page: {page.url[:60]}")

                    # Handle onboarding/survey pages — klik Continue/Skip
                    if any(x in cur for x in ["/onboarding", "/survey", "/welcome", "/get-started"]):
                        log_step("Onboarding page — cari tombol Continue/Skip...")
                        for sel in ["button:has-text('Continue')", "button:has-text('Skip')", "button:has-text('Get started')", "button:has-text('Next')", "button[type='submit']"]:
                            try:
                                loc = page.locator(sel).first
                                if loc.count() > 0 and loc.is_visible(timeout=1000):
                                    loc.click(timeout=3000)
                                    time.sleep(2)
                                    break
                            except Exception: pass
                        time.sleep(2)
                        # Cek apakah masih onboarding
                        if any(x in page.url.lower() for x in ["/onboarding", "/survey", "/welcome", "/get-started"]):
                            time.sleep(3)
                            continue
                    break
                # Cek popup auth Canva baru (deteksi broad: any canva.com page)
                for pg in browser.pages:
                    if "canva.com" in pg.url and pg != auth_page and pg != page:
                        log_step(f"OAuth popup baru: {pg.url[:60]}")
                        _click_canva_authorize_v2(pg, email)
                        auth_page = pg
                # Retry klik "Continue with Canva" jika stuck di /auth/login > 15s
                if "/auth/login" in cur and canva_btn_retry < 2:
                    elapsed_stuck = time.time() - (deadline_leo - 90)
                    if elapsed_stuck > 15 * (canva_btn_retry + 1):
                        log_step(f"Stuck di /auth/login, retry klik Continue with Canva ({canva_btn_retry+1}/2)...")
                        try:
                            page.goto("https://app.leonardo.ai/auth/login", wait_until="domcontentloaded", timeout=30000)
                            time.sleep(2)
                            click_first(page, ["button:has-text('Continue with Canva')"], timeout_ms=10000)
                            time.sleep(5)
                            # Cek popup setelah retry
                            for pg in browser.pages:
                                if "canva.com" in pg.url and pg != page:
                                    log_step(f"Popup setelah retry: {pg.url[:60]}")
                                    _click_canva_authorize_v2(pg, email)
                                    auth_page = pg
                                    time.sleep(5)
                                    break
                            else:
                                if "canva.com" in page.url:
                                    _click_canva_authorize_v2(page, email)
                                    auth_page = page
                                    time.sleep(5)
                        except Exception as _e:
                            log_step(f"Retry gagal: {_e}")
                        canva_btn_retry += 1
                # Heartbeat setiap 5 detik
                now = time.time()
                if now - last_log_leo >= 5:
                    short = page.url.replace("https://","").replace("www.","")[:50]
                    log_step(f"Menunggu Leonardo post-auth... [{short}]")
                    last_log_leo = now
                time.sleep(2)

            # Tunggu Leonardo load penuh (pastikan session tersimpan di storage)
            log_step(f"Leonardo page: {page.url[:60]}")
            log_step("Tunggu networkidle + session cookie set...")
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except: pass
            time.sleep(5)

            # Navigate ke /image-generation untuk force session cookie di-set
            log_step("Navigate ke image-generation untuk force session cookie...")
            try:
                page.goto("https://app.leonardo.ai/ai-generations", wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                page.wait_for_load_state("networkidle", timeout=10000)
            except: pass
            time.sleep(3)

            # Extract Leonardo cookies — pakai semua cookies dari context
            log_step("Mengekstrak cookies Leonardo...")
            all_cookies = page.context.cookies()
            log_step(f"Total cookies: {len(all_cookies)}")
            leo_cookies = [c for c in all_cookies if "leonardo.ai" in (c.get("domain") or "")]

            if not leo_cookies:
                leo_cookies = all_cookies
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in leo_cookies)

            # === LOGIKA DARI LEOAPI-MAIN (updated untuk better-auth) ===
            # Leonardo migrasi dari next-auth ke better-auth
            # Token ada di: __Secure-better-auth.session_data.* atau session_token
            SESSION_TOKEN_NAMES = [
                # better-auth (baru — Leonardo pakai ini sekarang)
                "__Secure-better-auth.session_data.0",
                "__Secure-better-auth.session_data.1",
                "better-auth.session_token",
                "__Secure-better-auth.session_token",
                # next-auth (lama — fallback)
                "__Secure-next-auth.session-token",
                "next-auth.session-token",
                "__Secure-authjs.session-token",
                "authjs.session-token",
            ]
            cookie_map = {}
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" in item:
                    k, v = item.split("=", 1)
                    cookie_map[k.strip()] = v.strip()

            # 1. Cek session-token langsung dari cookie
            session_token = ""
            for name in SESSION_TOKEN_NAMES:
                if name in cookie_map:
                    session_token = cookie_map[name]
                    log_step(f"Session token ditemukan di cookie: {name} (len={len(session_token)})")
                    break

            # 2. Jika tidak ada, coba POST /api/auth/session dengan CSRF
            if not session_token:
                log_step("Session token tidak ada di cookie, coba via /api/auth/session...")
                try:
                    CSRF_NAMES = [
                        "__Host-next-auth.csrf-token", "__Secure-next-auth.csrf-token",
                        "next-auth.csrf-token", "__Host-authjs.csrf-token",
                        "__Secure-authjs.csrf-token", "authjs.csrf-token"
                    ]
                    csrf_raw = ""
                    for n in CSRF_NAMES:
                        if n in cookie_map:
                            csrf_raw = cookie_map[n]; break
                    csrf = csrf_raw.split("|")[0] if "|" in csrf_raw else csrf_raw

                    import urllib.request as _ur, urllib.parse as _up
                    _headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Cookie": cookie_str, "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Origin": "https://app.leonardo.ai",
                        "Referer": "https://app.leonardo.ai/",
                    }
                    if csrf:
                        body = json.dumps({"csrfToken": csrf}).encode()
                        _req = _ur.Request("https://app.leonardo.ai/api/auth/session", data=body, headers=_headers, method="POST")
                    else:
                        _req = _ur.Request("https://app.leonardo.ai/api/auth/session", headers=_headers)
                    with _ur.urlopen(_req, timeout=15) as _resp:
                        _data = json.loads(_resp.read().decode())
                        # Cari JWT dari response
                        def _walk_jwt(obj, depth=0):
                            if depth > 6: return ""
                            if isinstance(obj, str) and obj.startswith("eyJ") and len(obj) > 100: return obj
                            if isinstance(obj, dict):
                                for k, v in obj.items():
                                    kl = k.lower()
                                    if "cf_access_token" in kl: continue
                                    if kl in {"idtoken","accesstoken","id_token","access_token","token"} or "token" in kl or isinstance(v,(dict,list)):
                                        r = _walk_jwt(v, depth+1)
                                        if r: return r
                            if isinstance(obj, list):
                                for v in obj:
                                    r = _walk_jwt(v, depth+1)
                                    if r: return r
                            return ""
                        session_token = _walk_jwt(_data)
                        if session_token:
                            log_step(f"JWT ditemukan via /api/auth/session (len={len(session_token)})")
                except Exception as e:
                    log_step(f"/api/auth/session gagal: {e}")

            # 3. Fallback: ambil dari intercept (captured dari response listener)
            if not session_token:
                session_token = captured_jwt.get("value", "")
                if session_token:
                    log_step(f"Pakai JWT dari intercept (len={len(session_token)})")

            if not session_token:
                log_step("GAGAL: next-auth.session-token tidak ditemukan di semua metode!")
                log_step(f"Cookies tersedia: {list(cookie_map.keys())[:10]}")

            if not cookie_str and not session_token:
                sys.stdout.write(json.dumps({"status": "error", "message": "Gagal extract session Leonardo"}) + "\n")
                sys.stdout.flush()
                return False

            sys.stdout.write(json.dumps({
                "status": "success",
                "cookie": cookie_str,
                "jwt": session_token,
                "balance": 150,
                "left_team": False,
            }) + "\n")
            sys.stdout.flush()
            return True

    finally:
        try:
            asyncio.set_event_loop(None)
            if hasattr(asyncio, "events") and hasattr(asyncio.events, "_set_running_loop"):
                asyncio.events._set_running_loop(None)
        except Exception: pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    # Node kirim --invite-link (hyphen), dulu kita pakai --invite_link
    parser.add_argument("--invite-link", "--invite_link", dest="invite_link", default="")
    parser.add_argument("--profiles-dir", "--profiles_dir", dest="profiles_dir", default="")
    parser.add_argument("--signup-method", "--signup_method", dest="signup_method", default="email")
    parser.add_argument("--skip-canva", "--skip_canva", dest="skip_canva", action="store_true")
    parser.add_argument("--canva-delay", "--canva_delay", dest="canva_delay", type=int, default=0)
    parser.add_argument("--canva-headless", "--canva_headless", dest="canva_headless", action="store_true")
    parser.add_argument("--leave-canva-team", "--leave_canva_team", dest="leave_canva_team", action="store_true")
    parser.add_argument("--headless", action="store_true")
    # Proxy args dari Node
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--proxy-server", "--proxy_server", dest="proxy_server", default=None)
    parser.add_argument("--proxy-user", "--proxy_user", dest="proxy_user", default=None)
    parser.add_argument("--proxy-pass", "--proxy_pass", dest="proxy_pass", default=None)
    args = parser.parse_args()

    # Build proxy string dari proxy-server/user/pass jika ada
    proxy = args.proxy
    if not proxy and args.proxy_server:
        if args.proxy_user and args.proxy_pass:
            from urllib.parse import urlparse as _up
            p = _up(args.proxy_server)
            proxy = f"{p.scheme}://{args.proxy_user}:{args.proxy_pass}@{p.hostname}:{p.port}"
        else:
            proxy = args.proxy_server

    if args.canva_delay > 0:
        import random
        delay = random.randint(1, args.canva_delay)
        log_step(f"Pre-Canva delay {delay}s...")
        time.sleep(delay)

    try:
        success = run_automation(args.email, args.password, args.invite_link, proxy, args.headless, skip_canva=args.skip_canva)
        if not success:
            # Emit status:error agar Node handler mark sebagai failed (bukan hanya exit code 0)
            sys.stdout.write(json.dumps({"status": "error", "message": "Automation gagal tanpa detail — cek log"}) + "\n")
            sys.stdout.flush()
    except Exception as e:
        log_step(f"ERROR: {str(e)}")
        import traceback
        sys.stderr.write(traceback.format_exc())
        sys.stdout.write(json.dumps({"status": "error", "message": str(e)}) + "\n")
        sys.stdout.flush()
