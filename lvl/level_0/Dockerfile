FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser  \
    && mkdir -p /app/logs \
    && chown appuser:appuser /app/logs

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser app.py .
COPY --chown=appuser:appuser common/ ./common/

USER appuser

EXPOSE 5000

CMD [ "python", "app.py" ]