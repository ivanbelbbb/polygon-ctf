FROM python:3.12-alpine
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir websockets==13.1
WORKDIR /app
COPY sidecar.py .
USER 10001
CMD ["python", "sidecar.py"]
