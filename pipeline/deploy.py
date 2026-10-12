"""Деплой CTF-полигона в Kubernetes.

Схема:
  namespace team-<N>              одна команда (3-4 участника)
    Deployment <task>-p<i>        под участника i: контейнер задачи + sidecar
    Service    <task>-p<i>        NodePort: порт задачи и порт WebSocket sidecar
    Secret     <task>-p<i>        секрет для токена (создаётся один раз, не меняется при передеплое)
    NetworkPolicy ctf-ingress / ctf-egress   изоляция от других команд

Как засчитывается задача:
  - задача пишет строку с [FLAG_SUCCESS] в /app/logs/flags.log
  - sidecar видит её и кладёт в /app/logs/flag.txt ИНСТРУКЦИЮ вместо флага
  - участник подключается к sidecar по WebSocket и шлёт токен из инструкции
"""
import argparse
import ipaddress
import os
import re
import secrets
import sys

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

WS_PORT = 8765                       # порт WebSocket внутри пода
TASK_LOG_DIR = "/app/logs"           # куда пишет задача
HELPER_LOG_DIR = "/logs"             # то же место, смонтированное у sidecar
PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.yaml")

DEFAULTS = {
    "max_participants": 4,
    "node_port_base": 30000,
    "pod_cidr": "10.244.0.0/16",
    "helper_image": "ctf-sidecar:1",
    "image_pull_policy": "IfNotPresent",
    "internet_egress": True,
    "checker_url": "",
    "checker_namespace": "",
}


# ---------- конфиг ----------

def load_config(path):
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    settings = {**DEFAULTS, **(data.get("settings") or {})}
    tasks = data.get("tasks") or []
    if not tasks:
        raise SystemExit(f"[-] В {path} нет ни одной задачи (секция tasks)")
    seen = set()
    for t in tasks:
        for key in ("name", "image", "port"):
            if key not in t:
                raise SystemExit(f"[-] В задаче {t} нет поля '{key}'")
        if not NAME_RE.match(t["name"]) or len(t["name"]) > 40:
            raise SystemExit(f"[-] Некорректное имя задачи '{t['name']}' "
                             f"(строчные буквы, цифры и '-', до 40 символов)")
        if t["name"] in seen:
            raise SystemExit(f"[-] Имя задачи '{t['name']}' повторяется")
        seen.add(t["name"])
    return settings, tasks


def node_ports(settings, tasks, team, participant, task_idx):
    """Стабильные NodePort: один и тот же (team, participant, task) всегда даёт те же порты."""
    index = ((team - 1) * settings["max_participants"] + (participant - 1)) * len(tasks) + task_idx
    task_np = settings["node_port_base"] + 2 * index
    ws_np = task_np + 1
    if ws_np > 32767:
        raise SystemExit(f"[-] NodePort {ws_np} выходит за пределы 32767. "
                         f"Уменьшите число команд/участников/задач или node_port_base.")
    return task_np, ws_np


def labels(team, participant, task):
    return {"ctf/team": str(team), "ctf/participant": str(participant), "ctf/task": task}


# ---------- сборка манифестов ----------

def build_namespace(team):
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": f"team-{team}",
                     "labels": {"ctf/team": str(team), "ctf/managed": "true"}},
    }


def build_secret(name, namespace, lbls, value):
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace, "labels": lbls},
        "type": "Opaque",
        "stringData": {"secret": value},
    }


def build_deployment(task, name, namespace, team, participant, ws_node_port,
                     public_host, settings):
    lbls = labels(team, participant, task["name"])
    logs_mount = lambda path: {"name": "shared-logs", "mountPath": path}
    helper_security = {
        "runAsNonRoot": True,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    resources = task.get("resources") or {
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits": {"cpu": "250m", "memory": "128Mi"},
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace, "labels": lbls},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name, **lbls}},
                "spec": {
                    # у взломанного контейнера не должно быть токена к API кластера
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "volumes": [{"name": "shared-logs", "emptyDir": {"sizeLimit": "16Mi"}}],
                    # заранее создаём лог и делаем его доступным на запись всем
                    "initContainers": [{
                        "name": "init-logs",
                        "image": settings["helper_image"],
                        "imagePullPolicy": settings["image_pull_policy"],
                        "command": ["/bin/sh", "-c",
                                    f"touch {HELPER_LOG_DIR}/flags.log && chmod 666 {HELPER_LOG_DIR}/flags.log"],
                        "securityContext": helper_security,
                        "volumeMounts": [logs_mount(HELPER_LOG_DIR)],
                    }],
                    "containers": [
                        {
                            "name": "task",
                            "image": task["image"],
                            "imagePullPolicy": settings["image_pull_policy"],
                            "ports": [{"containerPort": task["port"]}],
                            "resources": resources,
                            "volumeMounts": [logs_mount(TASK_LOG_DIR)],
                        },
                        {
                            "name": "helper",
                            "image": settings["helper_image"],
                            "imagePullPolicy": settings["image_pull_policy"],
                            "ports": [{"containerPort": WS_PORT}],
                            "env": [
                                {"name": "TEAM", "value": str(team)},
                                {"name": "PARTICIPANT", "value": str(participant)},
                                {"name": "TASK_NAME", "value": task["name"]},
                                {"name": "SECRET", "valueFrom": {
                                    "secretKeyRef": {"name": name, "key": "secret"}}},
                                {"name": "CHECKER_URL", "value": settings["checker_url"] or ""},
                                {"name": "PUBLIC_HOST", "value": public_host},
                                {"name": "WS_NODE_PORT", "value": str(ws_node_port)},
                                {"name": "WS_PORT", "value": str(WS_PORT)},
                                {"name": "LOG_DIR", "value": HELPER_LOG_DIR},
                            ],
                            "resources": {"limits": {"cpu": "200m", "memory": "128Mi"}},
                            "securityContext": helper_security,
                            "volumeMounts": [logs_mount(HELPER_LOG_DIR)],
                        },
                    ],
                },
            },
        },
    }


def build_service(task, name, namespace, team, participant, task_np, ws_np):
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace,
                     "labels": labels(team, participant, task["name"])},
        "spec": {
            "type": "NodePort",
            "selector": {"app": name},
            "ports": [
                {"name": "task", "port": task["port"], "targetPort": task["port"], "nodePort": task_np},
                {"name": "ws", "port": WS_PORT, "targetPort": WS_PORT, "nodePort": ws_np},
            ],
        },
    }


def build_network_policies(namespace, settings, task_ports):
    """Ingress: свой namespace + внешний трафик на порты задач/WebSocket, но не из подов других команд.
    Egress: свой namespace + DNS (+ чекер, + интернет без частных сетей)."""
    pod_cidr = settings["pod_cidr"]
    tcp = lambda p: {"protocol": "TCP", "port": p}

    ingress = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "ctf-ingress", "namespace": namespace},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress"],
            "ingress": [
                {"from": [{"podSelector": {}}]},
                {"from": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": [pod_cidr]}}],
                 "ports": [tcp(p) for p in sorted(set(task_ports) | {WS_PORT})]},
            ],
        },
    }

    dns_to = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}
    egress_rules = [
        {"to": [{"podSelector": {}}]},
        {"to": [dns_to], "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]},
    ]
    if settings["checker_namespace"]:
        egress_rules.append({"to": [{"namespaceSelector": {"matchLabels": {
            "kubernetes.io/metadata.name": settings["checker_namespace"]}}}]})
    if settings["internet_egress"]:
        excluded = list(PRIVATE_NETS)
        net = ipaddress.ip_network(pod_cidr)
        if not any(net.subnet_of(ipaddress.ip_network(p)) for p in PRIVATE_NETS):
            excluded.append(pod_cidr)
        egress_rules.append({"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": excluded}}]})

    egress = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "ctf-egress", "namespace": namespace},
        "spec": {"podSelector": {}, "policyTypes": ["Egress"], "egress": egress_rules},
    }
    return [ingress, egress]


# ---------- применение ----------

def apply(create, patch, name, namespace, manifest):
    """Создать, а если уже существует - обновить."""
    try:
        return create(namespace=namespace, body=manifest)
    except ApiException as e:
        if e.status != 409:
            raise
        return patch(name=name, namespace=namespace, body=manifest)


def detect_host(core_v1):
    nodes = core_v1.list_node().items
    for kind in ("ExternalIP", "InternalIP"):
        for n in nodes:
            for a in n.status.addresses or []:
                if a.type == kind:
                    return a.address
    raise SystemExit("[-] Не удалось определить IP ноды, укажите --public-host")


def deploy_team(team, participants, all_tasks, selected, settings, public_host):
    core_v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    net_v1 = client.NetworkingV1Api()
    namespace = f"team-{team}"

    try:
        core_v1.create_namespace(build_namespace(team))
        print(f"[+] Namespace '{namespace}' создан")
    except ApiException as e:
        if e.status != 409:
            raise
        print(f"[=] Namespace '{namespace}' уже есть")

    for pol in build_network_policies(namespace, settings, [t["port"] for t in all_tasks]):
        apply(net_v1.create_namespaced_network_policy, net_v1.patch_namespaced_network_policy,
              pol["metadata"]["name"], namespace, pol)
    print("[+] NetworkPolicy применены")

    rows = []
    for p in range(1, participants + 1):
        for task in selected:
            idx = next(i for i, t in enumerate(all_tasks) if t["name"] == task["name"])
            task_np, ws_np = node_ports(settings, all_tasks, team, p, idx)
            name = f"{task['name']}-p{p}"

            # секрет создаём один раз: при передеплое токены участников не меняются
            try:
                core_v1.create_namespaced_secret(namespace, build_secret(
                    name, namespace, labels(team, p, task["name"]), secrets.token_hex(16)))
            except ApiException as e:
                if e.status != 409:
                    raise

            apply(apps_v1.create_namespaced_deployment, apps_v1.patch_namespaced_deployment,
                  name, namespace,
                  build_deployment(task, name, namespace, team, p, ws_np, public_host, settings))
            apply(core_v1.create_namespaced_service, core_v1.patch_namespaced_service,
                  name, namespace,
                  build_service(task, name, namespace, team, p, task_np, ws_np))
            rows.append((p, task["name"], task_np, ws_np))

    print(f"\nКоманда {team}, адреса для участников:")
    for p, tname, task_np, ws_np in rows:
        print(f"  участник {p} / {tname}: задача http://{public_host}:{task_np}   "
              f"WebSocket ws://{public_host}:{ws_np}")


def main():
    parser = argparse.ArgumentParser(description="Deploy CTF team namespace to Kubernetes")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="YAML с настройками и задачами")
    parser.add_argument("--team", type=int, required=True, help="номер команды (>=1)")
    parser.add_argument("--participants", type=int, help="число участников (по умолчанию max_participants)")
    parser.add_argument("--task", help="задеплоить только одну задачу по имени")
    parser.add_argument("--public-host", default=os.environ.get("PUBLIC_HOST"),
                        help="IP/домен ноды для инструкции участника (по умолчанию IP первой ноды)")
    args = parser.parse_args()

    settings, tasks = load_config(args.config)
    if args.team < 1:
        raise SystemExit("[-] --team должен быть >= 1")
    participants = args.participants or settings["max_participants"]
    if not 1 <= participants <= settings["max_participants"]:
        raise SystemExit(f"[-] --participants должен быть от 1 до {settings['max_participants']}")

    selected = [t for t in tasks if not args.task or t["name"] == args.task]
    if not selected:
        raise SystemExit(f"[-] Задача '{args.task}' не найдена в {args.config}")

    config.load_kube_config()
    public_host = args.public_host or detect_host(client.CoreV1Api())
    deploy_team(args.team, participants, tasks, selected, settings, public_host)


if __name__ == "__main__":
    main()
