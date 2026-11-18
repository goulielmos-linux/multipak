import configparser
import json
import subprocess
from pathlib import Path

import pytest

from multipak import android_link_choice as choice
from multipak.integration import DESKTOP_ID

DEMO = "x-scheme-handler/demo"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(choice.host, "which", lambda name: None)
    applications = tmp_path / "data/applications"
    applications.mkdir(parents=True)
    (applications / "multipak-aaa-links.desktop").write_text(
        "[Desktop Entry]\nType=Application\n"
        "MimeType=x-scheme-handler/demo;x-scheme-handler/solo;x-scheme-handler/Bad_;\n"
    )
    (applications / "other.desktop").write_text(
        "[Desktop Entry]\nMimeType=x-scheme-handler/elsewhere;\n"
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config/mimeapps.list").write_text(
        f"[Default Applications]\n{DEMO}=linux-demo.desktop;\ntext/plain=editor.desktop;\n"
    )
    return tmp_path


def two_for_demo(scheme):
    programs = [("android.desktop", "/a/android.desktop", "Demo — Android 13", "")]
    if scheme == "demo":
        programs.append(("linux-demo.desktop", "/usr/share/applications/linux-demo.desktop", "Demo", "demo"))
    return programs


def mimeapps(home):
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    config.read(home / "config/mimeapps.list")
    return config


def test_only_android_link_shortcuts_are_read(home):
    assert choice.declared_schemes(home / "data/applications") == {"demo", "solo"}


def test_asking_takes_only_the_addresses_in_conflict_and_gives_them_back(home):
    assert choice.refresh(True, find=two_for_demo) == ["demo"]
    config = mimeapps(home)
    assert config["Default Applications"][DEMO] == DESKTOP_ID + ";"
    assert config["Added Associations"][DEMO] == DESKTOP_ID + ";"
    assert config["Default Applications"]["text/plain"] == "editor.desktop;"
    assert "x-scheme-handler/solo" not in config["Default Applications"]
    state = json.loads((home / "config/multipak/android-links.json").read_text())
    assert state == {"ask": True, "before": {DEMO: "linux-demo.desktop"}, "owned": ["demo"]}
    assert choice.asking()
    assert choice.scheme_of("demo://login?code=1") == "demo"
    assert choice.scheme_of("DEMO://x") == "demo"
    assert choice.scheme_of("solo://x") is None
    assert choice.scheme_of("/home/user/package.rpm") is None
    assert choice.scheme_of("snap:hello") is None

    assert choice.refresh(False, find=two_for_demo) == []
    config = mimeapps(home)
    assert config["Default Applications"][DEMO] == "linux-demo.desktop;"
    assert DEMO not in config["Added Associations"]
    assert choice.scheme_of("demo://x") is None
    assert not choice.asking()


def test_a_conflict_that_ends_gives_the_default_back(home):
    choice.refresh(True, find=two_for_demo)
    assert choice.refresh(find=lambda scheme: two_for_demo("solo")) == []
    assert mimeapps(home)["Default Applications"][DEMO] == "linux-demo.desktop;"
    assert choice.asking()  # The choice itself stays.


def test_without_a_previous_default_the_entry_is_removed(home):
    (home / "config/mimeapps.list").write_text("[Default Applications]\n")
    choice.refresh(True, find=two_for_demo)
    choice.refresh(False, find=two_for_demo)
    assert DEMO not in mimeapps(home)["Default Applications"]


def test_the_question_is_on_by_default(home):
    assert choice.asking() is True
    choice.refresh(find=two_for_demo)
    assert mimeapps(home)["Default Applications"][DEMO]


def test_nothing_changes_while_the_option_is_off(home):
    choice.refresh(False, find=lambda _scheme: [])
    before = (home / "config/mimeapps.list").read_bytes()
    assert choice.refresh(find=two_for_demo) == []
    assert (home / "config/mimeapps.list").read_bytes() == before


def test_programs_are_read_from_glib_without_multipak(home, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=(
            f"{DESKTOP_ID}\t/x/{DESKTOP_ID}\tMultiPak\t\n"
            "linux-demo.desktop\t/usr/share/applications/linux-demo.desktop\tDemo\tdemo\n"
            "linux-demo.desktop\t/elsewhere/linux-demo.desktop\tDemo again\t\n"
            "android.desktop\t/a/android.desktop\t\t/a/icon.png\n"
            "broken line\n"
            "relative.desktop\trelative.desktop\tRelative\t\n"
        ))

    monkeypatch.setattr(choice.host, "run", run)
    assert choice.handlers("demo") == [
        ("linux-demo.desktop", "/usr/share/applications/linux-demo.desktop", "Demo", "demo"),
        ("android.desktop", "/a/android.desktop", "android", "/a/icon.png"),
    ]
    assert calls[0][:2] == ["sh", "-c"] and calls[0][-1] == "demo"
    assert choice.handlers("Bad scheme") == [] and len(calls) == 1


def test_the_chosen_program_opens_directly(monkeypatch):
    calls = []
    monkeypatch.setattr(choice.host, "run", lambda argv, **_: calls.append(argv))
    choice.launch(Path("/usr/share/applications/linux-demo.desktop"), "demo://x?a=1")
    assert calls == [[
        "python3", "-I", "-c", choice.LAUNCH,
        "/usr/share/applications/linux-demo.desktop", "demo://x?a=1",
    ]]
    assert "launch_uris([sys.argv[2]]" in choice.LAUNCH
    with pytest.raises(ValueError):
        choice.launch("/x.desktop", "demo://x\n--help")
    assert len(calls) == 1


def test_the_installer_routes_addresses_in_conflict_to_the_choice():
    source = (Path(choice.__file__).parent / "kirigami/bootstrap.py").read_text()
    assert "if len(sys.argv) == 2 and scheme_of(sys.argv[1]):" in source
    assert "from multipak.kirigami.link_choice import main" in source
    android = (Path(choice.__file__).parent / "android.py").read_text()
    assert "android_link_choice.refresh()" in android
