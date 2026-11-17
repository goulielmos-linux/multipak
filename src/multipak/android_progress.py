"""Translate streamed Waydroid initialization output into visible stage progress."""

import math
import re

from .localization import tr
from .process import Cancelled

DOWNLOAD = re.compile(
    r"\[Downloading\]\s*(\d+(?:\.\d+)?)\s*MB/\s*(\d+(?:\.\d+)?)\s*MB\s+"
    r"(\d+(?:\.\d+)?)\s*(kB/s|MB/s|GB/s|B/s)\s*\(approx\.\)"
)

STARTUP_RETRY = re.compile(
    r"^(?:\[[^\]\r\n]*\]\s*)*(?:(?:WARNING|INFO)\s*:\s*)?"
    r"(?:Failed to get service waydroidplatform, trying again\.\.\."
    r"|Waiting for binder Service Manager\.\.\.)\s*$",
    re.I,
)


class AndroidStartupOutput:
    """Explain expected startup retries, retaining raw diagnostics on failure.

    Only the log callback is adapted. Command output, errors and exit codes
    continue to reach the readiness checks unchanged.
    """

    def __init__(self, runner, progress):
        self.runner = runner
        self.progress = progress
        self.had_log = hasattr(runner, "log")
        self.original_log = getattr(runner, "log", lambda _text: None)
        self.buffer = ""
        self.diagnostics = []

    def __enter__(self):
        message = tr("Waiting for Android to finish starting…")
        self.progress(-1, message)
        self.original_log(message)
        self.runner.log = self.feed
        return self

    def feed(self, text):
        # Runner logs command arguments separately, without a trailing newline.
        if text.startswith("$ "):
            self.flush()
            self.original_log(text)
            return
        records = re.split(r"[\r\n]", self.buffer + text)
        self.buffer = records.pop()
        for record in records:
            self._record(record)
        if len(self.buffer) > 8192:
            self.flush()

    def _record(self, line):
        if STARTUP_RETRY.fullmatch(line):
            self.diagnostics = (self.diagnostics + [line])[-32:]
        elif line:
            self.original_log(line)

    def flush(self):
        if self.buffer:
            self._record(self.buffer)
            self.buffer = ""

    def __exit__(self, exception_type, exception, traceback):
        try:
            self.flush()
            if exception is not None and not isinstance(exception, Cancelled) and self.diagnostics:
                self.original_log(tr("Android startup diagnostics:"))
                for line in self.diagnostics:
                    self.original_log(line)
        finally:
            if self.had_log:
                self.runner.log = self.original_log
            else:
                del self.runner.log


class AndroidProgress:
    def __init__(self, progress):
        self.progress = progress
        self.buffer = ""
        self.image = ""
        self.previous = None

    def feed(self, text):
        records = re.split(r"[\r\n]", self.buffer + text)
        self.buffer = records.pop()[-8192:]
        for record in records:
            self._record(record)
        # The last update may have no newline or terminating carriage return.
        if DOWNLOAD.search(self.buffer):
            self._record(self.buffer)

    def _emit(self, percent, message):
        if (percent, message) != self.previous:
            self.previous = percent, message
            self.progress(percent, message)

    def _record(self, line):
        if "Downloading https://" in line or "Downloading http://" in line:
            if "/system/" in line:
                self.image = "system"
            elif "/vendor/" in line:
                self.image = "vendor"
            else:
                self.image = ""
            message = {
                "system": tr("Connecting to the Android system download…"),
                "vendor": tr("Connecting to the Android components download…"),
            }.get(self.image, tr("Connecting to the Android download…"))
            self._emit(-1, message)
        if match := DOWNLOAD.search(line):
            current, total, speed, unit = match.groups()
            numbers = tuple(map(float, (current, total, speed)))
            if not all(math.isfinite(value) for value in numbers) or numbers[1] <= 0:
                return
            percent = min(100, max(0, int(numbers[0] / numbers[1] * 100)))
            title = {
                "system": tr("Downloading Android system"),
                "vendor": tr("Downloading Android components"),
            }.get(self.image, tr("Downloading Android"))
            self._emit(
                percent,
                tr("{0} — {1}%\n{2} MB of {3} MB · {4} {5}").format(
                    title, percent, current, total, speed, unit
                ),
            )
        if "Validating system image" in line:
            self.image = "system"
            self._emit(-1, tr("Validating the Android system…"))
        elif "Validating vendor image" in line:
            self.image = "vendor"
            self._emit(-1, tr("Validating the Android components…"))
        elif "Extracting to " in line:
            self._emit(
                -1,
                tr("Extracting the Android system…")
                if self.image == "system"
                else tr("Extracting the Android components…"),
            )
