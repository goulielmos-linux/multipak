"""Orderly teardown of a QML window before the Qt application goes away."""

import shiboken6
from PySide6.QtCore import QEvent


def release_engine(app, engine, window=None):
    """Close the window and destroy the QML engine while QApplication still exists.

    The engine is a child of the application. Left alone, PySide destroys it from
    QApplication's destructor, after the widget style is gone; the KDE Qt Quick style
    (org.kde.desktop) then reads that style while the QML items are deleted and the
    process dies with SIGSEGV in libqqc2desktopstyleplugin.so.
    """
    if window is not None and shiboken6.isValid(window):
        window.close()
    if engine is None or not shiboken6.isValid(engine):
        return
    engine.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    if shiboken6.isValid(engine):
        shiboken6.delete(engine)
