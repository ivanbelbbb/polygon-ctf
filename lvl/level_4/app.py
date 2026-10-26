import os
import re
import subprocess
import secrets
from flask import Flask, Response, request
from html import escape

from common.solve_flask import register_solve

app = Flask(__name__)
register_solve(app)


FLAG_LOG = os.environ.get("FLAG_LOG", "/app/logs/flags.log")
INJECTION = re.compile(r"[;&|`$\n]")
probe = secrets.token_hex(16)


def report_success(note):
    try:
        with open(FLAG_LOG, "a") as f:
            f.write(f"[FLAG_SUCCESS] {note}\n")
    except OSError as e:
        print(f"Log error: {e}", flush=True)


@app.route("/")
def index():
    return "CTF target level 4 \n На этом сайте реализована возможность пинговать сервисы через API\n \
        попробуй /ping?host=127.0.0.1 \
        найди уязвимость в этом сервисе"


@app.route("/ping")
def ping():
    host = request.args.get("host", "127.0.0.1")

    # рандомное число - токен для проверки 
    probe = secrets.token_hex(16)

    # переменная окружения с токеном
    env = os.environ.copy()
    env["CTF_PROBE"] = probe

    try:
        result = subprocess.run(
            f"ping -c 1 {host}",
            shell=True, 
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        # если токен в stdout - уязвимость нашли
        if probe in result.stdout:
            report_success("level-4-command-injection")

        output = result.stdout + result.stderr

        return Response(
            f"<pre>{escape(output)}</pre>",
            mimetype="text/html",
        )

    except subprocess.TimeoutExpired:
        return Response(
            "<pre>timeout</pre>",
            status=504,
            mimetype="text/html",
        )



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
