"""Общие функции сборки манифестов Kubernetes. Используются deploy.py, clean.py и operator.py,
чтобы под участника собирался одинаково независимо от того, кто его создаёт - CLI или Operator.
"""
import ipaddress
import os
import re

import yaml

WS_PORT = 8765
TASK_LOG_DIR = "/app/logs"
HELPER_LOG_DIR = "/logs"
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
    "task_prefix": "level",   # имя задачи = task_prefix + номер уровня, напр. level0
    "base_level": 0,
    "max_level": None,        # None -> вычисляется как максимальный N среди task_prefix+N
}


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
    if settings["max_level"] is None:
        levels = []
        for t in tasks:
            m = re.fullmatch(rf"{re.escape(settings['task_prefix'])}(\d+)", t["name"])
            if m:
                levels.append(int(m.group(1)))
        settings["max_level"] = max(levels) if levels else 0
    return settings, tasks


def task_by_level(tasks, settings, level):
    name = f"{settings['task_prefix']}{level}"
    for t in tasks:
        if t["name"] == name:
            return t
    return None


def labels(team, participant, task):
    return {"ctf/team": str(team), "ctf/participant": str(participant), "ctf/task": task}


def participant_node_ports(settings, team, participant):
    """Стабильные NodePort на участника (не на задачу): уровень меняется, порты - нет.
    Два порта на участника, поэтому диапазон хватает на гораздо больше команд, чем старая схема
    (там было по 2 порта на КАЖДУЮ задачу каждого участника)."""
    index = (team - 1) * settings["max_participants"] + (participant - 1)
    task_np = settings["node_port_base"] + 2 * index
    ws_np = task_np + 1
    if ws_np > 32767:
        raise SystemExit(f"[-] NodePort {ws_np} выходит за пределы 32767. "
                         f"Уменьшите число команд/участников или node_port_base.")
    return task_np, ws_np


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
                     public_host, settings, extra_labels=None):
    lbls = {**labels(team, participant, task["name"]), **(extra_labels or {})}
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
                                    "secretKeyRef": {"name": name + "-token", "key": "secret"}}},
                                {"name": "CHECKER_URL", "value": settings["checker_url"] or ""},
                                {"name": "PUBLIC_HOST", "value": public_host},
                                {"name": "WS_NODE_PORT", "value": str(ws_node_port)},
                                {"name": "WS_PORT", "value": str(WS_PORT)},
                                {"name": "LOG_DIR", "value": HELPER_LOG_DIR},
                                {"name": "CHECKER_KEY", "valueFrom": {"secretKeyRef": {"name": "checker-key", "key": "secret", "optional": True}}},
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


def build_service(task, name, namespace, team, participant, task_np, ws_np, extra_labels=None):
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace,
                     "labels": {**labels(team, participant, task["name"]), **(extra_labels or {})}},
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
    """Ingress: свой namespace + внешний трафик на порты задачи/WebSocket, но не из подов других команд.
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


def build_operator_role_binding(namespace, operator_sa_namespace="ctf-system",
                                operator_sa_name="ctf-operator"):
    """Даёт Operator'у права на Deployment/Service/Secret ТОЛЬКО в этом namespace команды,
    через общий ClusterRole 'ctf-operator-workloads' (см. ctf-rating/k8s/rbac.yaml).
    Это RoleBinding (не ClusterRoleBinding), поэтому права не выходят за пределы namespace -
    Operator не может создавать/менять объекты в ctf-system, kube-system и т.д."""
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": "ctf-operator-workloads", "namespace": namespace},
        "subjects": [{"kind": "ServiceAccount", "name": operator_sa_name,
                      "namespace": operator_sa_namespace}],
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                    "name": "ctf-operator-workloads"},
    }


def apply(create, patch, name, namespace, manifest):
    """Создать, а если уже существует - обновить."""
    from kubernetes.client.rest import ApiException
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
