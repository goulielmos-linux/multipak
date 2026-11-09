"""Cancellable subprocesses. Arguments are always lists, never shell fragments."""

import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable

from multipak.localization import tr

from . import host


class Cancelled(Exception):
    """Cancellation requested by the user."""


class CommandError(RuntimeError):
    def __init__(self, argv: list[str], code: int, output: str):
        self.argv, self.code, self.output = argv, code, output
        super().__init__(tr("{0} exited with code {1}.\n{2}").format(argv[0], code, output[-8000:]))


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
    ) -> str | bytes:
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
        with (
            subprocess.Popen(
                invocation,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if binary else subprocess.STDOUT,
                env=env,
                start_new_session=True,
            ) as proc,
            selectors.DefaultSelector() as selector,
        ):
            assert proc.stdout is not None
            os.set_blocking(proc.stdout.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ)
            if binary:
                selector.register(proc.stderr, selectors.EVENT_READ)
            try:
                while selector.get_map():
                    if self.cancelled.is_set() and not self.defer_cancellation:
                        raise Cancelled
                    if time.monotonic() - start > timeout:
                        raise TimeoutError(tr("Operation timed out: {0}").format(argv[0]))
                    for key, _ in selector.select(timeout=0.15):
                        chunk = os.read(key.fd, 65536)
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
                        self.log(chunk.decode("utf-8", errors="replace"))
                code = proc.wait(timeout=5)
            finally:
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
                            if not os.read(key.fd, 65536):
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
