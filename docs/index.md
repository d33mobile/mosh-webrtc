---
title: mosh-webrtc2
---

# mosh-webrtc2

A fork of [mosh](https://mosh.org) that can carry its UDP datagrams over a
WebRTC data channel. ICE with STUN and an optional TURN relay lets client and
server connect when one or both of them sit behind NAT and no port can be
forwarded. Everything else stays mosh: the terminal emulation, the
state-synchronization protocol and its own AES-OCB encryption.

## Install (Debian trixie, amd64)

Download the `.deb` from the
[latest release](https://github.com/d33mobile/mosh-webrtc2/releases/latest)
and install it on both ends. It replaces the `mosh` package and bundles
libdatachannel statically, so it depends only on standard Debian libraries.

```
sudo dpkg -i mosh-webrtc_*_amd64.deb
```

## Use

```
mosh --webrtc user@host
```

Signaling travels over the ssh session: the server prints its offer, the
wrapper starts the client, and the answer goes back over ssh's stdin. Both
ends read these variables:

| variable | meaning |
|---|---|
| `MOSH_STUN_SERVER` | `host:port`, default `stun.l.google.com:19302` |
| `MOSH_TURN_SERVER` | `turn:USER:PASS@HOST:PORT`, relay fallback |
| `MOSH_ICE_TRANSPORT_POLICY` | `relay` forces the relay, useful for testing |

The server side does not inherit your environment through ssh, so pass values
there explicitly when needed:

```
MOSH_TURN_SERVER=turn:mosh:SECRET@turn.example.org:36581 \
mosh --webrtc --server='MOSH_STUN_SERVER=stun.example.org:3478 mosh-server' user@host
```

A jump host works too (`--ssh='ssh -J jump@gateway'`); plain mosh cannot do
that because it has to know the server's public address.

## Limitations

- No trickle ICE: connection setup waits for candidate gathering (about
  20 s if the STUN server is unreachable).
- Data is encrypted twice (mosh's AES-OCB inside DTLS).
- Without a TURN server, symmetric NAT on both sides fails.
- Roaming works only within the ICE session.

## Build from source

```
git clone --recurse-submodules https://github.com/d33mobile/mosh-webrtc2
src/tests/webrtc-nat/build-libdatachannel.sh --prefix=/usr/local   # or --static
./autogen.sh && ./configure --enable-webrtc && make && make check
```

Tests: `make check` covers the loopback path; `make -C src/tests/webrtc-nat test`
runs a docker topology with two NAT routers and a coturn server and asserts
that the selected candidate pair is `srflx` (and `relay` in the TURN case)
while plain mosh fails.

Source and issues: [github.com/d33mobile/mosh-webrtc2](https://github.com/d33mobile/mosh-webrtc2)
