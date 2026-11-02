"""Checker: принимает уведомления от sidecar, пишет решения в Postgres и двигает прогресс
участника (ParticipantChallenge.status.currentLevel) через Kubernetes API.

Запрос sidecar подписан HMAC-SHA256 общим ключом CHECKER_KEY (лежит только в Secret,
смонтированном в sidecar; контейнер задачи его не видит). Без подписи любой
скомпрометированный под мог бы начислить очки чужой команде.

Две независимые вещи происходят при валидном решении:
  - очки команде (team-score): одно решение на задачу на команду за неделю, см. process();
  - прогресс участника (level progression): у КАЖДОГО участника свой уровень, продвигается
    при любом валидном решении его ТЕКУЩЕГО уровня, вне зависимости от того, получила ли
    команда очки (кто-то другой из команды мог решить эту же задачу раньше) - см. bump_level().
"""
import hashlib
import hmac
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

KEY = os.environ["CHECKER_KEY"].encode()
MAX_SKEW = 300  # секунд допустимого расхождения часов
pool = ConnectionPool(conninfo="", min_size=1, max_size=5, open=False)

# ---------- Kubernetes: прогресс участника ----------
GROUP, CRD_VERSION, PLURAL = "ctf.example.org", "v1", "participantchallenges"
TASK_PREFIX = os.environ.get("TASK_PREFIX", "level")
LEVEL_RE = re.compile(rf"^{re.escape(TASK_PREFIX)}(\d+)$")

try:
    from kubernetes import client as k8s_client, config as k8s_config
    k8s_config.load_incluster_config()
    CUSTOM_API = k8s_client.CustomObjectsApi()
except Exception as e:  # нет кластера рядом (например, локальный тест) - прогресс просто не ведём
    CUSTOM_API = None
    print(f"[checker] Kubernetes API недоступен, прогресс уровней отключён: {e}", flush=True)


def bump_level(team: int, participant: int, task: str) -> None:
    """Поднимает currentLevel участника на 1, если task совпадает с его ТЕКУЩИМ уровнем.
    Решение чужого/старого уровня (что физически не должно происходить - см. ниже) и попытка
    поднять выше maxLevel молча игнорируются. Любая ошибка API не должна ронять /solved -
    команда всё равно получит свои очки, даже если прогресс обновить не удалось."""
    if CUSTOM_API is None:
        return
    m = LEVEL_RE.fullmatch(task)
    if not m:
        return  # задача вне схемы level0..levelN (на будущее, для других треков)
    solved_level = int(m.group(1))
    namespace, name = f"team-{team}", f"p{participant}"
    try:
        cr = CUSTOM_API.get_namespaced_custom_object(GROUP, CRD_VERSION, namespace, PLURAL, name)
        current = (cr.get("status") or {}).get("currentLevel")
        max_level = (cr.get("spec") or {}).get("maxLevel", solved_level)
        # Секрет (и значит токен sidecar) перевыпускается при КАЖДОЙ смене уровня, поэтому
        # валидно подписанный запрос физически не может прийти для уровня, который уже не
        # активен у участника - эта проверка лишь вторая, дешёвая линия защиты.
        if current != solved_level or current >= max_level:
            return
        CUSTOM_API.patch_namespaced_custom_object_status(
            GROUP, CRD_VERSION, namespace, PLURAL, name,
            {"status": {"currentLevel": current + 1,
                        "lastCaptureAt": datetime.now(timezone.utc).isoformat()}})
    except Exception as e:
        print(f"[checker] bump_level(team={team}, participant={participant}, task={task}) "
             f"не удался: {e}", flush=True)


@asynccontextmanager
async def lifespan(_app):
    pool.open(wait=True, timeout=30)
    yield
    pool.close()


app = FastAPI(lifespan=lifespan)


class Solved(BaseModel):
    team: int = Field(ge=1, le=100000)
    participant: int = Field(ge=1, le=1000)
    task: str = Field(max_length=40)
    status: str = Field(max_length=20)
    ts: int


def log_event(cur, src, payload, accepted, reason):
    cur.execute("INSERT INTO events (source_ip, payload, accepted, reason) VALUES (%s, %s, %s, %s)",
                (src, Jsonb(payload), accepted, reason))


def process(cur, src, b: Solved, sig: str):
    payload = b.model_dump()
    msg = f"{b.team}:{b.participant}:{b.task}:{b.ts}".encode()
    expected = hmac.new(KEY, msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        log_event(cur, src, payload, False, "bad signature")
        return 403, {"result": "denied"}
    if abs(time.time() - b.ts) > MAX_SKEW:
        log_event(cur, src, payload, False, "stale timestamp")
        return 403, {"result": "denied"}
    if b.status != "solved":
        log_event(cur, src, payload, False, "bad status")
        return 400, {"result": "bad status"}

    cur.execute("SELECT points FROM tasks WHERE name = %s", (b.task,))
    row = cur.fetchone()
    if not row:
        log_event(cur, src, payload, False, "unknown task")
        return 404, {"result": "unknown task"}
    points = row[0]

    # команды и участники регистрируются автоматически по первой валидной подписи
    cur.execute("INSERT INTO teams (id) VALUES (%s) ON CONFLICT DO NOTHING", (b.team,))
    cur.execute("INSERT INTO participants (team_id, slot) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (b.team, b.participant))
    cur.execute("SELECT id FROM participants WHERE team_id = %s AND slot = %s", (b.team, b.participant))
    pid = cur.fetchone()[0]

    cur.execute(
        """INSERT INTO solves (team_id, participant_id, task, week_start, points)
           VALUES (%s, %s, %s, date_trunc('week', now() AT TIME ZONE 'utc')::date, %s)
           ON CONFLICT (team_id, task, week_start) DO NOTHING RETURNING id""",
        (b.team, pid, b.task, points))
    counted = cur.fetchone() is not None
    log_event(cur, src, payload, counted, None if counted else "duplicate (team already solved)")
    return 200, {"result": "counted" if counted else "duplicate"}


@app.post("/solved")
def solved(body: Solved, request: Request, x_signature: str = Header(default="")):
    src = request.client.host if request.client else None
    # коммит происходит при выходе из with, поэтому событие пишется и для отказов
    with pool.connection() as conn, conn.cursor() as cur:
        code, data = process(cur, src, body, x_signature)
    if code == 200:
        # вне транзакции БД: прогресс участника не должен держать соединение к Postgres
        # открытым на время похода в Kubernetes API, а неудача здесь не должна портить
        # уже сохранённый результат по очкам
        bump_level(body.team, body.participant, body.task)
    return JSONResponse(data, status_code=code)


def rows(query):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(query)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@app.get("/leaderboard/teams")
def lb_teams():
    return rows("SELECT * FROM leaderboard_teams ORDER BY points DESC, last_solve ASC NULLS LAST")


@app.get("/leaderboard/participants")
def lb_participants():
    return rows("SELECT * FROM leaderboard_participants ORDER BY points DESC, last_solve ASC NULLS LAST")


@app.get("/healthz")
def healthz():
    return {"ok": True}


DASHBOARD_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CTF Polygon — таблица лидеров</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: ui-monospace, "SF Mono", Consolas, monospace; background: #0b0f14;
         color: #d7e0e8; margin: 0; padding: 24px 16px 60px; }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 4px; }
  .sub { color: #6b7785; font-size: 13px; margin-bottom: 28px; }
  .cols { display: flex; gap: 24px; flex-wrap: wrap; max-width: 900px; margin: 0 auto; }
  .col { flex: 1 1 360px; min-width: 300px; }
  h2 { font-size: 14px; color: #8fa3b3; text-transform: uppercase; letter-spacing: .04em; margin: 0 0 10px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 8px 10px; font-size: 14px; border-bottom: 1px solid #1c2630; }
  th { color: #6b7785; font-weight: 500; font-size: 12px; text-transform: uppercase; }
  td.points { text-align: right; font-variant-numeric: tabular-nums; color: #7ee787; }
  tr:first-child td { color: #f2c94c; }
  .empty { color: #6b7785; padding: 16px 0; font-size: 13px; }
</style>
</head>
<body>
<h1>CTF Polygon</h1>
<div class="sub">обновляется каждые 5 секунд</div>
<div class="cols">
  <div class="col">
    <h2>Команды</h2>
    <table id="teams"><thead><tr><th>#</th><th>Команда</th><th>Решено</th><th class="points">Очки</th></tr></thead><tbody></tbody></table>
  </div>
  <div class="col">
    <h2>Участники</h2>
    <table id="participants"><thead><tr><th>#</th><th>Участник</th><th>Решено</th><th class="points">Очки</th></tr></thead><tbody></tbody></table>
  </div>
</div>
<script>
async function load(url, tableId) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  try {
    const rows = await (await fetch(url)).json();
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="4" class="empty">пока пусто</td></tr>'; return; }
    tbody.innerHTML = rows.map((r, i) => `
      <tr>
        <td>${i + 1}</td>
        <td>${r.name}</td>
        <td>${r.solves}</td>
        <td class="points">${r.points}</td>
      </tr>`).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">нет связи с сервером</td></tr>';
  }
}
function refresh() {
  load('/leaderboard/teams', 'teams');
  load('/leaderboard/participants', 'participants');
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML
