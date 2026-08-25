#!/bin/sh
# Points the default route at this container's NAT box, then runs the command.
# Requires CAP_NET_ADMIN. GATEWAY comes from docker-compose.yml.
set -eu
: "${GATEWAY:?GATEWAY (the NAT box's LAN address) is required}"
ip route replace default via "$GATEWAY"
exec "$@"
