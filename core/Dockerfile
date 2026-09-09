# Lunel Core — production image
# Non-root, distroless-adjacent: no shell tools needed at runtime.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LUNEL_STATE_PATH=/data/state.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lunel_core ./lunel_core

# Dedicated state volume
RUN mkdir -p /data && chown 65532:65532 /data

# Non-root user (fixed uid for predictable volume permissions)
USER 65532:65532

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

CMD ["python", "-m", "lunel_core"]
