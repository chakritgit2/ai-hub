# Placeholder — one image serves ai-runtime, ai-gateway and ai-worker (PRD-001 §4.2, §10).
# Which role a container plays is selected by CMD/entrypoint args at deploy time (K8s manifests
# in ../k8s/ are also placeholders and don't yet set this up).
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv sync --no-dev

COPY . .

EXPOSE 8080 8081

# TODO: real entrypoint script choosing between:
#   uv run uvicorn app.main_runtime:app --host 0.0.0.0 --port 8080   (ai-runtime, port 8080)
#   uv run uvicorn app.main_runtime:app --host 0.0.0.0 --port 8081   (ai-runtime internal, port 8081)
#   uv run uvicorn app.main_gateway:app --host 0.0.0.0 --port 8080   (ai-gateway)
#   uv run arq app.worker.WorkerSettings                              (ai-worker)
CMD ["uv", "run", "uvicorn", "app.main_runtime:app", "--host", "0.0.0.0", "--port", "8080"]
