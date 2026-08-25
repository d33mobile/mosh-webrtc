#!/usr/bin/env python3
"""Local (no docker, no ssh) smoke test of mosh over the WebRTC bridge.

Runs mosh-server --webrtc and mosh-client on this host, relays the
offer/answer between them the way scripts/mosh.pl does, types a marker
into the client pty and expects it to be echoed back by the remote shell.
"""

import argparse
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time

CONNECT_RE = re.compile(rb"^MOSH CONNECT webrtc ([A-Za-z0-9/+]{22}) (\S+)$")

# ICE gathering with an unreachable STUN server takes about 20 s per side.
OFFER_TIMEOUT_S = 90
ANSWER_TIMEOUT_S = 90
MARKER_TIMEOUT_S = 60
EXIT_TIMEOUT_S = 30

# Longer than the server's own answer timeout, so a server that never gives
# up is reported as such and not as a slow one.
NO_ANSWER_TIMEOUT_S = 90

# Fds the spawned /bin/sh may legitimately hold: stdio plus the copy of its
# controlling tty that dash and bash keep on fd 10.
SHELL_FDS = {"0", "1", "2", "10"}


def log(msg):
    print(f"[smoke] {msg}", file=sys.stderr, flush=True)


class ReadError(Exception):
    """Timeout or EOF in read_until; .buf holds what was read so far."""

    def __init__(self, msg, buf):
        super().__init__(f"{msg}; got {buf[-500:]!r}")
        self.buf = buf


def read_until(fd, pattern, timeout_s, buf=b""):
    """Reads from fd until pattern matches or timeout; returns (match, buf)."""
    deadline = time.monotonic() + timeout_s
    while True:
        match = pattern.search(buf)
        if match:
            return match, buf
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadError(f"waited {timeout_s}s for {pattern.pattern!r}", buf)
        ready, _, _ = select.select([fd], [], [], min(remaining, 1.0))
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            raise ReadError(f"EOF while waiting for {pattern.pattern!r}", buf)
        buf += chunk


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="./src/frontend/mosh-server")
    parser.add_argument("--client", default="./src/frontend/mosh-client")
    parser.add_argument("--stun", default="127.0.0.1:1", help="STUN server; the default fails fast")
    parser.add_argument(
        "--no-answer", action="store_true", help="never send the answer; expect the server to give up"
    )
    args = parser.parse_args()

    env = dict(os.environ, MOSH_STUN_SERVER=args.stun, TERM="xterm", LANG="C.UTF-8")

    log("starting mosh-server --webrtc")
    server = subprocess.Popen(
        [args.server, "new", "--webrtc", "--", "/bin/sh"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        env=env,
    )
    match, _ = read_until(server.stdout.fileno(), CONNECT_RE, OFFER_TIMEOUT_S)
    key, offer = match.group(1).decode(), match.group(2).decode()
    log(f"got offer ({len(offer)} base64 chars)")

    if args.no_answer:
        log("not answering, waiting for the server to time out")
        try:
            rc = server.wait(timeout=NO_ANSWER_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            server.kill()
            sys.exit(f"FAIL: mosh-server still waiting for the answer after {NO_ANSWER_TIMEOUT_S}s")
        log(f"mosh-server exited with {rc}")
        if rc != 1:
            sys.exit(f"FAIL: expected exit status 1, got {rc}")
        log("PASS")
        return

    with tempfile.TemporaryDirectory() as tmp:
        fifo = os.path.join(tmp, "answer")
        os.mkfifo(fifo)

        client_env = dict(env, MOSH_KEY=key, MOSH_WEBRTC_OFFER=offer, MOSH_WEBRTC_ANSWER_FIFO=fifo)
        log("starting mosh-client under a pty")
        client_pid, client_fd = pty.fork()
        if client_pid == 0:
            # mosh-client refuses a 0x0 terminal, which is what a fresh pty reports.
            fcntl.ioctl(sys.stdin.fileno(), termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            os.execve(args.client, [args.client, "127.0.0.1", "0"], client_env)

        # Opening the fifo for reading blocks until the client opens it for writing.
        fifo_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        _, answer_buf = read_until(fifo_fd, re.compile(rb"\n"), ANSWER_TIMEOUT_S)
        os.close(fifo_fd)
        answer = answer_buf.split(b"\n", 1)[0]
        log(f"got answer ({len(answer)} base64 chars), relaying to server")
        server.stdin.write(answer + b"\n")
        server.stdin.flush()
        server.stdin.close()

        marker = f"MARKER-{os.getpid()}".encode()
        # The typed line is echoed too, so look for the marker at the start
        # of a line to match only the command output.
        output_re = re.compile(rb"(?:^|[\r\n])" + re.escape(marker) + rb"[\r\n]")
        time.sleep(1)
        os.write(client_fd, b"echo " + marker[:-4] + b"'" + marker[-4:] + b"'\n")
        _, buf = read_until(client_fd, output_re, MARKER_TIMEOUT_S)
        log("marker echoed back through the bridge")

        if os.path.isdir("/proc/self/fd"):
            # WebRTC fds must not leak into the shell (the quote splits the
            # marker so the typed line does not match).
            fds_re = re.compile(rb"FDS-BEGIN[\r\n]+(.*?)[\r\n]+FDS-END[\r\n]", re.S)
            os.write(client_fd, b"echo FDS-'BEGIN'; ls /proc/$$/fd; echo FDS-'END'\n")
            match, buf = read_until(client_fd, fds_re, MARKER_TIMEOUT_S, buf)
            # mosh redraws the screen with cursor-movement sequences.
            fds = set(re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b" ", match.group(1)).decode().split())
            log(f"shell fds: {sorted(fds, key=int)}")
            if not fds <= SHELL_FDS:
                sys.exit(f"FAIL: fds leaked into the shell: {sorted(fds - SHELL_FDS, key=int)}")

        os.write(client_fd, b"exit\n")
        exit_re = re.compile(rb"mosh is exiting")
        try:
            read_until(client_fd, exit_re, EXIT_TIMEOUT_S, buf)
        except ReadError:
            pass
        _, status = os.waitpid(client_pid, 0)
        client_rc = os.waitstatus_to_exitcode(status)
        log(f"mosh-client exited with {client_rc}")
        server.wait(timeout=EXIT_TIMEOUT_S)
        if client_rc != 0:
            sys.exit(client_rc)
    log("PASS")


if __name__ == "__main__":
    main()
