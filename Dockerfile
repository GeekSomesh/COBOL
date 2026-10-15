# API + UI + parser + GnuCOBOL verification. The LLM runs in a separate Ollama container.
FROM python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends gnucobol \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 cobol

WORKDIR /app
COPY requirements.lock requirements.txt ./
# hash-pinned install when the lock file is present (scripts: pip-compile --generate-hashes)
RUN if [ -s requirements.lock ]; then pip install --no-cache-dir --require-hashes -r requirements.lock; \
    else pip install --no-cache-dir -r requirements.txt; fi

COPY src ./src
COPY scripts ./scripts
COPY data/synthetic ./data/synthetic
COPY data/gold ./data/gold
COPY data/gold_public ./data/gold_public
COPY data/specs ./data/specs
COPY data/public ./data/public
COPY data/splits.json ./data/splits.json
RUN mkdir -p /app/data/state /app/data/cache /app/data/uploads /app/data/jcl /app/data/backups \
 && chown -R cobol:cobol /app/data

USER cobol
ENV COBOL_BACKEND=native \
    OLLAMA_URL=http://ollama:11434 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=4).status == 200 else 1)"
CMD ["python", "scripts/serve.py", "--host", "0.0.0.0", "--port", "8000"]
