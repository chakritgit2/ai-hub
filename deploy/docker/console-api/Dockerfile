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

# Same base image + SSH/SFTP setup as operations-advws's Dockerfile (D:\Github\operations-advws\Dockerfile).
# 2. Install openssh-server (SFTP rides the SSH protocol's built-in sftp-server
# subsystem, already wired up in Debian's default sshd_config — no extra package needed)
RUN apt-get update && apt-get install -y openssh-server

# 3. Prepare SSHD environment & configure base root login
RUN mkdir -p /run/sshd \
    && echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config \
    && echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

# Enable the PHP-FPM status/ping endpoints the base image's built-in php-fpm-healthcheck
# script queries via cgi-fcgi — both ship commented out in the default pool config
# (same fix as operations-advws's Dockerfile).
RUN sed -i \
    -e "s|;pm.status_path = /status|pm.status_path = /status|" \
    -e "s|;ping.path = /ping|ping.path = /ping|" \
    /usr/local/etc/php-fpm.d/www.conf

# Set the container timezone to Asia/Bangkok
ENV TZ=Asia/Bangkok
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Ensure PHP serves all text responses as UTF-8 (Thai i18n support)
RUN echo "default_charset = UTF-8" >> /usr/local/etc/php/conf.d/charset.ini \
    && echo "mbstring.internal_encoding = UTF-8" >> /usr/local/etc/php/conf.d/charset.ini \
    && echo "mbstring.http_output = UTF-8" >> /usr/local/etc/php/conf.d/charset.ini

COPY --from=composer:latest /usr/bin/composer /usr/bin/composer

COPY composer.json composer.lock ./
# --no-autoloader: composer.json's classmap autoload needs app/ to exist to scan it,
# and app/ isn't copied in yet (kept separate so this dependency-install layer caches
# across builds that only change application code, not composer.json/composer.lock).
RUN composer install --no-dev --no-autoloader --no-scripts

COPY . .

RUN composer dump-autoload --no-dev --optimize \
    && chown -R www-data:www-data /app

COPY bin/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

EXPOSE 9000 2222

CMD ["/usr/local/bin/start.sh"]
