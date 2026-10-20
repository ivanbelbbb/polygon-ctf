"""Checker: принимает уведомления от sidecar и пишет решения в Postgres.

Запрос sidecar подписан HMAC-SHA256 общим ключом CHECKER_KEY (лежит только в Secret,
смонтированном в sidecar; контейнер задачи его не видит). Без подписи любой
скомпрометированный под мог бы начислить очки чужой команде.
Подключение к БД берётся из переменных PGHOST/PGUSER/PGPASSWORD/PGDATABASE.
"""
import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

KEY = os.environ["CHECKER_KEY"].encode()
MAX_SKEW = 300  # секунд допустимого расхождения часов
pool = ConnectionPool(conninfo="", min_size=1, max_size=5, open=False)


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
