#!/usr/bin/env python3
"""Supervisor event listener: stop the container when a core program is FATAL.

Supervisor gives up on a program after `startretries` failed starts and then
keeps running without it, so the container looks healthy while Plane is
broken. This listener writes a marker file and stops supervisord; the
entrypoint then exits non-zero and Railway's ON_FAILURE policy restarts the
whole container.

Protocol: http://supervisord.org/events.html#event-listener-notification-protocol
stdout is reserved for the protocol, so logs go to stderr.
"""

import os
import signal
import sys

CRITICAL = {
    p.strip()
    for p in os.environ.get(
        "PLANE_CRITICAL_PROGRAMS", "api,worker,beat,live,space,proxy,migrator"
    ).split(",")
    if p.strip()
}
MARKER = "/tmp/plane-fatal"


def parse_tokens(line):
    out = {}
    for token in line.split():
        if ":" in token:
            key, value = token.split(":", 1)
            out[key] = value
    return out


def main():
    while True:
        sys.stdout.write("READY\n")
        sys.stdout.flush()

        header_line = sys.stdin.readline()
        if not header_line:
            return 0
        header = parse_tokens(header_line)
        payload = sys.stdin.read(int(header.get("len", "0")))
        body = parse_tokens(payload.split("\n", 1)[0])

        name = body.get("processname", "?")
        if header.get("eventname") == "PROCESS_STATE_FATAL" and name in CRITICAL:
            sys.stderr.write(
                f"[crashwatch] '{name}' entered FATAL state; stopping supervisord so the container restarts\n"
            )
            sys.stderr.flush()
            try:
                with open(MARKER, "w") as fh:
                    fh.write(name + "\n")
                os.kill(os.getppid(), signal.SIGTERM)
            except OSError as exc:
                sys.stderr.write(f"[crashwatch] could not stop supervisord: {exc}\n")
                sys.stderr.flush()

        sys.stdout.write("RESULT 2\nOK")
        sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
