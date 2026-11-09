import configparser

from multipak.desktop import quote_exec, render_desktop


def test_prefixes_all_execs_and_disables_host_only_checks():
    content = (
        "[Desktop Entry]\nType=Application\nName=Demo\nExec=/opt/demo --url %U\n"
        "TryExec=/opt/demo\nPath=/opt\nDBusActivatable=true\nIcon=/opt/icon.png\n"
        "Actions=New;\n[Desktop Action New]\nName=New\nExec=/opt/demo --new %u\n"
    )
    result = render_desktop(content, "multipak-pending-app-abcd", "/usr/bin/distrobox")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(result)
    main = parser["Desktop Entry"]
    assert main["exec"].endswith("/opt/demo --url %U")
    assert '"multipak-pending-app-abcd"' in main["exec"]
    assert "tryexec" not in main
    assert "path" not in main
    assert main["dbusactivatable"] == "false"
    assert main["icon"] == "application-x-executable"
    assert '"/usr/bin/distrobox"' in parser["Desktop Action New"]["exec"]


def test_hidden_and_cli_only_entries_are_not_exported():
    assert (
        render_desktop(
            "[Desktop Entry]\nType=Application\nExec=demo\nNoDisplay=true\n",
            "box",
            "distrobox",
        )
        is None
    )


def test_quotes_literal_percent_and_special_characters():
    result = quote_exec('/a path/100%/$x`"\\')
    assert result.startswith('"') and result.endswith('"')
    assert "100%%" in result
    assert "\\\\$" in result
