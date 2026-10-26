"""Удаление команды (весь namespace) или одного участника.

  python3 clean.py --team 1                  # весь namespace team-1
  python3 clean.py --team 1 --participant 2  # только участник 2 (ParticipantChallenge CR;
                                              #  Deployment/Service/Secret участника Kubernetes
                                              #  удалит сам через ownerReferences, которые на них
                                              #  ставит Operator)
"""
import argparse

from kubernetes import client, config
from kubernetes.client.rest import ApiException

GROUP = "ctf.example.org"
VERSION = "v1"
PLURAL = "participantchallenges"


def ignore_404(fn, what):
    try:
        fn()
        print(f"[+] {what} удалён")
    except ApiException as e:
        if e.status == 404:
            print(f"[=] {what} не найден - пропускаю")
        else:
            print(f"[-] Ошибка удаления {what}: {e.reason}")


def main():
    parser = argparse.ArgumentParser(description="Remove CTF team or participant from Kubernetes")
    parser.add_argument("--team", type=int, required=True)
    parser.add_argument("--participant", type=int, help="удалить только этого участника")
    args = parser.parse_args()

    config.load_kube_config()
    core_v1 = client.CoreV1Api()
    custom_api = client.CustomObjectsApi()
    namespace = f"team-{args.team}"

    if args.participant is None:
        ignore_404(lambda: core_v1.delete_namespace(namespace), f"Namespace '{namespace}'")
        print("Удаление namespace идёт в фоне, подождите пару десятков секунд.")
        return

    name = f"p{args.participant}"
    ignore_404(lambda: custom_api.delete_namespaced_custom_object(
        GROUP, VERSION, namespace, PLURAL, name), f"ParticipantChallenge '{name}'")
    print("Deployment/Service/Secret этого участника Kubernetes удалит сам (owner references).")


if __name__ == "__main__":
    main()
