#!/bin/sh

# Same pattern as console-api/bin/start.sh and ai/bin/start.sh: SSH/SFTP credentials/port
# are set at runtime from env vars, not baked into the image.
SSH_PORT=${SSH_PORT:-2222}
SSH_PASSWORD=${SSH_PASSWORD:-"root"}

# 1. Set the root password dynamically
echo "root:${SSH_PASSWORD}" | chpasswd

# 2. Configure the SSH port dynamically
sed -i '/^Port /d' /etc/ssh/sshd_config
echo "Port ${SSH_PORT}" >> /etc/ssh/sshd_config

# 3. Start the SSH daemon in the background
/usr/sbin/sshd

# 4. Start nginx in the foreground (keeps the container running)
exec nginx -g 'daemon off;'
