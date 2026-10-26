"""Деплой CTF-полигона в Kubernetes.

Схема (после введения Operator):
  namespace team-<N>                       одна команда (3-4 участника)
    ParticipantChallenge p<i>               CR участника i - его текущий уровень
    (Deployment/Service/Secret p<i>...      создаёт и обновляет Operator, см. ctf-rating/operator)
    NetworkPolicy ctf-ingress / ctf-egress  изоляция от других команд

Этот скрипт больше не создаёт Deployment/Service сам - только CR. Дальше Operator разворачивает
под нужного уровня, а при решении задачи чекер поднимает currentLevel в CR, Operator пересоздаёт
под со следующим уровнем и новым токеном.

Как засчитывается задача - без изменений:
  - задача пишет строку с [FLAG_SUCCESS] в /app/logs/flags.log
  - sidecar видит её и кладёт в /app/logs/flag.txt ИНСТРУКЦИЮ вместо флага
  - участник подключается к sidecar по WebSocket и шлёт токен из инструкции
"""
import argparse
import os
import sys

from kubernetes import client, config
from kubernetes.client.rest import ApiException

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifests import (  # noqa: E402
    DEFAULT_CONFIG, apply, build_namespace, build_network_policies, build_operator_role_binding,
    build_secret, detect_host, labels, load_config,
)

GROUP = "ctf.example.org"
VERSION = "v1"
PLURAL = "participantchallenges"


def build_participant_challenge(team, participant, settings):
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "ParticipantChallenge",
        "metadata": {
            "name": f"p{participant}",
            "namespace": f"team-{team}",
            "labels": labels(team, participant, "progression"),
        },
        "spec": {
            "team": team,
            "participant": participant,
            "track": "web",
            "baseLevel": settings["base_level"],
            "maxLevel": settings["max_level"],
        },
    }


def apply_custom(custom_api, name, namespace, manifest):
    try:
        return custom_api.create_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, manifest)
    except ApiException as e:
        if e.status != 409:
            raise
        # CR уже есть - не трогаем spec/status, чтобы не сбросить прогресс участника
        return custom_api.get_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name)


def deploy_team(team, participants, all_tasks, settings, public_host):
    core_v1 = client.CoreV1Api()
    net_v1 = client.NetworkingV1Api()
    rbac_v1 = client.RbacAuthorizationV1Api()
    custom_api = client.CustomObjectsApi()
    namespace = f"team-{team}"

    try:
        core_v1.create_namespace(build_namespace(team))
        print(f"[+] Namespace '{namespace}' создан")
    except ApiException as e:
        if e.status != 409:
            raise
        print(f"[=] Namespace '{namespace}' уже есть")

    rb = build_operator_role_binding(namespace)
    apply(rbac_v1.create_namespaced_role_binding, rbac_v1.patch_namespaced_role_binding,
          rb["metadata"]["name"], namespace, rb)

    if os.environ.get("CHECKER_KEY"):
        try:
            core_v1.create_namespaced_secret(namespace, build_secret(
                "checker-key", namespace, {"ctf/team": str(team)}, os.environ["CHECKER_KEY"]))
        except ApiException as e:
            if e.status != 409:
                raise

    for pol in build_network_policies(namespace, settings, [t["port"] for t in all_tasks]):
        apply(net_v1.create_namespaced_network_policy, net_v1.patch_namespaced_network_policy,
              pol["metadata"]["name"], namespace, pol)
    print("[+] NetworkPolicy применены")

    for p in range(1, participants + 1):
        apply_custom(custom_api, f"p{p}", namespace, build_participant_challenge(team, p, settings))
    print(f"[+] ParticipantChallenge создан/есть для участников 1..{participants}")
    print(f"    Operator развернёт поды в течение нескольких секунд (образ нужного уровня должен")
    print(f"    быть уже загружен на worker). Адреса и уровень каждого участника:")
    print(f"    kubectl -n {namespace} get participantchallenges -o wide")
    print(f"    kubectl -n {namespace} get svc   # колонка PORT(S): <containerPort>:<NodePort>, "
          f"публичный адрес - http://{public_host}:<NodePort>")


def main():
    parser = argparse.ArgumentParser(description="Deploy CTF team namespace to Kubernetes")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="YAML с настройками и задачами")
    parser.add_argument("--team", type=int, required=True, help="номер команды (>=1)")
    parser.add_argument("--participants", type=int, help="число участников (по умолчанию max_participants)")
    parser.add_argument("--public-host", default=os.environ.get("PUBLIC_HOST"),
                        help="IP/домен ноды для инструкции участника (по умолчанию IP первой ноды)")
    args = parser.parse_args()

    settings, tasks = load_config(args.config)
    if args.team < 1:
        raise SystemExit("[-] --team должен быть >= 1")
    participants = args.participants or settings["max_participants"]
    if not 1 <= participants <= settings["max_participants"]:
        raise SystemExit(f"[-] --participants должен быть от 1 до {settings['max_participants']}")

    config.load_kube_config()
    public_host = args.public_host or detect_host(client.CoreV1Api())
    deploy_team(args.team, participants, tasks, settings, public_host)


if __name__ == "__main__":
    main()
