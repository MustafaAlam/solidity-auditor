# One image, many services. The agent's identity comes from AGENT_ID at runtime,
# so Cloud Run deploys N services from a single build rather than N images.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY service/pyproject.toml service/README.md ./
COPY service/audit_mas ./audit_mas

# The agent prompts live in references/, outside the package, because the skill
# and the service must read the same files. Copy them in.
COPY references ../references

RUN pip install --no-cache-dir '.[server,anthropic]'

ENV AGENT_ID=access-control AGENT_ROLE=lane MODEL_TIER=deep PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import httpx,os; httpx.get(f'http://localhost:{os.environ.get(\"PORT\",8080)}/healthz').raise_for_status()"

CMD exec uvicorn audit_mas.a2a_server:app --host 0.0.0.0 --port ${PORT}
