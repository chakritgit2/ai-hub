# Placeholder — one image serves ai-runtime, ai-gateway and ai-worker (PRD-001 §4.2, §10).
# Which role a container plays is selected by CMD/entrypoint args at deploy time (K8s manifests
# in ../k8s/ are also placeholders and don't yet set this up).
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir uv

# SSH/SFTP access into the running container — same pattern as console-api's Dockerfile
# and operations-advws's (D:\Github\operations-advws\Dockerfile): sshd runs alongside the
# app via bin/start.sh, credentials/port set at runtime so they aren't baked into the image.
RUN apt-get update && apt-get install -y openssh-server \
    && mkdir -p /run/sshd \
    && echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config \
    && echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY . .

COPY bin/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

EXPOSE 8080 8081 2222

# TODO: real entrypoint script choosing between:
#   uv run uvicorn app.main_runtime:app --host 0.0.0.0 --port 8080   (ai-runtime, port 8080)
#   uv run uvicorn app.main_runtime:app --host 0.0.0.0 --port 8081   (ai-runtime internal, port 8081)
#   uv run uvicorn app.main_gateway:app --host 0.0.0.0 --port 8080   (ai-gateway)
#   uv run arq app.worker.WorkerSettings                              (ai-worker)
#
# start.sh starts sshd then `exec`s whatever command/args are passed to the container —
# ai-runtime/ai-gateway pass a different uvicorn invocation via their Deployment `args:`.
ENTRYPOINT ["/usr/local/bin/start.sh"]
CMD ["uv", "run", "uvicorn", "app.main_runtime:app", "--host", "0.0.0.0", "--port", "8080"]
