"""Closing a window must not crash in the KDE Qt Quick style (SIGSEGV, exit code 139)."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

shiboken6 = pytest.importorskip("shiboken6")
QtQml = pytest.importorskip("PySide6.QtQml")
from PySide6.QtCore import QLibraryInfo  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak.kirigami import main, manager  # noqa: E402
from multipak.kirigami.shutdown import release_engine  # noqa: E402

SOURCE = Path(__file__).resolve().parents[1] / "src"


def application():
    return QApplication.instance() or QApplication(["multipak-tests"])


def test_release_engine_destroys_the_engine_while_the_application_lives():
    from PySide6.QtQuick import QQuickWindow

    app = application()
    engine = QtQml.QQmlApplicationEngine(app)
    window = QQuickWindow()
    window.show()
    release_engine(app, engine, window)
    assert not window.isVisible()
    assert not shiboken6.isValid(engine)
    release_engine(app, engine, window)  # a second call is harmless


class FakeApp:
    def __init__(self, *args):
        pass

    def setStyle(self, style):
        pass

    def exec(self):
        raise RuntimeError("event loop failed")


@pytest.mark.parametrize(
    ("module", "factory", "arguments"),
    (
        (main, "create_preview", ["installer"]),
        (manager, "create_preview_manager", ["manager", "--preview"]),
    ),
)
def test_entry_points_release_the_engine_even_when_the_loop_fails(
    monkeypatch, module, factory, arguments
):
    engine, window, released = object(), SimpleNamespace(requestActivate=lambda: None), []
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(module, "QApplication", FakeApp)
    monkeypatch.setattr(module, factory, lambda *args, **kwargs: (engine, object(), window))
    monkeypatch.setattr(module, "release_engine", lambda *args: released.append(args))
    with pytest.raises(RuntimeError, match="event loop failed"):
        module.main()
    assert len(released) == 1
    assert released[0][1:] == (engine, window)


def kde_style_available():
    imports = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))
    return (imports / "org/kde/desktop").is_dir() and (imports / "org/kde/kirigami").is_dir()


@pytest.mark.skipif(not kde_style_available(), reason="KDE Qt Quick style is not installed")
@pytest.mark.parametrize("module", ("multipak.kirigami.main", "multipak.kirigami.manager"))
def test_closing_with_the_kde_style_exits_cleanly(tmp_path, module):
    environment = {
        **os.environ,
        "PYTHONPATH": str(SOURCE),
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QUICK_CONTROLS_STYLE": "org.kde.desktop",
        "QT_LOGGING_RULES": "*=false",
    }
    arguments = ["--preview"] if module.endswith("manager") else ["--page", "environment"]
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments, "--screenshot", str(tmp_path / "shot.png")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert (tmp_path / "shot.png").is_file()
