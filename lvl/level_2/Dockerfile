FROM python:3.12-slim

WORKDIR /app

ENV LOG_DIR=/app/logs

RUN useradd --create-home --uid 10001 appuser \
    &&  mkdir -p /app/logs \
    && chown  -R appuser:appuser /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser app.py .
COPY --chown=appuser:appuser common/ ./common/
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser static/ ./static/

USER appuser

EXPOSE 5000

CMD ["python", "app.py"]
