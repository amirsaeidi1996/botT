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

# Templates are deliberately kept in the repository root so everything can
# be uploaded as individual files from a phone.
app = Flask(__name__, template_folder=str(ROOT))
app.secret_key = os.getenv("FLASK_SECRET", os.urandom(32))

proc = None
proc_lock = threading.Lock()
log_tail = deque(maxlen=300)


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
        env = os.environ.copy()
        load_dotenv(ENV_PATH, override=True)
        for k in ["TRAVIAN_SERVER", "TRAVIAN_USERNAME", "TRAVIAN_PASSWORD"]:
            env[k] = os.getenv(k, "")
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
            cfg["cycle_minutes"] = max(0, float(request.form.get("cycle_minutes", "10")))
        except ValueError:
            cfg["cycle_minutes"] = 10
        features = cfg.setdefault("features", {})
        features["collect_resources"] = request.form.get("collect_resources") == "on"
        features["build_queue"] = request.form.get("build_queue") == "on"
        features["farm_lists"] = request.form.get("farm_lists") == "on"
        farms = request.form.get("farm_list_ids", "").strip()
        cfg["farm_lists"] = [int(x.strip()) for x in farms.split(",") if x.strip().isdigit()]
        write_config(cfg)
        message = "Settings saved."
    return render_template(
        "index.html",
        running=is_running(),
        cfg=cfg,
        server=os.getenv("TRAVIAN_SERVER", ""),
        username=os.getenv("TRAVIAN_USERNAME", ""),
        farm_list_ids=",".join(str(x) for x in cfg.get("farm_lists", [])),
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


@app.post("/farm-now")
def farm_now():
    if not authorized():
        return redirect(url_for("login"))
    return redirect(url_for("index", msg="Farm action is disabled in this safe build."))


@app.get("/api/status")
def api_status():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"running": is_running(), "logs": list(log_tail)[-100:]})


@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    from waitress import serve
    port = int(os.getenv("PORT", "8080"))
    serve(app, host="0.0.0.0", port=port)
