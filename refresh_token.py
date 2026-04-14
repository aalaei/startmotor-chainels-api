#!/usr/bin/env python3
"""Run this when cookies expire: python refresh_token.py"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ENV_FILE = Path(__file__).parent / ".env"

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() and not k.strip().startswith("#"):
                env[k.strip()] = v.strip().strip("'\"")
    return env

def save_env(updates: dict):
    env = load_env()
    env.update(updates)
    ENV_FILE.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
    print(f"Saved to .env: {list(updates.keys())}")

def main():
    env = load_env()
    email = env.get("CHAINELS_EMAIL") or os.environ.get("CHAINELS_EMAIL", "")
    password = env.get("CHAINELS_PASSWORD") or os.environ.get("CHAINELS_PASSWORD", "")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context()
        page = context.new_page()

        print("Opening login page...")
        page.goto("https://startmotor.chainels.com/login")
        page.wait_for_load_state("networkidle")

        email_input = page.wait_for_selector("input[type=email], input[name=email]", timeout=15000)
        page.wait_for_timeout(1000)
        email_input.click()
        email_input.fill(email)
        page.wait_for_timeout(800)
        page.keyboard.press("Enter")

        pwd_input = page.wait_for_selector("input[type=password], input[name=password]", timeout=15000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        pwd_input.click()
        pwd_input.fill(password)
        page.wait_for_timeout(800)
        page.keyboard.press("Enter")

        print("Waiting for login... (complete any captcha in the browser window)")
        page.wait_for_url(lambda url: "/login" not in url, timeout=120000)
        print("Logged in! Capturing cookies...")

        page.goto("https://startmotor.chainels.com")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        all_cookies = {c["name"]: c["value"] for c in context.cookies()}
        browser.close()

    auth = all_cookies.get("chainels_prod_auth")
    ssid = all_cookies.get("chainels_prod_ssid")
    if not auth or not ssid:
        print("ERROR: Could not capture auth cookies.")
        return
    updates = {
        "CHAINELS_COOKIE_CHAINELS_PROD_AUTH": auth,
        "CHAINELS_COOKIE_CHAINELS_PROD_SSID": ssid,
    }

    save_env(updates)
    print("Done! Run python digest.py")

if __name__ == "__main__":
    main()
