# Translation maintenance

English is the base language for source code, comments, documentation and interface
messages. Use `tr("English message")` in Python, `mark("English message")` for constants
translated at presentation time, and `qsTr("English message")` in QML.

Use whole messages with numbered placeholders for dynamic values, preserving
escaping at the point of display. Do not translate command arguments, IDs, paths
or persisted state values. Application metadata may contain publisher-provided
localized names.

## Catalogs

Catalogs live in `src/multipak/locales/<locale>/LC_MESSAGES/multipak.po`.
The source template is `src/multipak/locales/multipak.pot`. Bundle compiled `.mo`
files with the Flatpak package. English needs no catalog; Brazilian Portuguese
uses `pt_BR`.

After changing source messages:

```bash
python3 scripts/translations.py --template
```

Update the `.po` entries from the generated template, translate every message and
rerun the command. It fails for missing or empty translations and changed
placeholders. Keep compiled `.mo` files in source control and release packages.
CI validates catalogs before tests and packaging.

The internal compiler supports singular gettext entries and quoted continuation
lines. Fuzzy entries are excluded. Plural and context entries require an extension
to the extractor and compiler before use.

## Runtime behavior

Core modules use standard-library gettext. A Qt translator bridges the same
catalog to QML. Saving a language selection calls `QQmlEngine.retranslate()` and
refreshes controller properties in the active manager. New processes read the
saved preference; other running processes keep their current selection.
Third-party command output is displayed as emitted by the tool.

## Preferences and fallback

The manager offers system language, Brazilian Portuguese and English. Preferences
are stored per user in `$XDG_CONFIG_HOME/multipak/preferences.json`; unrelated keys
are retained. Missing, invalid or unreadable settings select the system language.
A failed save shows an error and leaves the active language unchanged.

System language resolution checks `LANGUAGE`, then `LC_ALL`, `LC_MESSAGES`, `LANG`
and the locale API. Portuguese variants select `pt_BR`; English variants use source
English. Other languages fall back to English.

## Development and new languages

`MULTIPAK_LANGUAGE=en` or `MULTIPAK_LANGUAGE=pt_BR` overrides the initial selection
without writing preferences. Preview language changes last only for that session.

To add a language, provide its catalog, register its option and locale resolution,
and verify fallback, placeholders, persistence and rendered layouts.
