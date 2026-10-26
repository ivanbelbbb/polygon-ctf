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
# Регулярка для проверки наличия инъекции (спецсимволов)
INJECTION_PATTERN = re.compile(r"[;&|`$\n]")


def report_success(note):
    try:
        os.makedirs(os.path.dirname(FLAG_LOG), exist_ok=True)
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

    try:
        result = subprocess.run(
            f"ping -c 1 {host}",
            shell=True, 
            capture_output=True,
            text=True,
            timeout=10,
        )

        output = result.stdout + result.stderr

        # Условие победы: 
        # Если в переданном хосте пользователь использовал спецсимволы инъекции 
        # И при этом команда завершилась без критического сбоя самого пинга 
        # (или инъекция явно присутствует в параметре)
        if INJECTION_PATTERN.search(host):
            report_success("level-4-command-injection")

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