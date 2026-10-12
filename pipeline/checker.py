"""Проверка доступности задач команды: HTTP-порт задачи и TCP-порт WebSocket sidecar.

  python3 checker.py --team 1
  python3 checker.py --team 1 --participant 2
Код возврата 0 - всё доступно, 1 - есть проблемы.
"""
import argparse
import socket
import sys
import urllib.error
import urllib.request

from kubernetes import client, config


def node_ip(core_v1):
    nodes = core_v1.list_node().items
    for kind in ("ExternalIP", "InternalIP"):
        for n in nodes:
            for a in n.status.addresses or []:
                if a.type == kind:
                    return a.address
    return None


def check_http(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        return False, str(e)
    return code < 500, f"HTTP {code}"


def check_tcp(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True, "открыт"
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="Check CTF team services")
    parser.add_argument("--team", type=int, required=True)
    parser.add_argument("--participant", type=int)
    parser.add_argument("--host", help="IP ноды (по умолчанию - IP первой ноды кластера)")
    args = parser.parse_args()

    config.load_kube_config()
    core_v1 = client.CoreV1Api()
    host = args.host or node_ip(core_v1)
    if not host:
        print("[-] Не удалось определить IP ноды, укажите --host")
        sys.exit(1)

    sel = f"ctf/team={args.team}"
    if args.participant:
        sel += f",ctf/participant={args.participant}"
    services = core_v1.list_namespaced_service(f"team-{args.team}", label_selector=sel).items
    if not services:
        print(f"[-] В team-{args.team} не найдено сервисов")
        sys.exit(1)

    ok_all = True
    for svc in sorted(services, key=lambda s: s.metadata.name):
        ports = {p.name: p.node_port for p in svc.spec.ports}
        ok_http, info_http = check_http(f"http://{host}:{ports['task']}")
        ok_ws, info_ws = check_tcp(host, ports["ws"])
        ok_all &= ok_http and ok_ws
        print(f"{'OK ' if ok_http and ok_ws else 'FAIL'} {svc.metadata.name}: "
              f"задача :{ports['task']} ({info_http}), ws :{ports['ws']} ({info_ws})")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
