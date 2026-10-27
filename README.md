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

1. Находит уязвимость в своей задаче (уровни 0-5, см. `lvl/README.txt`).
2. Как только задача пишет `[FLAG_SUCCESS]` в `/app/logs/flags.log`, sidecar создаёт рядом `flag.txt`
   (инструкция для человека) и `flag.json` (адрес WebSocket и токен для кнопки).
3. В задаче сама появляется плашка «Уязвимость найдена» с кнопкой **«Сдать задачу»**
   (в течение пары секунд, обновлять страницу не нужно). Клик открывает WebSocket к sidecar,
   отправляет токен и получает `{"status": "solved"}`; плашка меняется на «Задача сдана».
   Состояние хранится в sidecar, после перезагрузки страницы плашка покажет «уже сдано».
4. Дальше организатор переключает участника на следующий уровень.

Без кнопки то же самое можно сделать вручную (например, прочитав `flag.txt` через эксплойт):
`python3 pipeline/ws_check.py ws://<IP>:<порт> <токен>`.

## Как добавить задачу

1. Добавьте её в `pipeline/tasks.yaml` (`name`, `image`, `port`).
2. Образ задачи должен писать строку с `[FLAG_SUCCESS]` в `/app/logs/flags.log`, когда её решили.
   Каталог `/app/logs` монтируется автоматически и должен быть доступен на запись пользователю контейнера.
3. Подключите кнопку «Сдать задачу» (общий код лежит в `common/`, `make build-levels` копирует его в контекст сборки):
   - **Flask:** `from common.solve_flask import register_solve` и `register_solve(app)` после создания `app`;
     в Dockerfile `COPY --chown=... common/ ./common/`. Шаблоны менять не нужно: скрипт вставляется в каждый HTML-ответ.
   - **nginx:** `location = /__flag.json { alias /app/logs/flag.json; }`, `sub_filter '</body>' '<script src="/__solve.js"></script></body>';`
     и копирование `common/solve.js` в `/usr/share/nginx/html/__solve.js` (пример: `lvl/level_1`).
   - **Другое:** отдавайте `/app/logs/flag.json` по пути `/__flag.json`, а `common/solve.js` по `/__solve.js`
     и добавьте на страницы `<script src="/__solve.js"></script>`.
4. `make deploy TEAM=<N> TASK=<name>`.

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
