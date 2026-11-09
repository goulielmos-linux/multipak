import pytest

from multipak.bazaar import application_search_term, search_command


def test_chrome_search_uses_app_name_instead_of_rpm_channel_suffix():
    assert application_search_term("google-chrome-stable") == "google chrome"


def test_uses_supported_search_for_option(monkeypatch):
    monkeypatch.setattr(
        "multipak.bazaar.host.which",
        lambda name: "/usr/bin/bazaar" if name == "bazaar" else None,
    )

    class Runner:
        def run(self, argv, **_kwargs):
            assert argv == ["/usr/bin/bazaar", "--help"]
            return "Options: --search-for=TERM"

    result = search_command(Runner(), "-demo; $(literal)")
    assert result.argv == ["/usr/bin/bazaar", "--search-for=demo; $(literal)"]
    assert result.search_supported


def test_older_bazaar_opens_without_invented_options(monkeypatch):
    monkeypatch.setattr(
        "multipak.bazaar.host.which",
        lambda name: "/usr/bin/bazaar" if name == "bazaar" else None,
    )

    class Runner:
        def run(self, *_args, **_kwargs):
            return "Usage: bazaar"

    result = search_command(Runner(), "demo")
    assert result.argv == ["/usr/bin/bazaar"]
    assert not result.search_supported


def test_missing_bazaar_is_actionable(monkeypatch):
    monkeypatch.setattr("multipak.bazaar.host.which", lambda _: None)
    with pytest.raises(RuntimeError, match="is not installed"):
        search_command(None, "demo")
