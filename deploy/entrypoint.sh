#!/bin/sh
# Substitute HUBOS_PORT in supervisord template and start supervisord.
# Default port 8088; override at runtime with -e HUBOS_PORT=3000.
set -e
export HUBOS_PORT="${HUBOS_PORT:-8088}"
envsubst '${HUBOS_PORT}' \
  < /etc/supervisor/conf.d/supervisord.conf.template \
  > /etc/supervisor/conf.d/supervisord.conf
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
