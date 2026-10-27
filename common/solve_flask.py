"""Подключение кнопки «Сдать задачу» к Flask-задаче одной строкой.

    from common.solve_flask import register_solve
    register_solve(app)

Что делает:
  - GET /__flag.json - отдаёт $LOG_DIR/flag.json (404, пока задача не решена);
  - GET /__solve.js  - отдаёт общий скрипт кнопки;
  - после каждого HTML-ответа дописывает <script src="/__solve.js"> перед
    </body> (или в конец, если тега нет), поэтому шаблоны трогать не нужно.
"""
import os
from pathlib import Path

from flask import Response

_HERE = Path(__file__).resolve().parent
_TAG = '<script src="/__solve.js"></script>'
_NO_STORE = {"Cache-Control": "no-store"}


def register_solve(app, log_dir=None):
    log_dir = Path(log_dir or os.environ.get("LOG_DIR", "/app/logs"))
    flag_json = log_dir / "flag.json"
    script = (_HERE / "solve.js").read_bytes()

    @app.get("/__flag.json")
    def _solve_flag():
        try:
            data = flag_json.read_bytes()
        except OSError:
            return Response('{"solved": false}', status=404,
                            mimetype="application/json", headers=_NO_STORE)
        return Response(data, mimetype="application/json", headers=_NO_STORE)

    @app.get("/__solve.js")
    def _solve_js():
        return Response(script, mimetype="application/javascript",
                        headers=_NO_STORE)

    @app.after_request
    def _inject_script(resp):
        if (resp.status_code != 200 or resp.mimetype != "text/html"
                or resp.direct_passthrough or resp.is_streamed):
            return resp
        html = resp.get_data(as_text=True)
        if _TAG in html:
            return resp
        idx = html.lower().rfind("</body>")
        html = html + _TAG if idx == -1 else html[:idx] + _TAG + html[idx:]
        resp.set_data(html)
        return resp
