import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import yaml
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), msg, flush=True)


def cfg():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def normalize_server(value):
    value = value.strip().rstrip("/")
    if not value.startswith("http://") and not value.startswith("https://"):
        value = "https://" + value
    return value


def page_url(server, path):
    return urljoin(server + "/", path.lstrip("/"))


def first_visible(page, selectors):
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            pass
    return None


def logged_in(page):
    url = page.url.lower()
    if "dorf1.php" in url or "dorf2.php" in url:
        return True
    try:
        return page.locator("a[href*='logout']").count() > 0
    except Exception:
        return False


def login(page, server, username, password):
    page.goto(server, wait_until="domcontentloaded", timeout=60000)
    if logged_in(page):
        log("Existing Travian session is already authenticated.")
        return True

    user = first_visible(page, [
        "input[name='name']",
        "input[name='username']",
        "input[type='email']",
        "input[name='email']",
    ])
    pwd = first_visible(page, [
        "input[name='password']",
        "input[type='password']",
    ])

    if user is None or pwd is None:
        log("Could not find Travian login fields.")
        return False

    user.fill(username)
    pwd.fill(password)

    submit = first_visible(page, [
        "button[type='submit']",
        "button:has-text('Login')",
        "button:has-text('Log in')",
        "input[type='submit']",
    ])
    if submit is None:
        log("Could not find login button.")
        return False

    submit.click()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        pass

    if not logged_in(page):
        try:
            page.goto(page_url(server, "dorf1.php"), wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

    ok = logged_in(page)
    log("Login successful." if ok else "Login did not complete.")
    return ok


def switch_village(page, server, village_id):
    if not village_id:
        return
    url = page_url(server, f"dorf1.php?newdid={int(village_id)}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    log(f"Selected village {village_id}.")


def construction_queue_busy(page, server):
    try:
        page.goto(page_url(server, "dorf1.php"), wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return False

    selectors = [
        ".buildingList li",
        ".buildingList .buildDuration",
        ".buildingList .name",
        "#buildingContract",
        ".constructionList li",
        ".buildingQueue li",
    ]
    for sel in selectors:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False


def parse_level(page):
    selectors = [
        ".level",
        ".buildingLevel",
        ".titleInHeader .level",
        "h1",
        "h2",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if not loc.count():
                continue
            text = loc.inner_text().strip()
            m = re.search(r"(?:level|lvl\.?)\s*(\d+)", text, re.I)
            if m:
                return int(m.group(1))
        except Exception:
            pass

    try:
        body = page.locator("body").inner_text()
        m = re.search(r"(?:level|lvl\.?)\s*(\d+)", body, re.I)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def upgrade_button(page):
    selectors = [
        "button.green.build",
        "button.textButtonV1.green.build",
        "button[class*='green'][class*='build']",
        "a.green.build",
        ".upgradeButtonsContainer button.green",
        ".contractLink button.green",
        "button:has-text('Upgrade')",
        "button:has-text('Build')",
    ]
    return first_visible(page, selectors)


def upgrade_slot(page, server, slot_id, desired_level, dry_run=False):
    url = page_url(server, f"build.php?id={int(slot_id)}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)

    current = parse_level(page)
    if current is not None:
        log(f"Slot {slot_id}: current level {current}, target {desired_level}.")
        if current >= desired_level:
            return "complete"

    button = upgrade_button(page)
    if button is None:
        text = ""
        try:
            text = page.locator("body").inner_text()[:500]
        except Exception:
            pass
        log(f"Slot {slot_id}: no usable upgrade/build button found.")
        if "not enough" in text.lower() or "resources" in text.lower():
            return "resources"
        return "unavailable"

    if dry_run:
        log(f"DRY RUN: would upgrade slot {slot_id}.")
        return "dry-run"

    try:
        button.click()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        except PlaywrightTimeoutError:
            pass
        log(f"Upgrade submitted for slot {slot_id}.")
        return "submitted"
    except Exception as e:
        log(f"Slot {slot_id}: upgrade click failed: {e}")
        return "error"


def process_build_queue(page, server, config):
    if not config.get("features", {}).get("build_queue", False):
        return

    queue = config.get("build_queue", [])
    if not queue:
        log("Build Queue is enabled but empty.")
        return

    if construction_queue_busy(page, server):
        log("Construction queue is busy; skipping new build this cycle.")
        return

    for item in queue:
        try:
            slot_id = int(item.get("target"))
            target = int(item.get("desired_level"))
        except Exception:
            continue

        result = upgrade_slot(
            page,
            server,
            slot_id,
            target,
            dry_run=bool(config.get("dry_run", True)),
        )
        if result == "complete":
            continue

        if result in {"submitted", "dry-run"} and config.get("one_build_per_cycle", True):
            return

        if result in {"resources", "unavailable", "error"}:
            continue


def farm_page(page, server):
    candidates = [
        "build.php?gid=16&tt=99",
        "build.php?gid=16&tt=99&action=showSlot",
        "build.php?gid=16",
    ]
    for path in candidates:
        try:
            page.goto(page_url(server, path), wait_until="domcontentloaded", timeout=30000)
            if page.locator("body").count():
                return True
        except Exception:
            pass
    return False


def send_specific_farm_list(page, list_id, dry_run=False):
    list_id = str(list_id)
    containers = [
        f"[data-list-id='{list_id}']",
        f"[data-listid='{list_id}']",
        f"#list{list_id}",
        f"#raidList{list_id}",
        f"[id*='{list_id}']",
    ]

    container = None
    for sel in containers:
        try:
            loc = page.locator(sel).first
            if loc.count():
                container = loc
                break
        except Exception:
            pass

    button_selectors = [
        "button:has-text('Start')",
        "button:has-text('Send')",
        "button.green",
        "input[type='submit']",
    ]

    if container is not None:
        for bs in button_selectors:
            try:
                btn = container.locator(bs).first
                if btn.count() and btn.is_visible():
                    if dry_run:
                        log(f"DRY RUN: would send farm list {list_id}.")
                    else:
                        btn.click()
                        log(f"Farm list {list_id} submitted.")
                    return True
            except Exception:
                pass

    # Fallback: find any button whose nearby text contains the list ID.
    try:
        rows = page.locator("form, .raidList, .farmList, li")
        for i in range(min(rows.count(), 100)):
            row = rows.nth(i)
            text = row.inner_text()
            if list_id not in text:
                continue
            for bs in button_selectors:
                btn = row.locator(bs).first
                if btn.count() and btn.is_visible():
                    if dry_run:
                        log(f"DRY RUN: would send farm list {list_id}.")
                    else:
                        btn.click()
                        log(f"Farm list {list_id} submitted.")
                    return True
    except Exception:
        pass

    log(f"Farm list {list_id}: send control not found.")
    return False


def process_farm_lists(page, server, config):
    if not config.get("features", {}).get("farm_lists", False):
        return
    ids = config.get("farm_lists", [])
    if not ids:
        log("Farm Lists is enabled but no list IDs are configured.")
        return
    if not farm_page(page, server):
        log("Could not open the Travian farm-list page.")
        return

    for list_id in ids:
        send_specific_farm_list(
            page,
            list_id,
            dry_run=bool(config.get("dry_run", True)),
        )
        time.sleep(1)


def run_cycle(page, server, config):
    switch_village(page, server, config.get("village_id"))
    process_build_queue(page, server, config)
    process_farm_lists(page, server, config)


def main():
    server = normalize_server(os.getenv("TRAVIAN_SERVER", ""))
    username = os.getenv("TRAVIAN_USERNAME", "").strip()
    password = os.getenv("TRAVIAN_PASSWORD", "")

    if not server or server == "https://" or not username or not password:
        raise RuntimeError("Save Travian server, username and password in the dashboard first.")

    initial = cfg()
    headless = bool(initial.get("headless", True))

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context()
        page = context.new_page()

        try:
            if not login(page, server, username, password):
                raise RuntimeError("Travian login failed.")

            while True:
                config = cfg()
                log("Starting automation cycle.")
                try:
                    run_cycle(page, server, config)
                except Exception as e:
                    log(f"Cycle error: {type(e).__name__}: {e}")

                cycle = float(config.get("cycle_minutes", 10))
                if cycle <= 0:
                    log("Run Once completed.")
                    break

                seconds = max(60, int(cycle * 60))
                log(f"Cycle complete. Next cycle in {seconds} seconds.")
                time.sleep(seconds)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
