"""Operator для CTF-полигона: каждый участник проходит уровни независимо.

ParticipantChallenge (namespace team-N, одна CR на участника) хранит status.currentLevel.
Этот Operator следит за изменением currentLevel и приводит в соответствие Deployment/Service/
Secret участника: образ задачи нужного уровня, те же NodePort (адрес участника не меняется
между уровнями), новый токен для sidecar (старый перестаёт подходить сразу после смены уровня).

Кто меняет currentLevel:
  - checker (ctf-rating/checker/app.py) - сразу после того, как участник прислал верный токен
    своего ТЕКУЩЕГО уровня;
  - weekly_reset.py (CronJob по понедельникам) - откатывает всех к spec.baseLevel.
Operator сам currentLevel никогда не меняет - только читает и разворачивает инфраструктуру.
"""
import datetime
import logging
import os
import secrets
import sys

import kopf
from kubernetes import client
from kubernetes.client.rest import ApiException

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))
from manifests import (  # noqa: E402
    DEFAULT_CONFIG, apply, build_deployment, build_secret, build_service, load_config,
    participant_node_ports, task_by_level,
)

GROUP = "ctf.example.org"
VERSION = "v1"
PLURAL = "participantchallenges"
LEVEL_ANNOTATION = "ctf.example.org/level"

SETTINGS, TASKS = load_config(os.environ.get("TASKS_CONFIG", DEFAULT_CONFIG))
# Operator не ходит в API кластера за IP ноды (как это делает deploy.py): адрес участника
# не должен тихо смениться при пересборке пода на другом уровне, поэтому задаём его явно.
PUBLIC_HOST = os.environ["PUBLIC_HOST"]


def week_start_iso(now=None):
    now = now or datetime.datetime.utcnow()
    monday = now - datetime.timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


@kopf.on.create(GROUP, VERSION, PLURAL)
def initialize(spec, status, patch, logger, **_):
    """При создании CR выставляет status.currentLevel = spec.baseLevel. Саму пересборку
    Deployment/Service/Secret делает on_level_change() - он сработает сам, когда увидит,
    что currentLevel поменялся с None на это значение."""
    if status.get("currentLevel") is None:
        base = spec.get("baseLevel", 0)
        patch.status["currentLevel"] = base
        patch.status["weekStart"] = week_start_iso()
        logger.info(f"currentLevel инициализирован: {base}")


def reconcile_pod(spec, status, namespace, name, body, logger=None):
    """Приводит Deployment/Service/Secret участника в соответствие со status.currentLevel.
    Идемпотентна: повторный вызов с тем же уровнем не создаёт новый токен и не делает лишних
    запросов к API, кроме стандартного create-or-patch (тело манифеста то же самое)."""
    log = logger or logging.getLogger("ctf-operator")
    core_v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()

    level = status.get("currentLevel")
    if level is None:
        return  # ещё не инициализирован - initialize() только что это запустит через on.field

    max_level = spec.get("maxLevel", SETTINGS["max_level"])
    level = max(spec.get("baseLevel", 0), min(level, max_level))
    task = task_by_level(TASKS, SETTINGS, level)
    if task is None:
        log.error(f"{namespace}/{name}: нет задачи для уровня {level} "
                  f"(ожидалось '{SETTINGS['task_prefix']}{level}' в tasks.yaml) - под не трогаю")
        return

    team, participant = spec["team"], spec["participant"]
    task_np, ws_np = participant_node_ports(SETTINGS, team, participant)
    extra_labels = {"ctf/level": str(level)}
    secret_name = f"{name}-token"

    # Токен перевыпускаем, только если уровень реально сменился с прошлого реконсайла -
    # иначе 60-секундный таймер-самолечение выдавал бы участнику новый токен каждую минуту.
    need_new_token = True
    try:
        existing = core_v1.read_namespaced_secret(secret_name, namespace)
        if (existing.metadata.annotations or {}).get(LEVEL_ANNOTATION) == str(level):
            need_new_token = False
    except ApiException as e:
        if e.status != 404:
            raise

    if need_new_token:
        secret_manifest = build_secret(secret_name, namespace, extra_labels, secrets.token_hex(16))
        secret_manifest["metadata"].setdefault("annotations", {})[LEVEL_ANNOTATION] = str(level)
        kopf.append_owner_reference([secret_manifest], owner=body)
        apply(core_v1.create_namespaced_secret, core_v1.patch_namespaced_secret,
              secret_name, namespace, secret_manifest)

    dep_manifest = build_deployment(task, name, namespace, team, participant, ws_np,
                                    PUBLIC_HOST, SETTINGS, extra_labels)
    kopf.append_owner_reference([dep_manifest], owner=body)
    apply(apps_v1.create_namespaced_deployment, apps_v1.patch_namespaced_deployment,
          name, namespace, dep_manifest)

    svc_manifest = build_service(task, name, namespace, team, participant, task_np, ws_np, extra_labels)
    kopf.append_owner_reference([svc_manifest], owner=body)
    apply(core_v1.create_namespaced_service, core_v1.patch_namespaced_service,
          name, namespace, svc_manifest)

    log.info(f"{namespace}/{name}: уровень {level} ({task['name']}), NodePort {task_np}/{ws_np}, "
            f"токен {'обновлён' if need_new_token else 'прежний'}")


@kopf.on.field(GROUP, VERSION, PLURAL, field="status.currentLevel")
def on_level_change(spec, status, namespace, name, body, logger, **_):
    reconcile_pod(spec, status, namespace, name, body, logger=logger)


@kopf.timer(GROUP, VERSION, PLURAL, interval=60, initial_delay=15)
def self_heal(spec, status, namespace, name, body, logger, **_):
    """Подстраховка: если Deployment/Service/Secret пропали вручную (kubectl delete),
    восстанавливает их, не дожидаясь следующего решения задачи или сброса недели."""
    reconcile_pod(spec, status, namespace, name, body, logger=logger)
