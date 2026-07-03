#!/usr/bin/env python3
"""Diagnostic v2: Dump ALL inputs + body AFTER email submitted, wait longer."""
import time, json, sys, asyncio
from pathlib import Path

def run():
    import asyncio
    from camoufox.sync_api import Camoufox
    try:
        asyncio.set_event_loop(None)
        if hasattr(asyncio, "events") and hasattr(asyncio.events, "_set_running_loop"):
            asyncio.events._set_running_loop(None)
    except Exception: pass

    email = sys.argv[1] if len(sys.argv) > 1 else "diag2@amstream.pro"
    invite = "https://www.canva.com/brand/join?token=Vsbi43rYlmplurmU63xbgw&referrer=team-invite"
    profile_dir = Path(f"profiles/diag2_{email.split('@')[0]}")
    profile_dir.mkdir(parents=True, exist_ok=True)

    kwargs = dict(headless=True, persistent_context=True, user_data_dir=str(profile_dir),
                  humanize=True, geoip=True, locale="en-US", os=("windows","macos","linux"))
    ctx = None
    try: ctx = Camoufox(**kwargs)
    except TypeError:
        for drop in ("os","geoip","humanize","locale"):
            kwargs.pop(drop, None)
            try: ctx = Camoufox(**kwargs); break
            except TypeError: continue

    with ctx as browser:
        page = browser.new_page()
        print("Opening invite link...")
        page.goto(invite, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

        # Accept cookies
        for s in ["button:has-text('Accept all cookies')", "button:has-text('Accept')"]:
            try:
                loc = page.locator(s).first
                if loc.count() > 0 and loc.is_visible(timeout=1000): loc.click(); break
            except: pass

        # Click "Continue with email" with force
        email_btn_selectors = [
            "button[aria-label='Continue with email']",
            "button[aria-label='Sign up with email']",
            "button:has-text('Continue with email')",
            "button:has-text('Use email')",
        ]
        email_btn = None
        deadline_btn = time.time() + 20
        while time.time() < deadline_btn and email_btn is None:
            for sel in email_btn_selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=400):
                        email_btn = loc
                        print(f"Found email button: {sel}")
                        break
                except: continue
            if email_btn is None: time.sleep(0.5)

        if email_btn:
            email_btn.click(force=True, timeout=5000)
            print("Email button clicked!")
        else:
            print("NO EMAIL BUTTON FOUND!")

        # Fill email
        email_input_selectors = [
            "input[name='username'][inputmode='email']",
            "input[autocomplete='username'][inputmode='email']",
            "input[type='email']",
            "input[name='email']",
            "input[inputmode='email']",
        ]
        email_input = None
        deadline_in = time.time() + 15
        while time.time() < deadline_in and email_input is None:
            for sel in email_input_selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=400):
                        email_input = loc
                        print(f"Found email input: {sel}")
                        break
                except: continue
            if email_input is None: time.sleep(0.5)

        if email_input:
            email_input.fill(email)
            print(f"Email filled: {email}")
        else:
            print("NO EMAIL INPUT FOUND!")

        # Submit
        for s in ["button[type='submit']", "button:has-text('Continue')"]:
            try:
                loc = page.locator(s).first
                if loc.count() > 0 and loc.is_visible(timeout=3000):
                    loc.click(); print(f"Clicked: {s}"); break
            except: pass

        # Wait 8 seconds for OTP screen
        print("\nWaiting 8s for OTP screen...")
        time.sleep(8)

        # Dump state
        print(f"\n=== URL after submit: {page.url} ===")
        print("\n=== ALL INPUTS (visible + hidden) ===")
        inputs = page.locator("input").all()
        for inp in inputs:
            try:
                attrs = {
                    "type": inp.get_attribute("type"),
                    "name": inp.get_attribute("name"),
                    "autocomplete": inp.get_attribute("autocomplete"),
                    "maxlength": inp.get_attribute("maxlength"),
                    "inputmode": inp.get_attribute("inputmode"),
                    "aria-label": inp.get_attribute("aria-label"),
                    "data-testid": inp.get_attribute("data-testid"),
                    "id": inp.get_attribute("id"),
                    "class": inp.get_attribute("class"),
                    "visible": inp.is_visible(timeout=200),
                }
                print(json.dumps(attrs))
            except: pass

        print(f"\n=== BODY TEXT (first 800 chars) ===")
        try: print(page.inner_text("body", timeout=2000)[:800])
        except: pass

        page.screenshot(path="profiles/canva_diag2.png")
        print(f"\nScreenshot: profiles/canva_diag2.png")

if __name__ == "__main__":
    run()
