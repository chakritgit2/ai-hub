#!/bin/bash

# Same pattern as operations-advws/start.sh (D:\Github\operations-advws\start.sh):
# SSH/SFTP credentials and timezone are set at runtime from env vars, not baked
# into the image. K8s overrides SSH_PASSWORD via a Secret at deploy time.
SSH_PORT=${SSH_PORT:-2222}
SSH_PASSWORD=${SSH_PASSWORD:-"root"}

# Set the timezone to Asia/Bangkok (overrides any inherited value at runtime)
TZ=${TZ:-"Asia/Bangkok"}
export TZ
ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime && echo "${TZ}" > /etc/timezone

# 1. Set the root password dynamically
echo "root:${SSH_PASSWORD}" | chpasswd

# 2. Configure the SSH port dynamically
sed -i '/^Port /d' /etc/ssh/sshd_config
echo "Port ${SSH_PORT}" >> /etc/ssh/sshd_config

# 3. Start the SSH daemon in the background
/usr/sbin/sshd

# 4. Start PHP-FPM in the foreground (keeps the container running)
exec php-fpm
