"""Удаление команды (весь namespace) или одного участника.

  python3 clean.py --team 1                  # весь namespace team-1
  python3 clean.py --team 1 --participant 2  # только участник 2 (Deployment, Service, Secret)
"""
import argparse

from kubernetes import client, config
from kubernetes.client.rest import ApiException


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
    apps_v1 = client.AppsV1Api()
    namespace = f"team-{args.team}"

    if args.participant is None:
        ignore_404(lambda: core_v1.delete_namespace(namespace), f"Namespace '{namespace}'")
        print("Удаление namespace идёт в фоне, подождите пару десятков секунд.")
        return

    sel = f"ctf/team={args.team},ctf/participant={args.participant}"
    # Service не поддерживает deletecollection, поэтому удаляем по одному
    for d in apps_v1.list_namespaced_deployment(namespace, label_selector=sel).items:
        ignore_404(lambda n=d.metadata.name: apps_v1.delete_namespaced_deployment(n, namespace),
                   f"Deployment '{d.metadata.name}'")
    for s in core_v1.list_namespaced_service(namespace, label_selector=sel).items:
        ignore_404(lambda n=s.metadata.name: core_v1.delete_namespaced_service(n, namespace),
                   f"Service '{s.metadata.name}'")
    for s in core_v1.list_namespaced_secret(namespace, label_selector=sel).items:
        ignore_404(lambda n=s.metadata.name: core_v1.delete_namespaced_secret(n, namespace),
                   f"Secret '{s.metadata.name}'")


if __name__ == "__main__":
    main()
