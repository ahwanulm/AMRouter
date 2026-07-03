#!/usr/bin/env python3
"""Standalone script to automate CodeBuddy signup & API key generation using Playwright/Camoufox.
Outputs step logs and final results to stdout as JSON lines.
"""

import sys
import json
import argparse
import time
import logging
import re
from pathlib import Path

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

# Setup simple stdout logger to not conflict with JSON line prints
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("codebuddy_signup")

CODEBUDDY_LOGIN_URL = "https://www.codebuddy.ai/login?redirect_uri=https://www.codebuddy.ai/home"
CODEBUDDY_API_KEYS_ENDPOINT = "https://www.codebuddy.ai/console/api/client/v1/api-keys"

GOOGLE_EMAIL_SELECTORS = [
    "input[type='email']",
    "input[name='identifier']",
    "input#identifierId",
]
GOOGLE_PASSWORD_SELECTORS = [
    "input[type='password'][name='Passwd']",
    "input[type='password']",
    "input[name='password']",
]
GOOGLE_NEXT_SELECTORS = [
    "#identifierNext button",
    "#passwordNext button",
    "button[jsname='LgbsSe']",
    "div[role='button']:has-text('Next')",
    "button:has-text('Next')",
    "button:has-text('Berikutnya')",
]

class CodeBuddyAutomationError(RuntimeError):
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

def get_proxy_country(proxy: dict = None) -> str:
    import requests
    url = "http://ip-api.com/json/"
    proxies = None
    if proxy and proxy.get("server"):
        server = proxy["server"]
        username = proxy.get("username")
        password = proxy.get("password")
        if username and password:
            if "://" in server:
                proto, host = server.split("://", 1)
                proxy_url = f"{proto}://{username}:{password}@{host}"
            else:
                proxy_url = f"http://{username}:{password}@{server}"
        else:
            proxy_url = server
        proxies = {
            "http": proxy_url,
            "https": proxy_url
        }
    
    for attempt in range(2):
        try:
            response = requests.get(url, proxies=proxies, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success" and data.get("country"):
                    return data["country"]
        except Exception:
            pass
            
        try:
            fallback_url = "https://ipapi.co/json/"
            response = requests.get(fallback_url, proxies=proxies, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("country_name"):
                    return data["country_name"]
        except Exception:
            pass
            
    return "United States"

def test_api_key(api_key: str, proxy: dict = None) -> bool:
    url = "https://www.codebuddy.ai/v2/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": "default-model",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "ping"}
        ],
        "stream": True
    }
    
    proxies = None
    if proxy and proxy.get("server"):
        server = proxy["server"]
        username = proxy.get("username")
        password = proxy.get("password")
        if username and password:
            if "://" in server:
                proto, host = server.split("://", 1)
                proxy_url = f"{proto}://{username}:{password}@{host}"
            else:
                proxy_url = f"http://{username}:{password}@{server}"
        else:
            proxy_url = server
        proxies = {"http": proxy_url, "https": proxy_url}
        
    import requests
    try:
        r = requests.post(url, headers=headers, json=payload, proxies=proxies, timeout=10, stream=True)
        if r.status_code == 200:
            try:
                r.close()
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False

def click_first(page, selectors, timeout_ms: int = 5000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=400):
                    try:
                        loc.click(timeout=1500)
                        return True
                    except Exception:
                        pass
                    try:
                        handle = loc.element_handle(timeout=500)
                        if handle:
                            page.evaluate("(el) => el.click()", handle)
                            return True
                    except Exception:
                        pass
            except Exception:
                continue
        time.sleep(0.3)
    return False

def fill_first(page, selectors, value: str, timeout_ms: int = 5000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=400):
                    loc.fill(value, timeout=1500)
                    return True
            except Exception:
                continue
        time.sleep(0.3)
    return False

def do_google_login(page, email: str, password: str) -> bool:
    log_step("Menunggu form email Google...")
    if not fill_first(page, GOOGLE_EMAIL_SELECTORS, email, timeout_ms=10000):
        log_step("Form email tidak ditemukan, cek apakah diminta pilih akun.")
        try:
            account_sel = f"div[data-identifier='{email.lower()}']"
            if page.locator(account_sel).count() > 0:
                page.locator(account_sel).click()
                log_step(f"Memilih akun {email}")
        except Exception:
            pass
    else:
        click_first(page, GOOGLE_NEXT_SELECTORS, timeout_ms=3000)

    log_step("Menunggu form password Google...")
    if fill_first(page, GOOGLE_PASSWORD_SELECTORS, password, timeout_ms=10000):
        click_first(page, GOOGLE_NEXT_SELECTORS, timeout_ms=3000)
        log_step("Password diisi dan disubmit.")
        return True

    time.sleep(2)
    if "codebuddy.ai" in page.url.lower():
        log_step("Berhasil auth, redirect ke CodeBuddy.")
        return True

    return False

def perform_login_page_actions(page, email: str) -> None:
    iframe_selector = "iframe[title='login-iframe']"
    try:
        page.wait_for_selector(iframe_selector, timeout=15000)
        for _ in range(30):
            exists = page.evaluate(f"""() => {{
                const iframe = document.querySelector("{iframe_selector}");
                if (iframe && iframe.contentDocument) {{
                    const doc = iframe.contentDocument;
                    return !!(doc.querySelector("#agree-policy-account") || doc.querySelector("input[type='checkbox']"));
                }}
                return false;
            }}""")
            if exists:
                break
            time.sleep(0.5)
    except Exception:
        pass

    iframe = page.frame_locator(iframe_selector)

    log_step("Centang checkbox persetujuan...")
    try:
        page.evaluate(f"""() => {{
            const iframe = document.querySelector("{iframe_selector}");
            if (iframe && iframe.contentDocument) {{
                const doc = iframe.contentDocument;
                const checkbox = doc.querySelector("#agree-policy-account") || doc.querySelector("input[type='checkbox']");
                if (checkbox) {{
                    checkbox.checked = true;
                    checkbox.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    checkbox.dispatchEvent(new Event('click', {{ bubbles: true }}));
                    return true;
                }}
            }}
            return false;
        }}""")
    except Exception:
        pass

    time.sleep(2)

    log_step("Klik Sign up with Google...")
    google_selector = "#social-google, a[href*='google/login'], a:has-text('Google')"
    clicked_google = False
    try:
        loc = iframe.locator(google_selector).first
        if loc.count() > 0:
            loc.click(timeout=10000, force=True)
            clicked_google = True
    except Exception:
        pass

    if not clicked_google:
        try:
            page.evaluate(f"""() => {{
                const iframe = document.querySelector("{iframe_selector}");
                if (iframe && iframe.contentDocument) {{
                    const doc = iframe.contentDocument;
                    const el = doc.querySelector("#social-google") || 
                               doc.querySelector("a[href*='google/login']") ||
                               Array.from(doc.querySelectorAll("button, a, div[role='button']"))
                                    .find(e => (e.innerText || '').includes("Google") || (e.textContent || '').includes("Google"));
                    if (el) {{
                        el.click();
                        return true;
                    }}
                }}
                return false;
            }}""")
        except Exception:
            pass

    time.sleep(2)

    try:
        confirm_btn = page.locator("button:has-text('Confirm')").first
        if confirm_btn.is_visible(timeout=3000):
            log_step("Klik Confirm pada popup Service Agreement...")
            confirm_btn.click(timeout=3000, force=True)
            time.sleep(2)
        else:
            confirm_btn2 = iframe.locator("button:has-text('Confirm')").first
            if confirm_btn2.is_visible(timeout=2000):
                confirm_btn2.click(timeout=3000, force=True)
                time.sleep(2)
    except Exception:
        pass

def safe_evaluate(page, js_code: str, arg=None, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            if arg is not None:
                return page.evaluate(js_code, arg)
            else:
                return page.evaluate(js_code)
        except Exception as e:
            err_msg = str(e).lower()
            if "execution context was destroyed" in err_msg or "context or browser has been closed" in err_msg:
                if attempt < max_retries - 1:
                    time.sleep(3)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    continue
            raise
    raise CodeBuddyAutomationError("Failed to evaluate JS after context destroyed retries")

def main():
    parser = argparse.ArgumentParser(description="CodeBuddy auto-signup tool")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--proxy-server")
    parser.add_argument("--proxy-user")
    parser.add_argument("--proxy-pass")
    parser.add_argument("--profiles-dir", required=True)
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

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
        sys.stdout.write(json.dumps({"status": "error", "message": "Camoufox package not installed in python environment."}) + "\n")
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
    ctx_manager = None
    try:
        try:
            ctx_manager = Camoufox(**kwargs)
        except TypeError:
            for drop in ("window", "os", "geoip", "humanize", "locale"):
                kwargs.pop(drop, None)
                try:
                    ctx_manager = Camoufox(**kwargs)
                    break
                except TypeError:
                    continue
            if not ctx_manager:
                ctx_manager = Camoufox(**kwargs)

        with ctx_manager as browser:
            page = browser.new_page()

            log_step("Membuka halaman login CodeBuddy...")
            page.goto(CODEBUDDY_LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3)

            if "codebuddy.ai" in page.url.lower() and "/login" in page.url.lower():
                perform_login_page_actions(page, args.email)
                try:
                    page.wait_for_url(lambda url: "accounts.google.com" in url or ("codebuddy.ai" in url and "/login" not in url), timeout=20000)
                except Exception:
                    pass

            if "accounts.google.com" in page.url.lower():
                do_google_login(page, args.email, args.password)
                try:
                    page.wait_for_url("**/codebuddy.ai/**", timeout=30000)
                except Exception:
                    pass

            max_google_loops = 10
            for loop_i in range(max_google_loops):
                cur_url = page.url.lower()
                if "accounts.google.com" not in cur_url and "myaccount.google.com" not in cur_url:
                    break

                log_step(f"Halaman interstitial Google: {page.url[:80]}...")
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                clicked = click_first(page, [
                    "button:has-text('Saya mengerti')",
                    "a:has-text('Saya mengerti')",
                    "button:has-text('I understand')",
                    "button:has-text('I Understand')",
                    "button:has-text('Continue')",
                    "button:has-text('Allow')",
                    "button:has-text('Accept')",
                    "button:has-text('Agree')",
                    "button:has-text('I agree')",
                    "button:has-text('Next')",
                    ".VfPpkd-LgbsSe[data-mdc-ripple-is-unbounded]",
                    "button[jsname='LgbsSe']",
                    "input[type='submit']",
                    "button[type='submit']",
                ], timeout_ms=4000)
                
                if clicked:
                    log_step("Mengklik persetujuan Google, menunggu redirect...")
                    time.sleep(3)
                else:
                    try:
                        continue_url = page.evaluate("""() => {
                            const url = new URL(window.location.href);
                            return url.searchParams.get('continue') || '';
                        }""")
                        if continue_url and "google.com" in continue_url:
                            page.goto(continue_url, wait_until="domcontentloaded", timeout=15000)
                            time.sleep(3)
                        else:
                            try:
                                js_clicked = page.evaluate("""() => {
                                    const btns = Array.from(document.querySelectorAll('button,input[type=submit]'));
                                    const target = btns.find(b => {
                                        const t = (b.innerText || b.value || '').trim();
                                        return t === 'Continue' || t === 'Allow' || t === 'Saya mengerti' || t === 'I understand' || t === 'Accept';
                                    });
                                    if (target) { target.click(); return true; }
                                    return false;
                                }""")
                                if js_clicked:
                                    time.sleep(3)
                                    continue
                            except Exception:
                                pass
                            time.sleep(3)
                    except Exception:
                        time.sleep(3)

            if "no-permission" in page.url.lower() or "login" in page.url.lower():
                log_step("Navigasi ke /register/user/complete...")
                page.goto("https://www.codebuddy.ai/register/user/complete", wait_until="domcontentloaded", timeout=20000)
                time.sleep(3)

            if "register" in page.url.lower():
                country = get_proxy_country(proxy_dict)
                log_step(f"Mengisi lokasi registrasi ({country})...")
                try:
                    page.evaluate(f"""() => {{
                        let inputs = Array.from(document.querySelectorAll('input'));
                        for (let input of inputs) {{
                            if (input.placeholder && input.placeholder.toLowerCase().includes('location')) {{
                                input.value = '{country}';
                                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                        }}
                    }}""")
                    time.sleep(1)
                    click_first(page, [
                        "button:has-text('Submit')",
                        "button:has-text('Complete')",
                        "button:has-text('Save')",
                        "button:has-text('Continue')"
                    ])
                    time.sleep(5)
                except Exception:
                    pass

            log_step("Menunggu aktivasi trial otomatis...")
            try:
                with page.expect_response(lambda r: "billing/ide/trial" in r.url, timeout=15000):
                    pass
                time.sleep(2)
            except Exception:
                pass

            if "home" not in page.url.lower() and "ide" not in page.url.lower() and "profile" not in page.url.lower():
                page.goto("https://www.codebuddy.ai/home", wait_until="domcontentloaded", timeout=15000)
                time.sleep(4)

            log_step("Mengaktifkan trial IDE secara manual...")
            try:
                trial_res = safe_evaluate(page, """async () => {
                    try {
                        const res = await fetch("https://www.codebuddy.ai/billing/ide/trial", {
                            method: "POST",
                            headers: {
                                "Accept": "application/json, text/plain, */*",
                                "x-requested-with": "XMLHttpRequest"
                            }
                        });
                        const text = await res.text();
                        try { return JSON.parse(text); } catch(e) { return {raw: text, status: res.status}; }
                    } catch(e) {
                        return {error: e.toString()};
                    }
                }""")
                time.sleep(2)
            except Exception:
                pass

            log_step("Memanggil API internal generate API Key...")
            unique_key_name = f"AutoKey_{int(time.time())}"
            api_payload = {
                "name": unique_key_name,
                "expire_in_days": 365,
                "user_enterprise_id": "personal-edition-user-id"
            }

            result = safe_evaluate(page, f"""async (payload) => {{
                try {{
                    const res = await fetch("{CODEBUDDY_API_KEYS_ENDPOINT}", {{
                        method: "POST",
                        headers: {{
                            "Content-Type": "application/json",
                            "Accept": "application/json"
                        }},
                        body: JSON.stringify(payload)
                    }});
                    const text = await res.text();
                    let data = null;
                    try {{ data = JSON.parse(text); }} catch(e) {{}}
                    return {{ok: res.ok, status: res.status, data: data}};
                }} catch(e) {{
                    return {{ok: false, error: e.toString()}};
                }}
            }}""", api_payload)

            api_key = ""
            if result and result.get("ok") and result.get("data"):
                data = result["data"]
                if isinstance(data, dict):
                    inner = data.get("data") or {}
                    api_key = inner.get("key") or inner.get("api_key") or data.get("key") or data.get("api_key") or ""

            if not api_key and result and result.get("data") and isinstance(result["data"], dict):
                err_code = result["data"].get("code")
                if err_code == 12502:
                    log_step("Nama key konflik, mencoba generate dengan nama lain...")
                    retry_name = f"AutoKey_{int(time.time())}_r"
                    retry_payload = dict(api_payload, name=retry_name)
                    try:
                        retry_result = safe_evaluate(page, f"""async (payload) => {{
                            const res = await fetch("{CODEBUDDY_API_KEYS_ENDPOINT}", {{
                                method: "POST",
                                headers: {{"Content-Type": "application/json", "Accept": "application/json"}},
                                body: JSON.stringify(payload)
                            }});
                            const text = await res.text();
                            let data = null;
                            try {{ data = JSON.parse(text); }} catch(e) {{}}
                            return {{ok: res.ok, status: res.status, data: data}};
                        }}""", retry_payload)
                        if retry_result and retry_result.get("ok") and retry_result.get("data"):
                            inner2 = (retry_result["data"].get("data") or {})
                            api_key = inner2.get("key") or inner2.get("api_key") or ""
                    except Exception:
                        pass

            if not api_key:
                log_step("Mencari API Key yang sudah ada di list...")
                list_result = safe_evaluate(page, f"""async () => {{
                    const res = await fetch("{CODEBUDDY_API_KEYS_ENDPOINT}?page=1&page_size=10&user_enterprise_id=personal-edition-user-id");
                    const text = await res.text();
                    let data = null;
                    try {{ data = JSON.parse(text); }} catch(e) {{}}
                    return {{ok: res.ok, status: res.status, data: data}};
                }}""")
                
                if list_result and list_result.get("data") and isinstance(list_result["data"], dict):
                    inner = list_result["data"].get("data") or {}
                    items = inner.get("items") or inner.get("list") or []
                    if items:
                        masked = items[0].get("masked_key", "")
                        sys.stdout.write(json.dumps({"status": "error", "message": f"Akun sudah memiliki API key ({masked}), tetapi hanya masked key yang tersedia."}) + "\n")
                        sys.exit(1)

            if api_key:
                log_step("Menguji validitas API Key...")
                if test_api_key(api_key, proxy_dict):
                    log_step("API Key valid dan siap digunakan!")
                    sys.stdout.write(json.dumps({"status": "success", "api_key": api_key}) + "\n")
                    sys.exit(0)
                else:
                    sys.stdout.write(json.dumps({"status": "error", "message": "API Key berhasil digenerate tetapi gagal tes fungsionalitas chat."}) + "\n")
                    sys.exit(1)
            else:
                sys.stdout.write(json.dumps({"status": "error", "message": "Gagal generate API Key dari CodeBuddy."}) + "\n")
                sys.exit(1)

    except Exception as e:
        sys.stdout.write(json.dumps({"status": "error", "message": str(e)}) + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
