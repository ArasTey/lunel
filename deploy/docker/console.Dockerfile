# Lunel Console — API + frontend (single service, the public endpoint)
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LUNEL_CONSOLE_PORT=8080

WORKDIR /app

COPY console/api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY console/api/lunel_console ./api/lunel_console
COPY console/frontend ./frontend

RUN useradd --uid 10001 --shell /usr/sbin/nologin lunel \
    && mkdir -p /data && chown lunel:lunel /data
USER 10001

EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=4s --start-period=8s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status==200 else 1)"

WORKDIR /app/api
CMD ["python", "-m", "lunel_console"]
