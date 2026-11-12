import pytest

from multipak import host
from multipak.process import Cancelled, CommandError
from multipak.software_store import application_search_term, search_command


class ProbeRunner:
    def __init__(self, *, default="", installed="", help_text=None, failure=None):
        self.default = default
        self.installed = installed
        self.help_text = help_text or {}
        self.failure = failure or {}
        self.calls = []

    def run(self, argv, **_kwargs):
        self.calls.append(argv)
        key = tuple(argv)
        if key in self.failure:
            raise self.failure[key]
        if argv[1:2] == ["query"]:
            return self.default
        if argv[1:2] == ["list"]:
            return self.installed
        return self.help_text[key]


@pytest.fixture
def installed_tools(monkeypatch):
    def configure(*names):
        monkeypatch.setattr(
            host, "which", lambda name: f"/usr/bin/{name}" if name in names else None
        )

    return configure


def test_chrome_search_uses_app_name_instead_of_rpm_channel_suffix():
    assert application_search_term("google-chrome-stable") == "google chrome"


@pytest.mark.parametrize(
    ("binary", "option"),
    [("bazaar", "--search-for"), ("gnome-software", "--search"), ("plasma-discover", "--search")],
)
def test_search_term_is_one_literal_argument(installed_tools, binary, option):
    installed_tools(binary)
    runner = ProbeRunner(help_text={(f"/usr/bin/{binary}", "--help"): f"Options: {option}=TERM"})
    result = search_command(runner, "-demo; $(literal)")
    assert result.argv == [f"/usr/bin/{binary}", f"{option}=demo; $(literal)"]
    assert result.search_supported


@pytest.mark.parametrize(
    ("desktop", "binary", "option"),
    [
        ("org.gnome.Software.desktop", "gnome-software", "--search"),
        ("org.kde.discover.desktop", "plasma-discover", "--search"),
        ("io.github.kolunmi.Bazaar.desktop", "bazaar", "--search-for"),
    ],
)
def test_respects_supported_default_when_multiple_stores_are_installed(
    installed_tools, desktop, binary, option
):
    installed_tools("bazaar", "gnome-software", "plasma-discover", "xdg-mime")
    runner = ProbeRunner(
        default=desktop + "\n",
        help_text={
            ("/usr/bin/bazaar", "--help"): "--search-for=TERM",
            ("/usr/bin/gnome-software", "--help"): "--search=TERM",
            ("/usr/bin/plasma-discover", "--help"): "--search=TERM",
        },
    )
    assert search_command(runner, "demo").argv == [f"/usr/bin/{binary}", f"{option}=demo"]
    assert runner.calls[0] == [
        "/usr/bin/xdg-mime",
        "query",
        "default",
        "x-scheme-handler/appstream",
    ]


def test_does_not_execute_an_unknown_default_desktop_file(installed_tools):
    installed_tools("bazaar", "xdg-mime")
    runner = ProbeRunner(
        default="/tmp/untrusted.desktop",
        help_text={("/usr/bin/bazaar", "--help"): "--search-for=TERM"},
    )
    assert search_command(runner, "demo").argv == ["/usr/bin/bazaar", "--search-for=demo"]
    assert not any("untrusted" in argument for call in runner.calls for argument in call)


def test_unavailable_default_falls_back_to_installed_store(installed_tools):
    installed_tools("gnome-software", "xdg-mime")
    runner = ProbeRunner(
        default="io.github.kolunmi.Bazaar.desktop",
        help_text={("/usr/bin/gnome-software", "--help"): "--search=TERM"},
    )
    assert search_command(runner, "demo").argv == ["/usr/bin/gnome-software", "--search=demo"]


@pytest.mark.parametrize(
    ("app_id", "branch", "installation", "scope", "option"),
    [
        ("io.github.kolunmi.Bazaar", "stable", "user", "--user", "--search-for"),
        ("org.gnome.Software", "stable", "system", "--system", "--search"),
        ("org.gnome.Software", "beta", "shared", "--installation=shared", "--search"),
        ("org.kde.discover", "stable", "user", "--user", "--search"),
        ("org.kde.discover", "stable", "system", "--system", "--search"),
        ("org.kde.discover", "beta", "shared", "--installation=shared", "--search"),
    ],
)
def test_flatpak_store_uses_its_installed_branch_and_scope(
    installed_tools, app_id, branch, installation, scope, option
):
    installed_tools("flatpak")
    command = ("/usr/bin/flatpak", "run", scope, f"--branch={branch}", app_id)
    runner = ProbeRunner(
        installed=f"{app_id}\t{branch}\t{installation}\n",
        help_text={(*command, "--help"): f"{option}=TERM"},
    )
    assert search_command(runner, "demo").argv == [*command, f"{option}=demo"]


@pytest.mark.parametrize(
    ("app_id", "option"),
    [("io.github.kolunmi.Bazaar", "--search-for"), ("org.kde.discover", "--search")],
)
def test_default_flatpak_store_takes_priority_over_other_native_store(
    installed_tools, app_id, option
):
    installed_tools("flatpak", "gnome-software", "xdg-mime")
    command = ("/usr/bin/flatpak", "run", "--user", "--branch=stable", app_id)
    runner = ProbeRunner(
        default=f"{app_id}.desktop",
        installed=f"{app_id}\tstable\tuser\n",
        help_text={(*command, "--help"): f"{option}=TERM"},
    )
    assert search_command(runner, "demo").argv == [*command, f"{option}=demo"]
    assert not any(call[0] == "/usr/bin/gnome-software" for call in runner.calls)


def test_prefer_user_stable_when_multiple_flatpak_copies_exist(installed_tools):
    installed_tools("flatpak")
    command = ("/usr/bin/flatpak", "run", "--user", "--branch=stable", "io.github.kolunmi.Bazaar")
    runner = ProbeRunner(
        installed=(
            "io.github.kolunmi.Bazaar\tbeta\tuser\n"
            "io.github.kolunmi.Bazaar\tstable\tsystem\n"
            "io.github.kolunmi.Bazaar\tstable\tuser\n"
        ),
        help_text={(*command, "--help"): "--search-for=TERM"},
    )
    assert search_command(runner, "demo").argv == [*command, "--search-for=demo"]


@pytest.mark.parametrize("binary", ["bazaar", "plasma-discover"])
def test_old_store_opens_without_invented_search_options(installed_tools, binary):
    installed_tools(binary)
    runner = ProbeRunner(help_text={(f"/usr/bin/{binary}", "--help"): f"Usage: {binary}"})
    result = search_command(runner, "google-chrome-stable")
    assert result.argv == [f"/usr/bin/{binary}"]
    assert not result.search_supported
    assert result.term == "google chrome"


def test_does_not_confuse_search_options_with_longer_names(installed_tools):
    installed_tools("gnome-software")
    runner = ProbeRunner(help_text={("/usr/bin/gnome-software", "--help"): "--search-for=TERM"})
    result = search_command(runner, "demo")
    assert result.argv == ["/usr/bin/gnome-software"]
    assert not result.search_supported


def test_broken_store_does_not_prevent_another_store_from_opening(installed_tools):
    installed_tools("bazaar", "gnome-software")
    runner = ProbeRunner(
        failure={("/usr/bin/bazaar", "--help"): CommandError(["bazaar"], 1, "broken")},
        help_text={("/usr/bin/gnome-software", "--help"): "--search=TERM"},
    )
    assert search_command(runner, "demo").argv == ["/usr/bin/gnome-software", "--search=demo"]


def test_failed_default_and_flatpak_queries_do_not_block_native_store(installed_tools):
    installed_tools("gnome-software", "xdg-mime", "flatpak")
    runner = ProbeRunner(
        failure={
            ("/usr/bin/xdg-mime", "query", "default", "x-scheme-handler/appstream"): TimeoutError(),
            (
                "/usr/bin/flatpak",
                "list",
                "--app",
                "--columns=application,branch,installation",
            ): OSError(),
        },
        help_text={("/usr/bin/gnome-software", "--help"): "--search=TERM"},
    )
    assert search_command(runner, "demo").search_supported


def test_store_detection_can_be_cancelled(installed_tools):
    installed_tools("xdg-mime")
    runner = ProbeRunner(
        failure={
            ("/usr/bin/xdg-mime", "query", "default", "x-scheme-handler/appstream"): Cancelled()
        }
    )
    with pytest.raises(Cancelled):
        search_command(runner, "demo")


def test_missing_stores_are_actionable(installed_tools):
    installed_tools()
    with pytest.raises(RuntimeError, match="Bazaar, GNOME Software or KDE Discover"):
        search_command(ProbeRunner(), "demo")


@pytest.mark.parametrize("binary", ["gnome-software", "plasma-discover"])
def test_flatpak_probes_resolve_tools_on_the_host(monkeypatch, binary):
    monkeypatch.setattr(host, "is_flatpak", lambda: True)
    commands = []

    def run(argv, **_kwargs):
        from subprocess import CompletedProcess

        commands.append(argv)
        return CompletedProcess(argv, 0, f"/usr/bin/{argv[-1]}\n", "")

    monkeypatch.setattr(host, "run", run)
    monkeypatch.setattr(host.shutil, "which", lambda _: pytest.fail("Sandbox lookup used"))
    assert host.which(binary) == f"/usr/bin/{binary}"
    assert commands[-1][-1] == binary
