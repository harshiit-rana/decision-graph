# The whole toolchain. The host needs Docker and nothing else — no Python, no psql,
# no pip, no WSL.
FROM python:3.13-slim

# psql is here for manual SQL only; `dg status` and `dg doctor` never shell out to it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

# Dependencies are baked into the image; the source is bind-mounted at /work. That split
# is deliberate — deps change rarely, so the layer stays cached, while editing the code
# takes effect immediately with no rebuild.
RUN pip install --no-cache-dir "psycopg[binary]>=3.1" "httpx>=0.27" "openai>=1.0"

ENV PYTHONPATH=/work/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /work

ENTRYPOINT ["python", "-m", "decision_graph.cli"]
CMD ["--help"]
