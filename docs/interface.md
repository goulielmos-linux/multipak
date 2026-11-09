# Editing the interface

The interface is in `src/multipak/kirigami/*.qml`, with Python controllers in the
same directory. Edit QML directly and use the system's matching Qt, PySide6 and
Kirigami for previews.

`Welcome.qml` is the root window for the Installation Wizard; `Manager.qml` is the
root window for the Application Manager. The wizard opens package files, while the
manager provides application removal, container updates and preferences. Both use
shared components such as `BrandHeader.qml`, `InformationWindow.qml` and `AboutWindow.qml`.

## Previews and layout

Run previews from the project root:

```bash
python3 src/multipak/kirigami/bootstrap.py
python3 src/multipak/kirigami/bootstrap.py --manager --preview
```

The scripts in `checks/native_ui` verify navigation, layout, dialogs and worker
behavior using sample data. Keep object names stable when checks depend on them.

Check light and dark themes, wrapped labels and compact window sizes. Ordinary
pages should fit their contents, with scrolling available for logs, license text
and longer information on small screens.

## About window

`AboutWindow.qml` is shared by the manager and installation wizard. Each supplies
its own title, description and icon. `about.py` supplies the application version,
license text, loaded library versions and backend information. Backend version
queries run asynchronously with `--version`, a two-second deadline and bounded
output. Library links point to official licensing information and project websites.

License references: [Python](https://docs.python.org/3/license.html),
[Qt](https://doc.qt.io/qt-6/licensing.html),
[Qt for Python](https://doc.qt.io/qtforpython-6/commercial/index.html), and
[Kirigami](https://github.com/KDE/kirigami/tree/master/LICENSES).

## Interface text

Use English source text with `qsTr()` in QML and `tr()` in Python. Refresh the
catalogs as described in [Translation maintenance](translations.md).
