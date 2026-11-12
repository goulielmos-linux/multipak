# Contributing

Use English for source text, comments and documentation. Put translated interface
text in the [language catalogs](docs/development.md#translations).

Keep interface code separate from package operations. Package inspection must
never execute package scripts. Pass commands as argument lists, avoid global
cleanup or rollback, and keep credentials out of logs.

Run the [development checks](docs/development.md#development-checks) relevant to
your change. Verify visual changes in the interface and cover both success and
failure when changing installation, cleanup or recovery.

When reporting a failure, include the package format, tool versions and operation
stage. Remove personal data from logs and say whether the result came from a
simulated test or a real desktop.
