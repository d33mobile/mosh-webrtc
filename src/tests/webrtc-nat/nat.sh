#!/bin/sh
# Turns this container into a NAT box between LAN_SUBNET and the wan network:
# masquerade everything leaving the LAN, forward only LAN-initiated flows
# (plus replies), and expose the LAN ssh server on this box's wan address.
# Requires CAP_NET_ADMIN and net.ipv4.ip_forward=1 (set by docker-compose.yml).
set -eu
: "${LAN_SUBNET:?LAN_SUBNET (e.g. 10.1.0.0/24) is required}"
: "${SSH_FORWARD_TO:=}"

# Both sides are directly attached; a default route would leak to the host.
# Docker adds no default route on some daemons; nothing to delete then.
ip route del default 2>/dev/null || true

iptables -t nat -A POSTROUTING -s "$LAN_SUBNET" ! -d "$LAN_SUBNET" -j MASQUERADE
# Unsolicited packets to this box must be dropped before conntrack records
# them: a recorded inbound tuple would force a different source port on the
# matching outbound flow (symmetric NAT), which defeats hole punching. Dropping
# also suppresses the ICMP port-unreachable a real NAT would never send.
iptables -P INPUT DROP
iptables -A INPUT -i lo -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -P FORWARD DROP
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -s "$LAN_SUBNET" -j ACCEPT

if [ -n "$SSH_FORWARD_TO" ]; then
    iptables -t nat -A PREROUTING -p tcp --dport 22 -j DNAT --to-destination "$SSH_FORWARD_TO:22"
    iptables -A FORWARD -p tcp -d "$SSH_FORWARD_TO" --dport 22 -m conntrack --ctstate NEW -j ACCEPT
fi

echo "nat ready: $LAN_SUBNET"
touch /run/nat-ready
exec sleep infinity
