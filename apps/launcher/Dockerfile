FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

WORKDIR /app
COPY server.py proxy.py /app/

EXPOSE 8001
CMD ["python", "/app/server.py"]
