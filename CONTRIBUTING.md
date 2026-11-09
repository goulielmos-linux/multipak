# Contributing

## Language

Use English for source messages, comments, documentation, commit descriptions and
new file names. Keep translated interface text in the language catalogs; see
[Translation maintenance](docs/translations.md). Tests that explicitly verify a
translation may contain the expected text in that language.

## Code changes

Keep presentation, installation rules and process execution separate. Package
inspection must not install packages or execute their scripts. Add distributions
through the catalog and implement new formats explicitly, without silently
converting packages.

Do not introduce `shell=True`, global Podman cleanup or global rpm-ostree
rollback/reset operations. Never log passwords, tokens or credentials.

## Verification

Run checks relevant to the change. Installation, cleanup, recovery and confirmation
changes need success and failure coverage. Verify simple visual changes directly
in the interface. The README lists commands for translation validation, Ruff,
pytest and package builds; see [Testing](docs/testing.md) for release validation.

Failure reports should include tool versions, the operation stage and logs reviewed
for personal data. State whether a result came from a simulated backend or a real
desktop, including the packages, architectures and container images exercised.
