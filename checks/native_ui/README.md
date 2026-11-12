# Native interface checks

This directory contains previews and integration checks for the MultiPak interface.
Use the system's matching Python, PySide6, Qt Quick and Kirigami.

Run commands from the project root:

```bash
python3 src/multipak/kirigami/bootstrap.py
python3 src/multipak/kirigami/bootstrap.py --manager --preview
python3 checks/native_ui/verify_manager.py
python3 checks/native_ui/verify_languages.py
```

The `verify_*.py` scripts cover navigation, layout, dialogs, configuration and
worker signals. They use synthetic packages and simulated operations. Previews
do not install or remove packages or save language preferences.

General checks default to English. `verify_languages.py` checks English and
Portuguese translation, persistence and fallback behavior.
For an interactive preview, set `MULTIPAK_LANGUAGE=en` or
`MULTIPAK_LANGUAGE=pt_BR` to choose the language without changing saved preferences.

For headless checks, set `QT_QPA_PLATFORM=offscreen` and
`QT_QUICK_BACKEND=software` if needed. The KDE control style also needs the system's
Qt plugin and QML import paths. Offscreen captures show window contents but cannot
verify the real desktop title bar or window-manager behavior.

The interface source is in `src/multipak/kirigami`. See
[Interface editing](../../docs/development.md#interface) and
[Translation maintenance](../../docs/development.md#translations).
