FROM --platform=linux/amd64 python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    requests \
    azure-storage-blob

COPY get_apmids_from_blob.py .

ENTRYPOINT ["python", "get_apmids_from_blob.py"]
