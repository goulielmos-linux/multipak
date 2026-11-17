"""The real windows, with sample data: the side bar, the lists' buttons and the wizard.

These tests click on the QML interface as a person would, so a button that disappears
or calls the wrong thing after a change to the layout is noticed. Nothing is installed.
"""

import os

import pytest

pytest.importorskip("PySide6.QtQuick")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
os.environ.setdefault("MULTIPAK_LANGUAGE", "en")

from PySide6.QtCore import QObject, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak.kirigami.main import create_preview  # noqa: E402
from multipak.kirigami.manager import create_preview_manager  # noqa: E402
from multipak.kirigami.shutdown import release_engine  # noqa: E402


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    # Kirigami comes from the desktop (or the Flatpak runtime), not from pip.
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(b"import org.kde.kirigami\nimport QtQuick\nItem {}", "")
    if component.isError():
        pytest.skip("Kirigami is not installed")
    return app


def wait(predicate, timeout=5000):
    for _ in range(timeout // 20):
        if predicate():
            return True
        QTest.qWait(20)
    return predicate()


def find(window, name):
    """The item called name in the window, preferring the one on screen, or None."""
    items = list(window.findChildren(QObject, name))
    pending = [window.contentItem()]
    while pending:
        current = pending.pop()
        if current.objectName() == name and current not in items:
            items.append(current)
        pending.extend(current.childItems())
    visible = [item for item in items if item.property("visible")]
    return (visible or items or [None])[0]


def shown(window, name):
    item = find(window, name)
    return item is not None and item.property("visible") and item.width() > 0


def appears(window, name):
    return wait(lambda: shown(window, name))


def ready(window, name):
    """Shown and enabled: the windows start disabled until their startup checks end."""
    return shown(window, name) and find(window, name).property("enabled")


def click(window, name):
    item = find(window, name)
    assert item is not None, name
    assert item.property("visible") and item.property("enabled"), name
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), name
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(150)


@pytest.fixture
def manager(application):
    engine, controller, window = create_preview_manager(application)
    window.show()
    assert wait(lambda: ready(window, "managerNav_install"))
    yield controller, window
    release_engine(application, engine, window)


def test_the_side_bar_opens_each_section(manager):
    controller, window = manager
    for key in ("install", "remove", "update", "snaps", "associations", "language"):
        click(window, "managerNav_" + key)
        assert wait(lambda: not controller.busy)
        assert find(window, "managerNav_" + key).property("highlighted"), key
    assert controller.mode == "language" or shown(window, "managerNav_language")


def test_about_is_the_last_item_and_opens_its_window(manager):
    _controller, window = manager
    about = find(window, "managerNav_about")
    language = find(window, "managerNav_language")
    assert about.mapToScene(QPointF(0, 0)).y() > language.mapToScene(QPointF(0, 0)).y()
    click(window, "managerNav_about")
    dialog = window.findChild(QObject, "managerAboutDialog")
    assert wait(lambda: dialog.property("visible"))
    # About is a window, not a section: the side bar keeps its place.
    assert not about.property("highlighted")
    dialog.close()


def test_each_application_has_its_own_buttons_and_the_footer_only_closes(manager):
    controller, window = manager
    click(window, "managerNav_remove")
    assert wait(lambda: not controller.busy and controller.rows)
    assert appears(window, "managerRowRemoveButton0")
    assert appears(window, "managerRefreshButton")
    assert appears(window, "managerCloseButton")
    # The old footer buttons are gone.
    assert find(window, "managerActionButton") is None
    asked = []
    controller.confirmationRequested.connect(lambda: asked.append(True))
    click(window, "managerRowRemoveButton0")
    assert asked == [True]
    # The confirmation opens; closing it uninstalls nothing.
    dialog = window.findChild(QObject, "removalDialog")
    assert wait(lambda: dialog.property("visible"))
    dialog.close()
    controller.cancelRemoval()
    assert wait(lambda: not dialog.property("visible"))

    click(window, "managerNav_update")
    assert wait(lambda: not controller.busy and controller.rows)
    assert appears(window, "managerCheckUpdatesButton")
    assert appears(window, "managerUpdateAllButton")
    updatable = [i for i, row in enumerate(controller.rows) if row.get("updatable")]
    assert updatable
    assert appears(window, f"managerRowUpdateButton{updatable[0]}")


def test_the_install_page_welcomes_and_offers_both_tools(manager):
    controller, window = manager
    click(window, "managerNav_install")
    assert controller.mode == "install"
    assert appears(window, "managerChooseFileButton")
    assert appears(window, "snapNameField")
    assert appears(window, "snapStoreSiteLink")
    assert not find(window, "confirmSnapStoreButton").property("enabled")


def test_saving_a_language_retranslates_the_open_window(application, tmp_path):
    from multipak.kirigami.languages import Languages
    from multipak.localization import LanguageSettings

    service = Languages(application, LanguageSettings(tmp_path / "config"))
    application._multipak_languages = service
    service.choose(2)  # English
    service.save(False)
    engine, controller, window = create_preview_manager(application)
    window.show()
    try:
        assert wait(lambda: ready(window, "managerNav_language"))
        click(window, "managerNav_language")
        assert appears(window, "languageHeading")
        assert find(window, "languageHeading").property("text") == "Language"
        service.choose(1)  # Português (Brasil)
        click(window, "languageSaveButton")
        assert wait(lambda: find(window, "languageHeading").property("text") == "Idioma")
    finally:
        service.choose(2)
        service.save(False)
        del application._multipak_languages
        release_engine(application, engine, window)


def test_the_wizard_moves_from_the_welcome_to_the_method(application):
    engine, _bridge, window = create_preview(application)
    window.show()
    try:
        assert wait(lambda: ready(window, "continueButton"))
        assert appears(window, "searchButton")
        click(window, "continueButton")
        assert wait(lambda: window.property("currentPage").objectName() == "methodPage")
        assert appears(window, "methodContinueButton")
    finally:
        release_engine(application, engine, window)
