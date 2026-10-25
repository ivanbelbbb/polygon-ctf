FROM python:3.12-slim

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/logs /data \
    && chown -R appuser:appuser /app /data

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV LOG_DIR=/app/logs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && chmod -R o+rX /ms-playwright

COPY --chown=appuser:appuser app.py .
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser static/ ./static/

USER appuser

EXPOSE 5000

CMD ["python", "app.py"]