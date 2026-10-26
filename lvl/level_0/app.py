import os
from flask import Flask, Response

from common.solve_flask import register_solve

app = Flask(__name__)
register_solve(app)

FLAG_LOG = os.environ.get("FLAG_LOG", "/app/logs/flags.log")

def report_success(note):
    try:
        with open(FLAG_LOG, "a") as f:
            f.write(f"[FLAG_SUCCESS] {note}\n")
    except OSError as e:
        print(f"Log error: {e}", flush=True)



@app.get("/")
def index():
    return (
        "POLYGON — Level 0\n найди скрытый маршрут."
    )


@app.get("/robots.txt")
def robots():
    return Response(
        "User-agent: *\nDisallow: /admin\n",
        mimetype="text/plain",
    )


@app.get("/admin")
def complete():
    report_success("level-0-recon")
    return Response(
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        "<title>Admin</title></head><body><h1>Задача выполнена.</h1></body></html>",
        mimetype="text/html",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)