#!/usr/bin/env python3
"""Smoke test of scripts/mosh.pl over ssh to localhost (no docker).

Runs mosh.pl under a pty, types a marker into the resulting shell and
expects it to be echoed back, then exits and checks the exit status.
With --webrtc the session goes through the WebRTC bridge.
"""

import argparse
import fcntl
import importlib.util
import os
import pty
import re
import stat
import struct
import sys
import tempfile
import termios
import time


def load_sibling(name):
    """Imports a script next to this one; the hyphenated file names are not module names."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


local_smoke = load_sibling("webrtc-local-smoke.py")
EXIT_TIMEOUT_S, MARKER_TIMEOUT_S = local_smoke.EXIT_TIMEOUT_S, local_smoke.MARKER_TIMEOUT_S
log, read_until = local_smoke.log, local_smoke.read_until

# The prompt appears only after both sides finished ICE gathering (about
# 20 s each with an unreachable STUN server).
PROMPT_TIMEOUT_S = 120

# With a dead client mosh.pl must give up on its own, well before the
# server-side answer timeout would end the session for it.
FAILURE_TIMEOUT_S = 15

FAKE_CLIENT = """#!/bin/sh
# Stand-in for mosh-client that dies before writing the WebRTC answer.
[ "$1" = -c ] && { echo 8; exit 0; }
echo "fake mosh-client: giving up" >&2
exit 1
"""


def fake_client_failure(args, argv, env):
    """mosh.pl must exit non-zero soon after its client dies without an answer."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = os.path.join(tmp, "mosh-client")
        with open(fake, "w") as f:
            f.write(FAKE_CLIENT)
        os.chmod(fake, stat.S_IRWXU)
        argv = [a if not a.startswith("--client=") else f"--client={fake}" for a in argv]
        log(f"starting {' '.join(argv)}")
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe(argv[0], argv, env)
        _, buf = read_until(fd, re.compile(rb"fake mosh-client: giving up"), args.prompt_timeout)
        log("fake client died, waiting for mosh.pl to notice")
        try:
            read_until(fd, re.compile(rb"(?!)"), FAILURE_TIMEOUT_S, buf)
        except local_smoke.ReadError as e:
            if not e.args[0].startswith("EOF"):
                os.kill(pid, 9)
                os.waitpid(pid, 0)
                sys.exit("FAIL: mosh.pl still running after the client exited without an answer")
        _, status = os.waitpid(pid, 0)
        rc = os.waitstatus_to_exitcode(status)
        log(f"mosh.pl exited with {rc}")
        if rc == 0:
            sys.exit("FAIL: mosh.pl exited 0 although the client exited without an answer")
        log("PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mosh", default="./scripts/mosh.pl")
    parser.add_argument("--server", default=os.path.abspath("./src/frontend/mosh-server"))
    parser.add_argument("--client", default=os.path.abspath("./src/frontend/mosh-client"))
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--ssh", default="ssh -o BatchMode=yes -o StrictHostKeyChecking=no")
    parser.add_argument("--stun", default="127.0.0.1:1", help="STUN server; the default fails fast")
    parser.add_argument("--webrtc", action="store_true")
    parser.add_argument(
        "--fake-client-failure",
        action="store_true",
        help="run with a client that exits before answering; expect mosh.pl to fail promptly",
    )
    parser.add_argument("--dump", help="write the raw pty output up to the marker to this file")
    parser.add_argument("--prompt-timeout", type=float, default=PROMPT_TIMEOUT_S)
    args = parser.parse_args()

    env = dict(os.environ, MOSH_STUN_SERVER=args.stun, TERM="xterm", LANG="C.UTF-8")
    argv = ["perl", args.mosh, f"--server={args.server}", f"--client={args.client}", f"--ssh={args.ssh}"]
    if args.webrtc:
        argv.append("--webrtc")
    argv += [args.host, "--", "/bin/sh"]

    if args.fake_client_failure:
        fake_client_failure(args, argv, env)
        return

    log(f"starting {' '.join(argv)}")
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(sys.stdin.fileno(), termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        os.execvpe(argv[0], argv, env)

    marker = f"MARKER-{os.getpid()}".encode()
    output_re = re.compile(rb"(?:^|[\r\n])" + re.escape(marker) + rb"[\r\n]")
    buf = b""
    try:
        # /bin/sh prints "$ " once mosh-client has drawn the remote screen.
        _, buf = read_until(fd, re.compile(rb"\$ "), args.prompt_timeout)
        log("got a prompt")
        time.sleep(1)
        os.write(fd, b"echo " + marker[:-4] + b"'" + marker[-4:] + b"'\n")
        _, buf = read_until(fd, output_re, MARKER_TIMEOUT_S, buf)
        log("marker echoed back")
    except local_smoke.ReadError as e:
        buf = e.buf
        raise
    finally:
        if args.dump:
            with open(args.dump, "wb") as f:
                f.write(buf)

    os.write(fd, b"exit\n")
    # Drain until mosh.pl closes the pty.
    try:
        read_until(fd, re.compile(rb"(?!)"), EXIT_TIMEOUT_S, buf)
    except local_smoke.ReadError:
        pass
    _, status = os.waitpid(pid, 0)
    rc = os.waitstatus_to_exitcode(status)
    log(f"mosh.pl exited with {rc}")
    if rc != 0:
        sys.exit(rc)
    log("PASS")


if __name__ == "__main__":
    main()
