.PHONY: setup build build-sidecar build-levels build-operator load deploy-infra deploy check status clean

PY ?= python3
TEAM ?=
PARTICIPANT ?=
PARTICIPANTS ?=
# номера уровней из lvl/level_N (образы challenge-web:level-N)
LEVELS ?= 0 1 2 3 4 5
# ssh-адрес worker для make load, например WORKER=root@2.27.35.202
WORKER ?=
# имя ноды worker В KUBERNETES (kubectl get nodes), для деплоя Operator на неё же
WORKER_NODE ?=
# публичный IP/домен ноды - пишется в инструкции участникам, не меняется между уровнями
PUBLIC_HOST ?=

setup:
	$(PY) -m pip install -r requirements.txt

build: build-sidecar build-levels build-operator
	@echo "Образы собраны. Загрузите их на worker: make load WORKER=root@<ip>"

build-sidecar:
	docker build -t ctf-sidecar:1 sidecar/

# common/ (кнопка «Сдать задачу») копируется в контекст каждого уровня на время сборки
build-levels:
	@for n in $(LEVELS); do \
		echo "== challenge-web:level-$$n"; \
		rm -rf lvl/level_$$n/common; cp -r common lvl/level_$$n/common; \
		docker build -t challenge-web:level-$$n lvl/level_$$n/; rc=$$?; \
		rm -rf lvl/level_$$n/common; \
		[ $$rc -eq 0 ] || exit $$rc; \
	done

# контекст - корень репо (нужен доступ к pipeline/manifests.py), поэтому -f, а не cd в папку
build-operator:
	docker build -f ctf-rating/operator/Dockerfile -t ctf-operator:1 .

# Передать образы в containerd на worker (kubeadm): docker save | ctr import
load:
	@test -n "$(WORKER)" || (echo "Укажите WORKER=root@<ip worker>"; exit 1)
	docker save ctf-sidecar:1 ctf-operator:1 $(foreach n,$(LEVELS),challenge-web:level-$(n)) \
		| ssh $(WORKER) 'ctr -n k8s.io images import -'

# CRD + RBAC + Deployment Operator'а + CronJob сброса. Один раз на кластер, не на команду.
deploy-infra:
	@test -n "$(WORKER_NODE)" || (echo "Укажите WORKER_NODE=<имя ноды, см. kubectl get nodes>"; exit 1)
	@test -n "$(PUBLIC_HOST)" || (echo "Укажите PUBLIC_HOST=<IP worker>"; exit 1)
	kubectl apply -f ctf-rating/crd/participantchallenge.yaml
	kubectl apply -f ctf-rating/k8s/rbac.yaml
	sed -e 's/WORKER_NODE/$(WORKER_NODE)/' -e 's/PUBLIC_HOST_PLACEHOLDER/$(PUBLIC_HOST)/' \
		ctf-rating/k8s/operator.yaml | kubectl apply -f -

deploy:
	@test -n "$(TEAM)" || (echo "Укажите TEAM=<номер команды>"; exit 1)
	$(PY) pipeline/deploy.py --team $(TEAM) $(if $(PARTICIPANTS),--participants $(PARTICIPANTS))

check:
	@test -n "$(TEAM)" || (echo "Укажите TEAM=<номер команды>"; exit 1)
	$(PY) pipeline/checker.py --team $(TEAM) $(if $(PARTICIPANT),--participant $(PARTICIPANT))

status:
	@test -n "$(TEAM)" || (echo "Укажите TEAM=<номер команды>"; exit 1)
	kubectl -n team-$(TEAM) get participantchallenges,pods,svc -o wide

clean:
	@test -n "$(TEAM)" || (echo "Укажите TEAM=<номер команды>"; exit 1)
	$(PY) pipeline/clean.py --team $(TEAM) $(if $(PARTICIPANT),--participant $(PARTICIPANT))
