.PHONY: setup build deploy check status clean

PY ?= python3
TEAM ?=
PARTICIPANT ?=
PARTICIPANTS ?=
TASK ?=

setup:
	$(PY) -m pip install -r requirements.txt

build:
	docker build -t ctf-sidecar:1 sidecar/
	docker build -t challenge-web:level-1 target-level1/
	@echo "Загрузите образы на ноды кластера: kind load docker-image / minikube image load / k3s ctr images import"

deploy:
	@test -n "$(TEAM)" || (echo "Укажите TEAM=<номер команды>"; exit 1)
	$(PY) pipeline/deploy.py --team $(TEAM) $(if $(PARTICIPANTS),--participants $(PARTICIPANTS)) $(if $(TASK),--task $(TASK))

check:
	@test -n "$(TEAM)" || (echo "Укажите TEAM=<номер команды>"; exit 1)
	$(PY) pipeline/checker.py --team $(TEAM) $(if $(PARTICIPANT),--participant $(PARTICIPANT))

status:
	@test -n "$(TEAM)" || (echo "Укажите TEAM=<номер команды>"; exit 1)
	kubectl -n team-$(TEAM) get pods,svc -o wide

clean:
	@test -n "$(TEAM)" || (echo "Укажите TEAM=<номер команды>"; exit 1)
	$(PY) pipeline/clean.py --team $(TEAM) $(if $(PARTICIPANT),--participant $(PARTICIPANT))
