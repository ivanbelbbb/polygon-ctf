"""Еженедельный сброс прогресса: патчит status.currentLevel = spec.baseLevel у ВСЕХ
ParticipantChallenge во всех namespace team-*. Пересборку подов (новый уровень, новый токен)
делает Operator - он следит за изменением currentLevel и реагирует сам, этому скрипту
для этого ничего дополнительно делать не нужно.

Запускается как Job по расписанию CronJob (ctf-rating/k8s/weekly-reset-cronjob.yaml),
в поде с правами ServiceAccount 'ctf-weekly-reset' (см. ctf-rating/k8s/rbac.yaml).
"""
import datetime
import sys

from kubernetes import client, config
from kubernetes.client.rest import ApiException

GROUP = "ctf.example.org"
VERSION = "v1"
PLURAL = "participantchallenges"


def week_start_iso(now=None):
    now = now or datetime.datetime.utcnow()
    monday = now - datetime.timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


def main():
    config.load_incluster_config()
    custom_api = client.CustomObjectsApi()
    week = week_start_iso()

    items = custom_api.list_cluster_custom_object(GROUP, VERSION, PLURAL).get("items", [])
    changed = 0
    for cr in items:
        meta = cr["metadata"]
        spec = cr.get("spec", {})
        status = cr.get("status", {})
        base = spec.get("baseLevel", 0)
        if status.get("currentLevel") == base and status.get("weekStart") == week:
            continue  # уже на базовом уровне и неделя уже отмечена - пропускаем
        patch = {"status": {"currentLevel": base, "weekStart": week}}
        try:
            custom_api.patch_namespaced_custom_object_status(
                GROUP, VERSION, meta["namespace"], PLURAL, meta["name"], patch)
            changed += 1
            print(f"[+] {meta['namespace']}/{meta['name']}: уровень -> {base}")
        except ApiException as e:
            print(f"[-] {meta['namespace']}/{meta['name']}: {e.status} {e.reason}", file=sys.stderr)

    print(f"Сброшено участников: {changed} из {len(items)}")


if __name__ == "__main__":
    main()
