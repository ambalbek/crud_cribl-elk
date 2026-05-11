# Build context must be the project root so the shared utility files
# (role_rm.py, cribl_utils.py, cribl_api.py, cribl_config.py, cribl_logger.py)
# can be COPYd in alongside the service code.
#
# docker-compose sets:  build: { context: ., dockerfile: ece_service/Dockerfile }
# Manual build example: docker build -f ece_service/Dockerfile -t ece-service .

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Dependencies (separate layer for cache efficiency) ───────────────────────
COPY ece_service/requirements.txt ./ece_service/requirements.txt
RUN pip install --no-cache-dir -r ece_service/requirements.txt \
    && find /usr/local/lib -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true

# ── Shared utility modules from project root ─────────────────────────────────
# role_rm.py is the primary module being wrapped; it imports the cribl_* modules
COPY role_rm.py      ./role_rm.py
COPY cribl_api.py    ./cribl_api.py
COPY cribl_config.py ./cribl_config.py
COPY cribl_logger.py ./cribl_logger.py
COPY cribl_utils.py  ./cribl_utils.py
COPY otel_setup.py   ./otel_setup.py

# ── Service application code ─────────────────────────────────────────────────
COPY ece_service/ ./ece_service/

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')" || exit 1

# Module path: /app is WORKDIR and in sys.path, ece_service/ is a package
CMD ["uvicorn", "ece_service.main:app", "--host", "0.0.0.0", "--port", "8002"]
