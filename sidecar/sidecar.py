"""Sidecar для CTF-задачи (один на под участника).

  1. Следит за $LOG_DIR/flags.log. Когда задача пишет туда [FLAG_SUCCESS],
     кладёт рядом flag.txt с ИНСТРУКЦИЕЙ по подключению вместо флага.
  2. Поднимает WebSocket-сервер. Участник шлёт токен из инструкции;
     если токен верный - отвечаем {"status": "solved"} и (если задан CHECKER_URL)
     один раз уведомляем чекер POST-запросом.

Токен = HMAC(SECRET, "team:participant:task"). SECRET лежит в Kubernetes Secret
и доступен только sidecar-контейнеру, контейнер задачи его не видит.
"""
import asyncio
import hashlib
import hmac
import json
import os
import urllib.request

from websockets.asyncio.server import serve

TEAM = os.environ.get("TEAM", "?")
PARTICIPANT = os.environ.get("PARTICIPANT", "?")
TASK_NAME = os.environ["TASK_NAME"]
SECRET = os.environ["SECRET"].encode()
CHECKER_URL = os.environ.get("CHECKER_URL", "")
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "<NODE_IP>")
WS_NODE_PORT = os.environ.get("WS_NODE_PORT", "<WS_NODEPORT>")
WS_PORT = int(os.environ.get("WS_PORT", "8765"))
LOG_DIR = os.environ.get("LOG_DIR", "/logs")

LOG_FILE = os.path.join(LOG_DIR, "flags.log")
INSTRUCTION_FILE = os.path.join(LOG_DIR, "flag.txt")
MARKER = "[FLAG_SUCCESS]"

_notified = False


def make_token() -> str:
    msg = f"{TEAM}:{PARTICIPANT}:{TASK_NAME}".encode()
    return hmac.new(SECRET, msg, hashlib.sha256).hexdigest()[:32]


def instruction_text() -> str:
    url = f"ws://{PUBLIC_HOST}:{WS_NODE_PORT}"
    return (
        "Здесь должен быть флаг, но вместо него - инструкция.\n"
        "Чтобы задача была засчитана, открой WebSocket-соединение:\n"
        f"  адрес:    {url}\n"
        f'  отправь:  {{"token": "{make_token()}"}}\n'
        'В ответ придёт {"status": "solved"}.\n'
    )


def write_instruction() -> None:
    # пишем во временный файл и подменяем: работает, даже если flag.txt
    # удалили или пересоздали из контейнера задачи
    tmp = os.path.join(LOG_DIR, ".flag.tmp")
    try:
        with open(tmp, "w") as f:
            f.write(instruction_text())
        os.chmod(tmp, 0o644)
        os.replace(tmp, INSTRUCTION_FILE)
        print(f"[helper] флаг найден, инструкция записана в {INSTRUCTION_FILE}", flush=True)
    except OSError as e:
        print(f"[helper] не удалось записать инструкцию: {e}", flush=True)


async def watch_log() -> None:
    """Читаем только новые строки (как tail -f), переживаем пересоздание файла."""
    pos = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0
    print(f"[helper] слежу за {LOG_FILE}, задача={TASK_NAME}", flush=True)
    while True:
        await asyncio.sleep(1)
        try:
            if not os.path.exists(LOG_FILE):
                pos = 0
                continue
            size = os.path.getsize(LOG_FILE)
            if size < pos:  # файл обрезали
                pos = 0
            if size == pos:
                continue
            with open(LOG_FILE, errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
        except OSError as e:
            print(f"[helper] ошибка чтения лога: {e}", flush=True)
            continue
        if any(MARKER in line for line in chunk.splitlines()):
            write_instruction()


def notify_checker() -> None:
    global _notified
    if not CHECKER_URL or _notified:
        return
    body = json.dumps({"team": TEAM, "participant": PARTICIPANT,
                       "task": TASK_NAME, "status": "solved"}).encode()
    req = urllib.request.Request(
        CHECKER_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
        _notified = True
        print("[helper] чекер уведомлён", flush=True)
    except Exception as e:  # чекер недоступен - не роняем сервер
        print(f"[helper] не удалось уведомить чекер: {e}", flush=True)


async def handler(ws) -> None:
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
    except Exception:
        return
    try:
        token = str(json.loads(raw).get("token", ""))
    except (ValueError, AttributeError):
        token = str(raw).strip()

    if hmac.compare_digest(token, make_token()):
        await ws.send(json.dumps({"status": "solved", "task": TASK_NAME}))
        print("[helper] верный токен, задача решена", flush=True)
        await asyncio.to_thread(notify_checker)
    else:
        await ws.send(json.dumps({"status": "denied"}))
        print("[helper] неверный токен", flush=True)


async def main() -> None:
    async with serve(handler, "0.0.0.0", WS_PORT):
        print(f"[helper] WebSocket слушает порт {WS_PORT}", flush=True)
        await watch_log()


if __name__ == "__main__":
    asyncio.run(main())
