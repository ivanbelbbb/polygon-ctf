FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask==3.0.3
RUN useradd -m -u 10002 ctf && mkdir -p /app/logs && chown ctf /app/logs
WORKDIR /app
COPY app.py .
USER ctf
EXPOSE 5000
CMD ["python", "app.py"]
