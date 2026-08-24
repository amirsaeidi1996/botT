import os
import random
import time
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def log(message):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), message, flush=True)


def load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def jitter(cfg):
    lo = float(cfg.get("min_delay_seconds", 2.5))
    hi = float(cfg.get("max_delay_seconds", 6.5))
    time.sleep(random.uniform(min(lo, hi), max(lo, hi)))


def main():
    server = os.getenv("TRAVIAN_SERVER", "").strip().rstrip("/")
    username = os.getenv("TRAVIAN_USERNAME", "").strip()
    password = os.getenv("TRAVIAN_PASSWORD", "")
    cfg = load_config()

    if not server or not username or not password:
        raise RuntimeError("Save TRAVIAN_SERVER, TRAVIAN_USERNAME and TRAVIAN_PASSWORD in the dashboard first.")

    log("Starting browser.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(server, wait_until="domcontentloaded", timeout=60000)
            log(f"Opened {server}")

            # This build intentionally limits itself to opening the game world
            # and reporting page state. Site-specific automated gameplay actions
            # should only be added where permitted by the game's rules.
            log(f"Page title: {page.title()}")
            log("Browser connection is working.")

            cycle = float(cfg.get("cycle_minutes", 10))
            while cycle > 0:
                jitter(cfg)
                page.reload(wait_until="domcontentloaded", timeout=60000)
                log(f"Heartbeat OK — {page.title()}")
                time.sleep(max(30, cycle * 60))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
