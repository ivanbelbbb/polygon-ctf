import os
import re
import subprocess

from flask import Flask, request

app = Flask(__name__)

# Общий с sidecar каталог: как только здесь появляется [FLAG_SUCCESS],
# sidecar кладёт в /app/logs/flag.txt инструкцию с токеном.
FLAG_LOG = os.environ.get("FLAG_LOG", "/app/logs/flags.log")
INJECTION = re.compile(r"[;&|`$\n]")


def report_success(note):
    try:
        with open(FLAG_LOG, "a") as f:
            f.write(f"[FLAG_SUCCESS] {note}\n")
    except OSError as e:
        print(f"не удалось записать {FLAG_LOG}: {e}", flush=True)


@app.route("/")
def index():
    return "CTF target level 1 — ping checker. Try /ping?host=127.0.0.1"


@app.route("/ping")
def ping():
    host = request.args.get("host", "127.0.0.1")
    # Уязвимость (намеренная): host подставляется в shell без экранирования.
    # Успехом считаем попытку инъекции: в host есть метасимволы shell.
    if INJECTION.search(host):
        report_success("command injection in /ping")
    try:
        result = subprocess.run(f"ping -c 1 {host}", shell=True,
                                capture_output=True, text=True, timeout=10)
        return f"<pre>{result.stdout}{result.stderr}</pre>"
    except subprocess.TimeoutExpired:
        return "<pre>timeout</pre>", 504


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
