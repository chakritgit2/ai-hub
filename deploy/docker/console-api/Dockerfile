# Placeholder — not yet wired to a working php-fpm+nginx build. PRD-001 §5, §10.
# TODO: multi-stage build: composer install --no-dev, copy into a php:8.1-fpm base with the
# Phalcon extension compiled in, front with nginx (see nginx.conf in this folder), --platform linux/amd64.
FROM php:8.1-fpm

WORKDIR /app
COPY . .

# TODO: install ext-phalcon (not available via PECL as a prebuilt binary for all platforms —
# compile from source or use a base image that already has it, e.g. phalcon/phalcon-docker).
# TODO: composer install --no-dev --optimize-autoloader

EXPOSE 9000
