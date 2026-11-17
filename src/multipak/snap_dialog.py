"""zenity's dialogs for snaps, shown by MultiPak outside the bubble.

Snaps often expect zenity, which is common on GNOME but missing in the bubble. A small
zenity client in the bubble sends its requests here, and MultiPak shows them with Qt
(the desktop portal, as a Flatpak). zenity options apps do not use are refused."""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "4.0.1"  # zenity in Ubuntu 24.04; some apps check it before use.
MODES = (
    "file-selection",
    "info",
    "warning",
    "error",
    "question",
    "entry",
    "password",
    "color-selection",
)
# zenity's other dialogs, which MultiPak does not show.
OTHER_MODES = ("list", "calendar", "progress", "scale", "text-info", "forms", "notification")
# Options that take a value, also written as "--option value".
VALUES = {
    "title",
    "filename",
    "file-filter",
    "separator",
    "text",
    "ok-label",
    "cancel-label",
    "extra-button",
    "entry-text",
    "color",
    "width",
    "height",
    "timeout",
    "icon-name",
    "window-icon",
    "attach",
}
REPEATED = {"file-filter", "extra-button"}
CANCELLED, FAILED = 1, 255


@dataclass
class Request:
    mode: str = ""
    values: dict = field(default_factory=dict)
    lists: dict = field(default_factory=dict)
    flags: set = field(default_factory=set)

    def value(self, name, default=""):
        return self.values.get(name, default)


def parse(arguments):
    """zenity's command line as a Request; ValueError for what it would refuse."""
    request = Request()
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        index += 1
        if not argument.startswith("--"):
            continue  # zenity ignores stray words.
        name, equals, value = argument[2:].partition("=")
        if name in MODES or name in OTHER_MODES or name == "version":
            if request.mode and request.mode != name:
                raise ValueError("Two dialog types requested")
            request.mode = name
            continue
        if name in VALUES:
            if not equals:
                if index >= len(arguments):
                    raise ValueError(f"--{name} needs a value")
                value = arguments[index]
                index += 1
            if name in REPEATED:
                request.lists.setdefault(name, []).append(value)
            else:
                request.values[name] = value
        else:
            request.flags.add(name)
    if not request.mode:
        raise ValueError("You must specify a dialog type")
    return request


def name_filter(text):
    """zenity's "Name | *.a *.b" (or just patterns) as Qt's "Name (*.a *.b)"."""
    name, separator, patterns = text.rpartition("|")
    patterns = " ".join(patterns.split()) or "*"
    name = name.strip() if separator else patterns
    return f"{name} ({patterns})"


def run(arguments):
    """Answer one zenity call: (exit code, text, whether the text is standard output)."""
    try:
        request = parse(arguments)
    except ValueError as error:
        return FAILED, f"zenity: {error}\n", False
    if request.mode == "version":
        return 0, VERSION + "\n", True
    if request.mode not in MODES:
        return FAILED, f"zenity: --{request.mode} is not available here\n", False
    # A process of its own: the dialog needs a Qt application, which the snap's
    # launcher does not have, and the launcher must keep answering meanwhile.
    package = str(Path(__file__).resolve().parents[1])
    script = (
        f"import sys; sys.path.insert(0, {package!r}); "
        "from multipak.snap_dialog import main; main()"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps(arguments),
            capture_output=True,
            text=True,
            check=False,
        )
        answer = json.loads(result.stdout)
        return int(answer["code"]), str(answer["text"]), bool(answer["output"])
    except (OSError, ValueError, KeyError, TypeError):
        return FAILED, "zenity: the dialog could not be shown\n", False


# The dialog process


def _file_selection(request):
    from PySide6.QtWidgets import QDialog, QFileDialog

    dialog = QFileDialog(None, request.value("title"))
    if "directory" in request.flags:
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly)
    elif "save" in request.flags:
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        if "confirm-overwrite" not in request.flags:
            dialog.setOption(QFileDialog.Option.DontConfirmOverwrite)
    elif "multiple" in request.flags:
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
    else:
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    filename = request.value("filename")
    if filename:
        path = Path(filename)
        if filename.endswith("/") or path.is_dir():
            dialog.setDirectory(str(path))
        else:
            if path.parent.is_dir():
                dialog.setDirectory(str(path.parent))
            dialog.selectFile(path.name)
    filters = [name_filter(item) for item in request.lists.get("file-filter", [])]
    if filters:
        dialog.setNameFilters(filters)
    if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selectedFiles():
        return CANCELLED, "", True
    separator = request.value("separator", "|")
    return 0, separator.join(dialog.selectedFiles()) + "\n", True


def _message(request):
    from PySide6.QtWidgets import QMessageBox

    icons = {
        "info": QMessageBox.Icon.Information,
        "warning": QMessageBox.Icon.Warning,
        "error": QMessageBox.Icon.Critical,
        "question": QMessageBox.Icon.Question,
    }
    box = QMessageBox(icons[request.mode], request.value("title"), request.value("text"))
    if request.mode == "question":
        accept = box.addButton(QMessageBox.StandardButton.Yes)
        reject = box.addButton(QMessageBox.StandardButton.No)
        if request.value("cancel-label"):
            reject.setText(request.value("cancel-label"))
        box.setDefaultButton(reject if "default-cancel" in request.flags else accept)
    else:
        accept, reject = box.addButton(QMessageBox.StandardButton.Ok), None
    if request.value("ok-label"):
        accept.setText(request.value("ok-label"))
    extra = {}
    for label in request.lists.get("extra-button", []):
        extra[box.addButton(label, QMessageBox.ButtonRole.ActionRole)] = label
    box.exec()
    clicked = box.clickedButton()
    if clicked is accept:
        return 0, "", True
    if clicked in extra:
        # zenity prints the extra button's label and reports 1.
        return CANCELLED, extra[clicked] + "\n", True
    return CANCELLED, "", True


def _entry(request):
    from PySide6.QtWidgets import QInputDialog, QLineEdit

    hidden = request.mode == "password" or "hide-text" in request.flags
    text, accepted = QInputDialog.getText(
        None,
        request.value("title"),
        request.value("text"),
        QLineEdit.EchoMode.Password if hidden else QLineEdit.EchoMode.Normal,
        request.value("entry-text"),
    )
    return (0, text + "\n", True) if accepted else (CANCELLED, "", True)


def _color(request):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    initial = QColor(request.value("color") or "#ffffff")
    color = QColorDialog.getColor(initial, None, request.value("title"))
    if not color.isValid():
        return CANCELLED, "", True
    return 0, f"rgb({color.red()},{color.green()},{color.blue()})\n", True


def show(request):
    if request.mode == "file-selection":
        return _file_selection(request)
    if request.mode in ("info", "warning", "error", "question"):
        return _message(request)
    if request.mode in ("entry", "password"):
        return _entry(request)
    return _color(request)


def main():
    from PySide6.QtWidgets import QApplication

    try:
        request = parse(json.loads(sys.stdin.read()))
        application = QApplication.instance() or QApplication(["zenity"])
        application.setQuitOnLastWindowClosed(False)
        code, text, output = show(request)
    except Exception as error:  # noqa: BLE001 - every failure must reach the app.
        code, text, output = FAILED, f"zenity: {error}\n", False
    sys.stdout.write(json.dumps({"code": code, "text": text, "output": output}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
