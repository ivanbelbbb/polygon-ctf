# test-polygon

Полигон для CTF в Kubernetes. Одна команда (3–4 участника) = один namespace `team-<N>`.
У каждого участника свой под: уязвимая задача + sidecar, который выдаёт токен для засчитывания.

```
namespace team-1
├── level1-p1  (под: task + helper)  NodePort 30000 (задача) / 30001 (WebSocket)
├── level1-p2  (под: task + helper)  NodePort 30002 / 30003
├── level1-p3 ...
└── level1-p4 ...
```

## Быстрый старт

```bash
make setup                    # pip install -r requirements.txt
make build                    # собрать образы ctf-sidecar:1 и challenge-web:level-1

make deploy TEAM=1 PARTICIPANTS=4     # поднять команду 1 на 4 участника
make check  TEAM=1                    # проверить доступность портов
make status TEAM=1                    # kubectl get pods,svc
make clean  TEAM=1                    # удалить всю команду
make clean  TEAM=1 PARTICIPANT=2      # удалить только участника 2
```

`make deploy` печатает адреса каждого участника. Повторный деплой безопасен: порты и токены не меняются.

## Как участник решает задачу

1. Находит уязвимость в своей задаче (level1: command injection в `/ping?host=`).
2. Как только задача пишет `[FLAG_SUCCESS]` в `/app/logs/flags.log`, sidecar создаёт `/app/logs/flag.txt`
   с инструкцией (вместо флага).
3. Участник читает `flag.txt` через эксплойт, подключается по WebSocket на указанный адрес,
   отправляет `{"token": "..."}` и получает `{"status": "solved"}`.

Проверить вручную: `python3 pipeline/ws_check.py ws://<IP>:<порт> <токен>`.

## Как добавить задачу

1. Добавьте её в `pipeline/tasks.yaml` (`name`, `image`, `port`).
2. Образ задачи должен писать строку с `[FLAG_SUCCESS]` в `/app/logs/flags.log`, когда её решили.
   Каталог `/app/logs` монтируется автоматически и должен быть доступен на запись пользователю контейнера.
3. `make deploy TEAM=<N> TASK=<name>`.

## Изоляция

- Поды разных команд не могут обращаться друг к другу (NetworkPolicy `ctf-ingress` блокирует `pod_cidr`).
- Внутри команды поды видят друг друга.
- Исходящий трафик: свой namespace, DNS, интернет без частных сетей (`internet_egress: false` отключает интернет).
- У подов нет токена к API кластера, у sidecar не-root, read-only root FS и сброшенные capabilities.
- Секрет токена лежит в Kubernetes Secret и виден только sidecar.

## Важно

- NetworkPolicy работают только если CNI их поддерживает (Calico, Cilium, flannel+netpol; у kindnet по умолчанию нет).
- Если NodePort снаружи недоступен при включённых политиках, проверьте `pod_cidr`:
  при SNAT адрес источника может попасть в диапазон подов.
