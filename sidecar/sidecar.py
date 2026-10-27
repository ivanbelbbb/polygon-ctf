"""Sidecar для CTF-задачи (один на под участника).

  1. Следит за $LOG_DIR/flags.log. Когда задача пишет туда [FLAG_SUCCESS],
     кладёт рядом flag.txt с ИНСТРУКЦИЕЙ по подключению вместо флага и
     flag.json с теми же данными для кнопки «Сдать задачу» (common/solve.js).
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
import time
import urllib.request

from websockets.asyncio.server import serve

TEAM = os.environ.get("TEAM", "?")
PARTICIPANT = os.environ.get("PARTICIPANT", "?")
TASK_NAME = os.environ["TASK_NAME"]
SECRET = os.environ["SECRET"].encode()
CHECKER_URL = os.environ.get("CHECKER_URL", "")
CHECKER_KEY = os.environ.get("CHECKER_KEY", "").encode()
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "<NODE_IP>")
WS_NODE_PORT = os.environ.get("WS_NODE_PORT", "<WS_NODEPORT>")
WS_PORT = int(os.environ.get("WS_PORT", "8765"))
LOG_DIR = os.environ.get("LOG_DIR", "/logs")

LOG_FILE = os.path.join(LOG_DIR, "flags.log")
INSTRUCTION_FILE = os.path.join(LOG_DIR, "flag.txt")
JSON_FILE = os.path.join(LOG_DIR, "flag.json")
MARKER = "[FLAG_SUCCESS]"

_notified = False
_solved = False


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


def _atomic_write(path: str, text: str) -> None:
    # пишем во временный файл и подменяем: работает, даже если файл
    # удалили или пересоздали из контейнера задачи
    tmp = os.path.join(LOG_DIR, "." + os.path.basename(path) + ".tmp")
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def write_state() -> None:
    """flag.txt - инструкция для человека, flag.json - то же для кнопки «Сдать задачу»."""
    state = {
        "task": TASK_NAME,
        "ws": f"ws://{PUBLIC_HOST}:{WS_NODE_PORT}",
        "ws_port": int(WS_NODE_PORT) if str(WS_NODE_PORT).isdigit() else None,
        "token": make_token(),
        "solved": _solved,
    }
    try:
        _atomic_write(INSTRUCTION_FILE, instruction_text())
        _atomic_write(JSON_FILE, json.dumps(state, ensure_ascii=False))
        print(f"[sidecar] инструкция записана в {INSTRUCTION_FILE} и {JSON_FILE}", flush=True)
    except OSError as e:
        print(f"[sidecar] не удалось записать инструкцию: {e}", flush=True)


def load_solved() -> bool:
    """После рестарта контейнера восстанавливаем состояние «сдано» из flag.json."""
    try:
        with open(JSON_FILE) as f:
            return bool(json.load(f).get("solved"))
    except (OSError, ValueError):
        return False


async def watch_log() -> None:
    """Читаем лог как tail -f, переживаем пересоздание файла.

    Начинаем с начала файла: если контейнер sidecar перезапустился после
    решения, маркер в логе уже есть и инструкцию нужно восстановить.
    """
    pos = 0
    print(f"[sidecar] слежу за {LOG_FILE}, задача={TASK_NAME}", flush=True)
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
            print(f"[sidecar] ошибка чтения лога: {e}", flush=True)
            continue
        if any(MARKER in line for line in chunk.splitlines()):
            write_state()


def notify_checker() -> None:
    global _notified
    if not CHECKER_URL or _notified:
        return
    ts = int(time.time())
    body = json.dumps({"team": TEAM, "participant": PARTICIPANT,
                       "task": TASK_NAME, "status": "solved", "ts": ts}).encode()
    sig = hmac.new(CHECKER_KEY, f"{TEAM}:{PARTICIPANT}:{TASK_NAME}:{ts}".encode(),
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        CHECKER_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
        _notified = True
        print("[sidecar] чекер уведомлён", flush=True)
    except Exception as e:  # чекер недоступен - не роняем сервер
        print(f"[sidecar] не удалось уведомить чекер: {e}", flush=True)


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
        global _solved
        await ws.send(json.dumps({"status": "solved", "task": TASK_NAME}))
        print("[sidecar] верный токен, задача решена", flush=True)
        _solved = True
        write_state()  # кнопка после перезагрузки страницы покажет «уже сдано»
        await asyncio.to_thread(notify_checker)
    else:
        await ws.send(json.dumps({"status": "denied"}))
        print("[sidecar] неверный токен", flush=True)


async def main() -> None:
    global _solved
    _solved = load_solved()
    async with serve(handler, "0.0.0.0", WS_PORT):
        print(f"[sidecar] WebSocket слушает порт {WS_PORT}", flush=True)
        await watch_log()


if __name__ == "__main__":
    asyncio.run(main())
