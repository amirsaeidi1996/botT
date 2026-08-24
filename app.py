import os
import signal
import subprocess
import threading
from collections import deque
from pathlib import Path

import yaml
from dotenv import load_dotenv, set_key
from flask import Flask, redirect, render_template, request, session, url_for, jsonify

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / "config.yaml"
LOG_PATH = ROOT / "bot.log"

ENV_PATH.touch(exist_ok=True)
load_dotenv(ENV_PATH)

app = Flask(__name__, template_folder=str(ROOT))
app.secret_key = os.getenv("FLASK_SECRET", os.urandom(32))

proc = None
proc_lock = threading.Lock()
log_tail = deque(maxlen=500)


def dashboard_password():
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("DASHBOARD_PASSWORD", "change-this-password")


def authorized():
    return session.get("ok") is True


def read_config():
    if not CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def write_config(cfg):
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def parse_build_queue(text):
    items = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.replace(",", ":")
        parts = [p.strip() for p in line.split(":")]
        if len(parts) != 2:
            continue
        try:
            target = int(parts[0])
            desired = int(parts[1])
        except ValueError:
            continue
        if target > 0 and desired > 0:
            items.append({"target": target, "desired_level": desired})
    return items


def build_queue_text(cfg):
    return "\n".join(
        f"{int(x.get('target', 0))}:{int(x.get('desired_level', 1))}"
        for x in cfg.get("build_queue", [])
        if x.get("target")
    )


def reader_thread(p):
    with LOG_PATH.open("a", encoding="utf-8") as f:
        for line in iter(p.stdout.readline, ""):
            if not line:
                break
            line = line.rstrip("\n")
            log_tail.append(line)
            f.write(line + "\n")
            f.flush()


def is_running():
    global proc
    return proc is not None and proc.poll() is None


def start_bot():
    global proc
    with proc_lock:
        if is_running():
            return False, "Bot is already running."

        load_dotenv(ENV_PATH, override=True)
        env = os.environ.copy()
        for key in ["TRAVIAN_SERVER", "TRAVIAN_USERNAME", "TRAVIAN_PASSWORD"]:
            env[key] = os.getenv(key, "")

        proc = subprocess.Popen(
            ["python", "-u", str(ROOT / "travian_bot.py")],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        threading.Thread(target=reader_thread, args=(proc,), daemon=True).start()
        return True, "Bot started."


def stop_bot():
    global proc
    with proc_lock:
        if not is_running():
            return False, "Bot is not running."
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=8)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
        proc = None
        return True, "Bot stopped."


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password", "") == dashboard_password():
            session["ok"] = True
            return redirect(url_for("index"))
        error = "Wrong dashboard password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
def index():
    if not authorized():
        return redirect(url_for("login"))

    cfg = read_config()
    load_dotenv(ENV_PATH, override=True)
    message = request.args.get("msg")

    if request.method == "POST":
        server = request.form.get("server", "").strip().rstrip("/")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        set_key(str(ENV_PATH), "TRAVIAN_SERVER", server)
        set_key(str(ENV_PATH), "TRAVIAN_USERNAME", username)
        if password:
            set_key(str(ENV_PATH), "TRAVIAN_PASSWORD", password)

        cfg["dry_run"] = request.form.get("dry_run") == "on"
        cfg["headless"] = True

        try:
            cfg["cycle_minutes"] = max(1, float(request.form.get("cycle_minutes", "10")))
        except ValueError:
            cfg["cycle_minutes"] = 10

        village_raw = request.form.get("village_id", "").strip()
        cfg["village_id"] = int(village_raw) if village_raw.isdigit() else None

        features = cfg.setdefault("features", {})
        features["build_queue"] = request.form.get("build_queue_enabled") == "on"
        features["farm_lists"] = request.form.get("farm_lists_enabled") == "on"
        features["refresh_status"] = True

        cfg["one_build_per_cycle"] = request.form.get("one_build_per_cycle") == "on"
        cfg["build_queue"] = parse_build_queue(request.form.get("build_queue_text", ""))

        farms_raw = request.form.get("farm_list_ids", "").strip()
        cfg["farm_lists"] = [
            int(x.strip()) for x in farms_raw.split(",") if x.strip().isdigit()
        ]

        write_config(cfg)
        message = "Settings saved."

    return render_template(
        "index.html",
        running=is_running(),
        cfg=cfg,
        server=os.getenv("TRAVIAN_SERVER", ""),
        username=os.getenv("TRAVIAN_USERNAME", ""),
        farm_list_ids=",".join(str(x) for x in cfg.get("farm_lists", [])),
        build_queue_text=build_queue_text(cfg),
        message=message,
    )


@app.post("/start")
def start():
    if not authorized():
        return redirect(url_for("login"))
    _, msg = start_bot()
    return redirect(url_for("index", msg=msg))


@app.post("/stop")
def stop():
    if not authorized():
        return redirect(url_for("login"))
    _, msg = stop_bot()
    return redirect(url_for("index", msg=msg))


@app.post("/run-once")
def run_once():
    if not authorized():
        return redirect(url_for("login"))
    if is_running():
        return redirect(url_for("index", msg="Stop the continuous bot before Run Once."))

    cfg = read_config()
    old = cfg.get("cycle_minutes", 10)
    cfg["cycle_minutes"] = 0
    write_config(cfg)

    ok, msg = start_bot()

    def restore():
        import time
        time.sleep(4)
        cfg2 = read_config()
        cfg2["cycle_minutes"] = old
        write_config(cfg2)

    threading.Thread(target=restore, daemon=True).start()
    return redirect(url_for("index", msg=msg))


@app.get("/api/status")
def api_status():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"running": is_running(), "logs": list(log_tail)[-200:]})


@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    from waitress import serve
    port = int(os.getenv("PORT", "8080"))
    serve(app, host="0.0.0.0", port=port)
