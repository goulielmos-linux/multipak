"""Cancellable subprocesses. Arguments are always lists, never shell fragments."""

import errno
import os
import pty
import re
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from termios import tcsetwinsize

from multipak.localization import tr

from . import host

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*?(?:\x07|\x1b\\)")
PARTIAL_CSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*)?$")


class _LogOutput:
    """Strip terminal control codes only from the human-readable log."""

    def __init__(self):
        self.pending = ""

    def feed(self, text):
        text = ANSI.sub("", self.pending + text)
        self.pending = ""
        if match := PARTIAL_CSI.search(text):
            self.pending = match[0][-256:]
            text = text[: match.start()]
        return text


class Cancelled(Exception):
    """Cancellation requested by the user."""


@contextmanager
def _terminal_output(enabled):
    """Let tools report transfer sizes while keeping stdin noninteractive."""
    if not enabled:
        yield None
        return
    master, slave = pty.openpty()
    with os.fdopen(master, "rb", buffering=0) as reader, os.fdopen(slave, "wb") as writer:
        tcsetwinsize(writer.fileno(), (50, 160))
        yield reader, writer


class CommandError(RuntimeError):
    def __init__(self, argv: list[str], code: int, output: str):
        self.argv, self.code, self.output = argv, code, output
        super().__init__(
            tr("{0} exited with code {1}.\n{2}").format(argv[0], code, ANSI.sub("", output)[-8000:])
        )


class Runner:
    def __init__(self, cancelled: threading.Event, log: Callable[[str], None]):
        self.cancelled = cancelled
        self.log = log
        self.defer_cancellation = False

    def read_bytes(self, argv: list[str], *, limit: int, timeout: float = 30) -> bytes:
        return self.run(argv, timeout=timeout, binary=True, output_limit=limit)

    def run(
        self,
        argv: list[str],
        *,
        timeout: float = 1800,
        check: bool = True,
        binary: bool = False,
        output_limit: int = 4 * 1024 * 1024,
        on_output: Callable[[str], None] | None = None,
        cancel_file: Path | None = None,
        terminal_output: bool = False,
    ) -> str | bytes:
        if binary and terminal_output:
            raise ValueError("Terminal output cannot carry binary data")
        if self.cancelled.is_set():
            raise Cancelled
        overrides = {
            "LC_ALL": "C",
            "DBX_CONTAINER_MANAGER": "podman",
            "DBX_NON_INTERACTIVE": "1",
            "TERM": "dumb",
            "NO_COLOR": "1",
        }
        on_host = host.is_flatpak() and os.path.basename(argv[0]) in host.HOST_TOOLS
        invocation = host.command(argv, environment=overrides, supervise=True) if on_host else argv
        env = os.environ | overrides
        self.log("$ " + repr(argv))
        start = time.monotonic()
        tail = bytearray()
        diagnostics = bytearray()
        display = _LogOutput()
        with (
            _terminal_output(terminal_output) as terminal,
            subprocess.Popen(
                invocation,
                stdin=subprocess.DEVNULL,
                stdout=terminal[1] if terminal else subprocess.PIPE,
                stderr=subprocess.PIPE if binary else subprocess.STDOUT,
                env=env,
                start_new_session=True,
            ) as proc,
            selectors.DefaultSelector() as selector,
        ):
            output = terminal[0] if terminal else proc.stdout
            assert output is not None
            if terminal:
                terminal[1].close()
            os.set_blocking(output.fileno(), False)
            selector.register(output, selectors.EVENT_READ)

            def read(fd):
                try:
                    return os.read(fd, 65536)
                except OSError as error:
                    # Linux PTYs signal EOF with EIO once all slave fds close.
                    if terminal and fd == output.fileno() and error.errno == errno.EIO:
                        return b""
                    raise

            if binary:
                selector.register(proc.stderr, selectors.EVENT_READ)
            try:
                while selector.get_map():
                    if self.cancelled.is_set() and not self.defer_cancellation:
                        raise Cancelled
                    if time.monotonic() - start > timeout:
                        raise TimeoutError(tr("Operation timed out: {0}").format(argv[0]))
                    for key, _ in selector.select(timeout=0.15):
                        chunk = read(key.fd)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if binary and key.fileobj is proc.stderr:
                            diagnostics.extend(chunk)
                            del diagnostics[:-8000]
                            continue
                        tail.extend(chunk)
                        if binary:
                            if len(tail) > output_limit:
                                raise ValueError(tr("The internal file exceeds the read limit."))
                            continue
                        # Bound captured output; full streamed logs are written by the engine.
                        if len(tail) > output_limit:
                            del tail[: len(tail) - output_limit]
                        text = chunk.decode("utf-8", errors="replace")
                        if clean := display.feed(text):
                            self.log(clean)
                        if on_output is not None:
                            on_output(text)
                try:
                    code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    # Output closed, but the process kept running.
                    raise TimeoutError(tr("Operation timed out: {0}").format(argv[0])) from exc
            finally:
                if cancel_file is not None:
                    # A privileged supervisor watches this file. Let it terminate
                    # its own child: an unprivileged GUI cannot signal root processes.
                    cancel_file.unlink(missing_ok=True)
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        pass
                # Also terminates descendants holding the pipe open after their parent exits.
                # Interrupt the host launcher; its supervisor also cleans up
                # descendants when the watched bus connection disappears.
                if on_host and proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGINT)
                        proc.wait(timeout=1)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        pass
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                # A descendant may ignore TERM even after the parent has exited.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if on_host:
                    # Pipe closure acknowledges cleanup by the host supervisor,
                    # including descendants that ignored the initial interrupt.
                    deadline = time.monotonic() + 1
                    while selector.get_map() and time.monotonic() < deadline:
                        for key, _ in selector.select(timeout=0.05):
                            if not read(key.fd):
                                selector.unregister(key.fileobj)
        output = tail.decode("utf-8", errors="replace")
        if check and code:
            raise CommandError(argv, code, output + diagnostics.decode("utf-8", errors="replace"))
        return bytes(tail) if binary else output


def looks_like_network_failure(message: str) -> bool:
    """Hints only; server failures must not be reported as proof of an offline host."""
    return any(
        token in message.lower()
        for token in (
            "could not resolve",
            "couldn't resolve",
            "temporary failure resolving",
            "network is unreachable",
            "connection timed out",
            "failed to connect",
            "connection refused",
            "tls handshake timeout",
            "curl error (6)",
            "curl error (7)",
        )
    )
