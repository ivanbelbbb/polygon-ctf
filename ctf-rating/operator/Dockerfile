# Собирается с контекстом РЕПО (корень), а не этой папки - нужен доступ к pipeline/manifests.py:
#   docker build -f ctf-rating/operator/Dockerfile -t ctf-operator:1 .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --create-home --uid 10004 appuser

COPY ctf-rating/operator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser pipeline/manifests.py pipeline/tasks.yaml ./pipeline/
COPY --chown=appuser:appuser ctf-rating/operator/operator.py ctf-rating/operator/weekly_reset.py ./

ENV TASKS_CONFIG=/app/pipeline/tasks.yaml
USER 10004

# weekly-reset CronJob переопределяет command на: python weekly_reset.py
CMD ["kopf", "run", "-A", "--standalone", "operator.py"]
