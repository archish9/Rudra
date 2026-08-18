"""Drive `rudra` through a real pty so isatty() is true, and answer the plan prompt.

Usage: pty_drive.py <workdir> <answer> [<revision text>]
  answer: one of a / r / c

Prints the full transcript to stdout and exits with rudra's exit code.
"""

from __future__ import annotations

import os
import pty
import re
import select
import sys
import time

RUDRA = "/Users/archish/Documents/ai-ml/Rudra/.venv/bin/rudra"
TASK = "write greet.py with a greet(name) function that returns 'Hello, <name>!'"

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROMPT = re.compile(r"\[a/r/c\]")
FILE_PROMPT = re.compile(r"\[a\]pprove\s+\[r\]eject")
REVISE_PROMPT = re.compile(r"revision|what should change|How should", re.I)


def main() -> int:
    workdir, answer = sys.argv[1], sys.argv[2]
    revision = sys.argv[3] if len(sys.argv) > 3 else "drop any test task, I will write tests myself"

    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "TERM": "xterm",
        "COLUMNS": "100",
        "LINES": "40",
    }

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(workdir)
        os.execve(RUDRA, [RUDRA, TASK], env)

    transcript = []
    answered = False
    revised = False
    deadline = time.time() + 900

    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 5.0)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace")
            transcript.append(text)
            sys.stdout.write(text)
            sys.stdout.flush()

            # Strip ANSI before matching: the pty stream interleaves colour
            # codes, so "[a]pprove" arrives as "\x1b[1m[a]\x1b[0mpprove"
            # and a naive regex never fires.
            tail = ANSI.sub("", "".join(transcript))[-800:]
            if FILE_PROMPT.search(tail):
                # Per-write approval under `ask` mode. Answered every time,
                # so the approve path runs to completion instead of hanging.
                time.sleep(0.4)
                os.write(fd, b"a\n")
                transcript.append("\x00")  # break the match so it fires once
                sys.stdout.write("\n>>> SENT: 'a' (file approval)\n")
                sys.stdout.flush()
            elif not answered and PROMPT.search(tail):
                time.sleep(0.4)
                os.write(fd, f"{answer}\n".encode())
                answered = True
                sys.stdout.write(f"\n>>> SENT: {answer!r}\n")
                sys.stdout.flush()
            elif answered and answer == "r" and not revised and REVISE_PROMPT.search(tail):
                time.sleep(0.4)
                os.write(fd, f"{revision}\n".encode())
                revised = True
                sys.stdout.write(f"\n>>> SENT REVISION: {revision!r}\n")
                sys.stdout.flush()

    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    sys.stdout.write(f"\n>>> EXIT: {code}\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
