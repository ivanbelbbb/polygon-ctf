.PHONY: setup build build-sidecar build-levels load deploy check status clean

PY ?= ./venv/bin/python3
TEAM ?=
PARTICIPANT ?=
PARTICIPANTS ?=
TASK ?=
# номера уровней из lvl/level_N (образы challenge-web:level-N)
LEVELS ?= 0 1 2 3 4 5
# ssh-адрес worker для make load, например WORKER=root@2.27.35.202
WORKER ?=

setup:
	$(PY) -m pip install -r requirements.txt

build: build-sidecar build-levels
	@echo "Образы собраны. Загрузите их на worker: make load WORKER=root@<ip>"

build-sidecar:
	docker build -t ctf-sidecar:1 sidecar/

build-levels:
	@for n in $(LEVELS); do \
		echo "== challenge-web:level-$$n"; \
		docker build -t challenge-web:level-$$n lvl/level_$$n/ || exit 1; \
	done

# Передать образы в containerd на worker (kubeadm): docker save | ctr import
load:
	@test -n "$(WORKER)" || (echo "Укажите WORKER=root@<ip worker>"; exit 1)
	docker save ctf-sidecar:1 $(foreach n,$(LEVELS),challenge-web:level-$(n)) \
		| ssh $(WORKER) 'ctr -n k8s.io images import -'

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
