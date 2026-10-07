# SvelteKit adapter-static build served as static files (PRD-001 §5, §10), with nginx
# also reverse-proxying /admin/v1 to console-api so the SPA and API share one origin —
# same pattern as operations-advws's deploy/nginx/configmap.yaml.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG PUBLIC_CONSOLE_API_BASE_URL=/admin/v1
ENV PUBLIC_CONSOLE_API_BASE_URL=$PUBLIC_CONSOLE_API_BASE_URL
RUN npm run build

FROM nginx:alpine

# SSH/SFTP access into the running container — same pattern as console-api/ai's Dockerfiles
# and operations-advws's (D:\Github\operations-advws\Dockerfile). Note: this container only
# has the built static output (no Node/Svelte compiler), so SFTP here only lets you overwrite
# /usr/share/nginx/html with an already-built `build/` folder — it will NOT live-recompile
# .svelte source the way console-api/ai's PHP/Python edits do.
RUN apk add --no-cache openssh shadow \
    && mkdir -p /run/sshd \
    # Unlike Debian's openssh-server, Alpine's package does not generate host keys on
    # install — without this, sshd fails at runtime with "no hostkeys available".
    && ssh-keygen -A \
    && echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config \
    && echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY bin/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

EXPOSE 80 2222

CMD ["/usr/local/bin/start.sh"]
