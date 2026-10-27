import os
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from common.solve_flask import register_solve

app = Flask(__name__)
register_solve(app)

BASE_DIR = Path(__file__).resolve().parent

# БД и логи — в заведомо writable директорию (переопределяется env-переменными),
# не рядом со скриптом: контейнер может быть read-only или без прав на запись.в
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("DB_PATH", str(DATA_DIR / "polygon.db")))
LOG_DIR = Path(os.environ.get("LOG_DIR", str(DATA_DIR / "logs")))
FLAG_LOG = LOG_DIR / "flags.log"

# Внутренний адрес, который открывает тестовый браузер (сам процесс, localhost).
SELF_URL = os.environ.get("SELF_URL", "http://127.0.0.1:5000/")
BOT_INTERVAL_SECONDS = float(os.environ.get("BOT_INTERVAL_SECONDS", "3"))
BOT_ENABLED = os.environ.get("BOT_ENABLED", "1") != "0"

SUCCESS_REPORTED = False
_lock = threading.Lock()


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with closing(get_db()) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.commit()


def report_success(note):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(FLAG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[FLAG_SUCCESS] {note}\n")
    except OSError as e:
        print(f"Log error: {e}", flush=True)


def list_comments():
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT id, author, body, created_at FROM comments ORDER BY id DESC"
        ).fetchall()
    return rows


@app.get("/")
def index():
    return render_template("index.html", comments=list_comments())


@app.post("/comment")
def add_comment():
    author = request.form.get("author", "").strip() or "Аноним"
    body = request.form.get("body", "")
    if body.strip():
        with closing(get_db()) as db:
            db.execute(
                "INSERT INTO comments (author, body) VALUES (?, ?)",
                (author, body),
            )
            db.commit()
    return redirect(url_for("index"))


init_db()


# ---------------------------------------------------------------------------
# Тестовый браузер (не публикуется в UI): отдельный headless Chromium
# периодически открывает страницу как обычный посетитель и проверяет,
# что внедрённый код реально ВЫПОЛНИЛСЯ в контексте страницы — а не просто
# присутствует как текст в комментарии или в query string.
#
# Для этого перед загрузкой страницы в неё кладётся одноразовый токен
# (window.__polygonToken), а колбэк (window.__polygonSignal) привязывается
# через Playwright expose_function — вызвать его можно только исполнив JS
# в этом самом браузере. Payload должен прочитать токен в рантайме и
# передать его обратно, иначе проверка не засчитывается.
# ---------------------------------------------------------------------------
def _run_browser_checker():
    global SUCCESS_REPORTED
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        while True:
            try:
                context = browser.new_context()
                page = context.new_page()

                token = secrets.token_hex(8)
                proof = {"ok": False}

                def _on_signal(received_token, _token=token, _proof=proof):
                    if received_token == _token:
                        _proof["ok"] = True

                page.expose_function("__polygonSignal", _on_signal)
                page.add_init_script(f"window.__polygonToken = {token!r};")

                page.goto(SELF_URL, wait_until="networkidle", timeout=5000)
                page.wait_for_timeout(500)

                context.close()

                if proof["ok"] and not SUCCESS_REPORTED:
                    with _lock:
                        if not SUCCESS_REPORTED:
                            report_success("level-xss")
                            SUCCESS_REPORTED = True
            except Exception as e:
                print(f"XSS checker error: {e}", flush=True)

            time.sleep(BOT_INTERVAL_SECONDS)


def start_checker_thread():
    if not BOT_ENABLED:
        return
    t = threading.Thread(target=_run_browser_checker, daemon=True)
    t.start()


start_checker_thread()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)