#!/usr/bin/env python3
"""NAT-traversal e2e test, run inside the client container of webrtc-nat/docker-compose.yml.

1. mosh --webrtc must give a shell (marker echoed, clean exit) and the selected
   ICE pair must use the peer's NATted wan address (srflx/prflx candidate).
2. Plain mosh to the same host must not connect: the NAT has no UDP forward.
"""

import re
import socket
import subprocess
import sys
import time

SSH_HOST = "10.99.0.3"
SSH_USER = "mosh"
STUN_SERVER = "10.99.0.10:3478"
WAN_PREFIX = "10.99.0."
SSHD_WAIT_S = 60
PLAIN_MOSH_PROMPT_TIMEOUT_S = 20
DUMP = "/tmp/webrtc-session.out"

SMOKE = ["python3", "/tests/webrtc-ssh-smoke.py", f"--ssh=ssh -o StrictHostKeyChecking=no -i /key",
         "--mosh=/usr/local/bin/mosh", "--client=/usr/local/bin/mosh-client",
         f"--host={SSH_USER}@{SSH_HOST}"]
PAIR_RE = re.compile(rb"WebRTC: selected candidate pair (.*?) <-> (.*?)[\r\n]")


def log(msg):
    print(f"[e2e] {msg}", file=sys.stderr, flush=True)


def wait_for_sshd():
    deadline = time.monotonic() + SSHD_WAIT_S
    while True:
        try:
            with socket.create_connection((SSH_HOST, 22), timeout=2):
                return
        except OSError as exc:
            if time.monotonic() > deadline:
                raise TimeoutError(f"sshd at {SSH_HOST}:22 not reachable: {exc}")
            time.sleep(1)


def candidate_uses_wan_address(candidate):
    fields = candidate.split()
    address, typ = fields[4], fields[fields.index(b"typ") + 1]
    return typ in (b"srflx", b"prflx") and address.startswith(WAN_PREFIX.encode())


def test_webrtc_traverses_nat():
    log("webrtc session")
    subprocess.run(
        SMOKE + ["--webrtc", f"--stun={STUN_SERVER}", f"--dump={DUMP}",
                 f"--server=MOSH_STUN_SERVER={STUN_SERVER} mosh-server"],
        check=True,
    )
    with open(DUMP, "rb") as f:
        output = f.read()
    pairs = PAIR_RE.findall(output)
    log(f"selected candidate pairs: {pairs}")
    assert pairs, "no 'WebRTC: selected candidate pair' line in the session output"
    for _, remote in pairs:
        assert candidate_uses_wan_address(remote), f"remote candidate is not a NATted wan address: {remote!r}"


def test_plain_mosh_is_blocked():
    log("plain mosh session (expected to hang)")
    result = subprocess.run(
        SMOKE + [f"--prompt-timeout={PLAIN_MOSH_PROMPT_TIMEOUT_S}", "--server=mosh-server"],
        capture_output=True,
    )
    tail = result.stderr[-300:]
    log(f"plain mosh exited with {result.returncode}: {tail!r}")
    assert result.returncode != 0, "plain mosh got a shell through the NAT"
    assert b"MARKER-" not in result.stderr, tail
    # mosh-client gives up on its own after a while; otherwise the driver times out.
    assert b"did not make a successful connection" in result.stderr or b"waited" in result.stderr, tail


def main():
    wait_for_sshd()
    test_webrtc_traverses_nat()
    test_plain_mosh_is_blocked()
    log("PASS")


if __name__ == "__main__":
    main()
