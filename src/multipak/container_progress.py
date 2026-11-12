"""Display only progress reported by Podman and the container's package manager."""

import math
import re

from .localization import tr
from .process import ANSI

SIZE = r"\d+(?:\.\d+)?\s*(?:[KMGT]i?[Bb]|[Bb])"
BLOB = re.compile(
    rf"^Copying blob (?:sha256:)?([a-f0-9]+)\s+\[.*?\]\s+({SIZE})\s*/\s*({SIZE})",
    re.I,
)
PERCENT = re.compile(r"(?<![\d.\-])(\d{1,3}(?:\.\d+)?)\s*%")


def byte_size(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]?)(i?)[Bb]", value, re.I)
    if not match:
        return None
    number, prefix, binary = match.groups()
    power = "KMGT".find(prefix.upper()) + 1 if prefix else 0
    return float(number) * (1024 if binary else 1000) ** power


class OutputProgress:
    def __init__(self, progress):
        self.progress = progress
        self.buffer = ""
        self.previous = None

    def feed(self, text):
        records = re.split(r"[\r\n]", self.buffer + text)
        self.buffer = records.pop()[-16384:]
        for record in records:
            self.record(ANSI.sub("", record).strip())

    def finish(self):
        if self.buffer:
            self.record(ANSI.sub("", self.buffer).strip())
            self.buffer = ""

    def emit(self, percent, text):
        if (percent, text) != self.previous:
            self.previous = percent, text
            self.progress(percent, text)

    def run(self, runner, command, **kwargs):
        try:
            return runner.run(command, on_output=self.feed, **kwargs)
        finally:
            self.finish()


class ImageProgress(OutputProgress):
    def __init__(self, progress):
        super().__init__(progress)
        self.layers = {}

    def record(self, line):
        if match := BLOB.match(line):
            layer, current, total = match.groups()
            received, size = byte_size(current), byte_size(total)
            if (
                received is None
                or size is None
                or size <= 0
                or not math.isfinite(received)
                or not math.isfinite(size)
            ):
                return
            percent = int(min(1, received / size) * 100)
            part = self.layers.get(layer, (None, len(self.layers) + 1))[1]
            self.layers[layer] = total, part
            self.emit(
                percent,
                tr("Downloading container image — part {0}: {1}%\n{2} of {3}").format(
                    part, percent, current, total
                ),
            )
        elif line.startswith("Trying to pull "):
            self.emit(-1, tr("Connecting to the container image download…"))
        elif line.startswith("Getting image source signatures"):
            self.emit(-1, tr("Checking the container image…"))
        elif line.startswith("Copying blob "):
            layer = line.split()[2].removeprefix("sha256:")
            if "done" in line and layer in self.layers:
                total, part = self.layers[layer]
                self.emit(
                    100,
                    tr("Downloading container image — part {0}: {1}%\n{2} of {3}").format(
                        part, 100, total, total
                    ),
                )
            elif "skipped" in line or "already exists" in line:
                self.emit(-1, tr("Reusing downloaded image layers…"))
        elif line.startswith(("Copying config ", "Writing manifest to image destination")):
            self.emit(-1, tr("Saving the container image…"))


class PackageProgress(OutputProgress):
    def __init__(self, progress, *, updating=False, install_label=None):
        super().__init__(progress)
        self.install_label = install_label or (
            tr("Installing package updates…")
            if updating
            else tr("Installing the container's basic components…")
        )
        self.phase = "repositories"

    def record(self, line):
        if not line:
            return
        lower = line.lower()
        if lower.startswith(("distrobox: ", "installing basic packages")):
            self.emit(
                -1,
                tr("Preparing the container's basic components…")
                if "installing basic packages" in lower
                else tr("Configuring the container…"),
            )
            return
        if any(
            token in lower
            for token in (
                "running transaction",
                "preparing to unpack",
                "unpacking ",
                "setting up ",
                "installing:",
                "upgrading:",
                "reinstalling:",
                "processing triggers",
            )
        ) or re.match(r"^\(\d+/\d+\)\s+(?:installing|upgrading|reinstalling)", lower):
            self.phase = "install"
            self.emit(-1, self.install_label)
            return
        if lower.startswith("progress: ") and (match := PERCENT.search(line)):
            percent = float(match[1])
            if percent <= 100:
                self.phase = "install"
                self.emit(int(percent), tr("Installing packages — {0}%").format(int(percent)))
            return
        if any(
            token in lower
            for token in (
                "downloading packages",
                "retrieving packages",
                "need to get ",
                "total download size:",
                "total size of inbound packages",
            )
        ):
            self.phase = "download"
            self.emit(-1, tr("Downloading container packages…"))
        elif lower.startswith(("get:", "hit:", "ign:")):
            metadata = any(
                token in lower for token in ("inrelease", "release", "packages", "translation")
            )
            if lower.startswith("get:") and not metadata:
                self.phase = "download"
                self.emit(-1, tr("Downloading container packages…"))
            elif self.phase != "download":
                self.emit(-1, tr("Checking package repositories…"))
        elif any(
            token in lower
            for token in (
                "synchronizing package databases",
                "updating and loading repositories",
                "reading package lists",
            )
        ):
            self.phase = "repositories"
            self.emit(-1, tr("Checking package repositories…"))

        # APT reports an overall percentage at the start of its status line.
        if re.match(r"^\d+(?:\.\d+)?%\s+\[", line) and (match := PERCENT.search(line)):
            percent = float(match[1])
            if percent <= 100:
                label = (
                    tr("Downloading container packages — {0}%")
                    if self.phase == "download"
                    else tr("Checking package repositories — {0}%")
                )
                self.emit(int(percent), label.format(int(percent)))
            return

        # DNF/DNF5 and pacman distinguish each file from the aggregate "Total".
        # Never turn one file's percentage into overall download progress.
        match = PERCENT.search(line)
        if not match or self.phase == "install":
            return
        if not re.search(r"\b(?:[KMGT]i?B|B)(?:/s|\s)", line, re.I):
            return
        percent = float(match[1])
        if percent > 100:
            return
        name = re.sub(r"^(?:\[\s*\d+/\s*\d+\]|\(\d+/\d+\):)\s*", "", line[: match.start()]).strip()
        name = re.split(r"\s{2,}|\s+\d+(?:\.\d+)?\s*[KMGT]?i?B\b|\s+\[", name)[0].strip()
        if not name:
            return
        self.phase = "download"
        label = (
            tr("Downloading container packages — {0}%").format(int(percent))
            if re.fullmatch(r"total(?:\s+\(\d+/\d+\))?", name, re.I)
            else tr("Downloading {0} — {1}%").format(name[:120], int(percent))
        )
        self.emit(int(percent), label)
