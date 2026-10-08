# API + UI + parser + GnuCOBOL verification. The LLM runs in a separate Ollama container.
FROM python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends gnucobol \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts
COPY data/synthetic ./data/synthetic
COPY data/gold ./data/gold
COPY data/specs ./data/specs
COPY data/public ./data/public
COPY data/splits.json ./data/splits.json

ENV COBOL_BACKEND=native \
    OLLAMA_URL=http://ollama:11434 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
