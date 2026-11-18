# Relais Vox (vox.vled.fr) : page du téléphone + canal WebSocket.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    VOX_RELAY_WEB=/app/web

WORKDIR /app
COPY pyproject.toml ./
COPY vox_relay ./vox_relay
RUN pip install --no-cache-dir . && useradd --system --uid 10001 vox
COPY web ./web

USER vox
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"
CMD ["python", "-m", "vox_relay"]
