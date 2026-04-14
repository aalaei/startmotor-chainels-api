#!/usr/bin/env python3
"""Run this when cookies expire: python refresh_token.py"""
import os, math, random
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

def human_move_and_click(page, selector):
    """Move mouse in a natural curved path to element, then click."""
    el = page.query_selector(selector)
    if not el:
        return False
    box = el.bounding_box()
    if not box:
        return False

    target_x = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
    target_y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)

    # Start from a random nearby position
    start_x = target_x + random.uniform(-200, 200)
    start_y = target_y + random.uniform(-150, 150)

    # Bezier control points for a natural curve
    cp1_x = start_x + (target_x - start_x) * random.uniform(0.2, 0.4) + random.uniform(-40, 40)
    cp1_y = start_y + (target_y - start_y) * random.uniform(0.1, 0.3) + random.uniform(-40, 40)
    cp2_x = start_x + (target_x - start_x) * random.uniform(0.6, 0.8) + random.uniform(-20, 20)
    cp2_y = start_y + (target_y - start_y) * random.uniform(0.6, 0.9) + random.uniform(-20, 20)

    steps = random.randint(25, 40)
    page.mouse.move(start_x, start_y)

    for i in range(1, steps + 1):
        t = i / steps
        # Cubic bezier
        x = ((1-t)**3 * start_x + 3*(1-t)**2*t * cp1_x +
             3*(1-t)*t**2 * cp2_x + t**3 * target_x)
        y = ((1-t)**3 * start_y + 3*(1-t)**2*t * cp1_y +
             3*(1-t)*t**2 * cp2_y + t**3 * target_y)
        page.mouse.move(x, y)
        page.wait_for_timeout(random.randint(10, 30))

    page.wait_for_timeout(random.randint(80, 180))
    page.mouse.click(target_x, target_y)
    return True

def main():
    env = load_env()
    email = env.get("CHAINELS_EMAIL") or os.environ.get("CHAINELS_EMAIL", "")
    password = env.get("CHAINELS_PASSWORD") or os.environ.get("CHAINELS_PASSWORD", "")

    with sync_playwright() as p:
        headless = not os.environ.get("DISPLAY")
        browser = p.chromium.launch(
            headless=headless,
            slow_mo=300,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = context.new_page()

        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

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

        print("Waiting for login...")
        # Wait for the page to settle after submit
        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(1000)

        # If still on login page — something went wrong, move to button and click
        if "/login" in page.url:
            print("Login failed, retrying with human-like click...")
            page.wait_for_timeout(800)
            human_move_and_click(page, "button[type=submit], input[type=submit]")

        # Now wait for actual success
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
