import os
import sqlite3
from contextlib import closing
from pathlib import Path

from flask import Flask, render_template, request

from common.solve_flask import register_solve

app = Flask(__name__)
register_solve(app)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polygon.db"

# Лог-директория: из переменной окружения, по умолчанию /app/logs (в контейнере).
LOG_DIR = Path(os.environ.get("LOG_DIR", "/app/logs"))
FLAG_LOG = LOG_DIR / "flags.log"

SUCCESS_REPORTED = False


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with closing(get_db()) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                email TEXT NOT NULL,
                access_level TEXT NOT NULL,
                is_secret INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.executemany("""
            INSERT OR IGNORE INTO employees
                (id, name, department, email, access_level, is_secret)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (101, "Мария Соколова", "Финансы", "m.sokolova@polygon.local", "PUBLIC", 0),
            (102, "Илья Морозов", "Разработка", "i.morozov@polygon.local", "PUBLIC", 0),
            (103, "Анна Волкова", "Поддержка", "a.volkova@polygon.local", "PUBLIC", 0),
            (9004, "Internal Audit Record", "Security", "audit-internal@polygon.local", "RESTRICTED", 1),
        ])
        db.commit()


def report_success(note):
    global FLAG_LOG
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(FLAG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[FLAG_SUCCESS] {note}\n")
    except OSError as e:
        # Фолбэк: если /app/logs недоступен, пишем рядом со скриптом
        print(f"Log error: {e}", flush=True)
        try:
            fallback = BASE_DIR / "logs"
            fallback.mkdir(parents=True, exist_ok=True)
            with open(fallback / "flags.log", "a", encoding="utf-8") as f:
                f.write(f"[FLAG_SUCCESS] {note}\n")
        except OSError as e2:
            print(f"Fallback log error: {e2}", flush=True)


# Инициализация БД при импорте модуля (работает и под flask run, и под gunicorn)
init_db()


@app.get("/")
def index():
    global SUCCESS_REPORTED

    search = request.args.get("q", "")
    employees = []
    error = None

    try:
        with closing(get_db()) as db:
            # Намеренно уязвимый запрос (SQL Injection) — это учебный полигон.
            sql = f"""
                SELECT id, name, department, email,
                       access_level, is_secret
                FROM employees
                WHERE is_secret = 0
                  AND name LIKE '%{search}%'
                ORDER BY id
            """
            employees = db.execute(sql).fetchall()
    except sqlite3.Error:
        error = "Не удалось выполнить запрос. Проверьте параметры поиска."

    solved = any(row["is_secret"] == 1 for row in employees)

    if solved and not SUCCESS_REPORTED:
        report_success("level-sql-injection")
        SUCCESS_REPORTED = True

    return render_template(
        "index.html",
        employees=employees,
        search=search,
        error=error,
        solved=solved,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)