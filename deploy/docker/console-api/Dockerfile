# php-fpm image for console-api (PRD-001 §5, §10). Reuses the same base image
# operations-advws already builds and runs successfully in the cdi-advws cluster
# (D:\Github\operations-advws\Dockerfile) — ext-phalcon isn't available as a prebuilt
# PECL binary, and this image ships it precompiled. composer.json's
# `config.platform.ext-phalcon` pin (5.9.3) is only a dependency-resolver hint, not a
# runtime check, so this image's 5.9.2 is fine.
FROM phalconphp/cphalcon:v5.9.2-php8.4

# This base image defaults to a non-root user (operations-advws's own Dockerfile works
# around the same thing) — switch back to root so COPY/RUN below can write to /app.
USER root
WORKDIR /app

COPY --from=composer:latest /usr/bin/composer /usr/bin/composer

COPY composer.json composer.lock ./
# --no-autoloader: composer.json's classmap autoload needs app/ to exist to scan it,
# and app/ isn't copied in yet (kept separate so this dependency-install layer caches
# across builds that only change application code, not composer.json/composer.lock).
RUN composer install --no-dev --no-autoloader --no-scripts

COPY . .

RUN composer dump-autoload --no-dev --optimize \
    && chown -R www-data:www-data /app

EXPOSE 9000
