import os
from flask import Flask, Response

app = Flask(__name__)

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
        "Задача выполнена.",
        mimetype="text/plain",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)