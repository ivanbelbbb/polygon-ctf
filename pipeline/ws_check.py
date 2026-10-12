"""Клиент для проверки: подключается по WebSocket и отправляет токен из инструкции.

Пример:
  python3 ws_check.py ws://192.168.1.10:30081 <токен>
"""
import asyncio
import json
import sys

from websockets.asyncio.client import connect


async def main(url: str, token: str) -> int:
    async with connect(url, open_timeout=5) as ws:
        await ws.send(json.dumps({"token": token}))
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    print(reply)
    return 0 if reply.get("status") == "solved" else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))