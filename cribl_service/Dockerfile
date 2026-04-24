# Build context must be the project root so the shared utility files
# (cribl_api.py, cribl_config.py, cribl_utils.py, cribl_logger.py)
# can be COPYd in alongside the service code.
#
# docker-compose sets:  build: { context: ., dockerfile: cribl_service/Dockerfile }
# Manual build example: docker build -f cribl_service/Dockerfile -t cribl-service .

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Dependencies (separate layer for cache efficiency) ───────────────────────
COPY cribl_service/requirements.txt ./cribl_service/requirements.txt
RUN pip install --no-cache-dir -r cribl_service/requirements.txt \
    && find /usr/local/lib -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true

# ── Shared utility modules from project root ─────────────────────────────────
COPY cribl_api.py    ./cribl_api.py
COPY cribl_config.py ./cribl_config.py
COPY cribl_logger.py ./cribl_logger.py
COPY cribl_utils.py  ./cribl_utils.py

# ── Service application code ─────────────────────────────────────────────────
COPY cribl_service/ ./cribl_service/

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

# Module path: /app is WORKDIR and in sys.path, cribl_service/ is a package
CMD ["uvicorn", "cribl_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
