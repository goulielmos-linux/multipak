import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from multipak import android
from multipak.android_apk import ANDROID_NS, browsable_schemes
from multipak.models import Package, Transaction

VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"


def element(parents, tag, **attrs):
    return (tuple(parents), tag, {(ANDROID_NS, key): (3, value) for key, value in attrs.items()})


def intent_filter(owner, actions=(), categories=(), schemes=()):
    inside = ("manifest", "application", owner, "intent-filter")
    return [
        element(inside[:-1], "intent-filter"),
        *(element(inside, "action", name=a) for a in actions),
        *(element(inside, "category", name=c) for c in categories),
        *(element(inside, "data", scheme=s) for s in schemes),
    ]


VIEW = "android.intent.action.VIEW"
BROWSABLE = "android.intent.category.BROWSABLE"


def test_only_specific_browsable_view_schemes_are_kept():
    elements = [
        element((), "manifest"),
        *intent_filter("activity", [VIEW], [BROWSABLE], ["MyApp", "https", "bad_scheme", "fb123"]),
        *intent_filter("activity", [VIEW], [], ["notbrowsable"]),
        *intent_filter("activity", ["android.intent.action.SEND"], [BROWSABLE], ["share"]),
        *intent_filter("activity-alias", [VIEW], [BROWSABLE], ["alias", "mailto", "intent"]),
        element(("manifest", "application", "activity"), "meta-data", name="ignored"),
    ]
    assert browsable_schemes(elements) == ("alias", "fb123", "myapp")


def test_scheme_list_is_limited():
    schemes = [f"app{i:02d}" for i in range(40)]
    elements = intent_filter("activity", [VIEW], [BROWSABLE], schemes)
    assert len(browsable_schemes(elements)) == 16


def transaction(store, instance="androida"):
    tx = Transaction(
        "b" * 32, "Demo", "", "org.example.demo", "1", "android", "",
        method="waydroid", status="installed", android_instance_id=instance,
        android_package_id="org.example.demo", android_environment_label="Android 13",
        launchers=[f"multipak-waydroid.{instance}.org.example.demo.desktop"],
    )
    store.save(tx)
    return tx


def test_link_handler_registers_schemes_without_becoming_default(store, monkeypatch):
    calls = []
    monkeypatch.setattr(android.host, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(android.host, "run", lambda argv, **_: calls.append(argv))
    tx = transaction(store)
    engine = SimpleNamespace(store=store)
    package = Package(Path("a.apk"), "android", "Demo\nApp", "1", "x86_64",
                      android_schemes=("demo", "demo-login"))
    android._link_handler(engine, tx, package)
    name = f"multipak-{tx.id}-links.desktop"
    text = (store.applications / name).read_text()
    assert "Name=Demo App — Android 13\n" in text
    assert "NoDisplay=true\n" in text
    assert "MimeType=x-scheme-handler/demo;x-scheme-handler/demo-login;\n" in text
    assert '"--instance" "androida" "open-uri" "org.example.demo" %u\n' in text
    assert name in store.transactions()[0].launchers
    assert not list(store.applications.glob("*.tmp"))
    # GLib applications only see new handlers after mimeinfo.cache is rebuilt.
    assert calls == [
        ["/usr/bin/update-desktop-database", str(store.applications)],
        ["/usr/bin/kbuildsycoca6"],
    ]
    assert "mimeapps.list" not in {p.name for p in store.applications.parent.rglob("*")}

    # An update without schemes removes the registration.
    android._link_handler(engine, tx, Package(Path("a.apk"), "android", "Demo", "1", "x86_64"))
    assert not (store.applications / name).exists()
    assert name not in store.transactions()[0].launchers
    assert len(calls) == 4


@pytest.fixture
def links(monkeypatch, tmp_path):
    context = SimpleNamespace(
        current=lambda: SimpleNamespace(
            user_state=tmp_path, exchange=tmp_path / "exchange", owner=1000,
            home=str(tmp_path), id="androida", data=tmp_path / "data", label="Android 13",
            command=lambda *a: " ".join(["/usr/local/bin/multipak-waydroid", "--instance", "androida", *a]),
            desktop_name=lambda package: f"multipak-waydroid.androida.{package}.desktop",
        )
    )
    monkeypatch.setitem(sys.modules, "multipak_instance", context)
    for name in ("multipak_input", "multipak_outbox", "multipak_files", "multipak_links"):
        spec = importlib.util.spec_from_file_location(name, VENDOR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return module


class Manager:
    def __init__(self):
        self.calls = 0

    def SetDefaultBrowser(self):
        self.calls += 1


class Platform:
    def __init__(self):
        self.secure = {}

    def settingsPutString(self, namespace, key, value):
        assert namespace == 0
        self.secure[key] = value

    def settingsGetString(self, namespace, key):
        return self.secure.get(key)


def test_role_command_is_fixed(links):
    assert links.ROLE_COMMAND == (
        "role", "add-role-holder", "--user", "0", "android.app.role.BROWSER",
        "io.github.goulielmos_linux.multipak.controller",
    )


def test_links_open_on_linux_by_default(links, monkeypatch):
    manager, platform = Manager(), Platform()
    monkeypatch.setattr(links.multipak_input, "ensure_mapper", lambda *_: True)
    links.ensure(platform, manager)
    assert platform.secure == {"multipak_links": "1"} and manager.calls == 1


def test_browser_role_is_applied_once_and_again_after_helper_update(links, monkeypatch, tmp_path):
    manager, platform = Manager(), Platform()
    fresh = iter([True, False, True])
    monkeypatch.setattr(links.multipak_input, "ensure_mapper", lambda *_: next(fresh))
    assert links.ensure(platform, manager) is True
    assert links.ensure(platform, manager) is False  # A browser chosen later in Android stays.
    assert links.ensure(platform, manager) is True
    assert manager.calls == 2 and platform.secure["multipak_links"] == "1"
    saved = json.loads((tmp_path / "input-preferences.json").read_text())
    assert saved["links"] is True and saved["links_applied"] is True


def test_turning_links_off_is_remembered_and_keeps_the_role(links, monkeypatch):
    manager, platform = Manager(), Platform()
    monkeypatch.setattr(links.multipak_input, "ensure_mapper", lambda *_: True)
    links.apply(platform, manager, True, force=True)
    links.apply(platform, manager, False, force=True)
    links.ensure(platform, manager)
    assert manager.calls == 1 and platform.secure["multipak_links"] == "0"


def test_android_must_confirm_the_setting(links):
    platform = Platform()
    platform.settingsPutString = lambda *_: None
    with pytest.raises(RuntimeError, match="confirm"):
        links.apply(platform, Manager(), True, force=True)


@pytest.mark.parametrize(
    "uri", ["myapp://login?code=1", "fb123:/authorize", "spotify:track:abc"]
)
def test_app_addresses_are_accepted(links, uri):
    assert links.check_uri(uri) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "", "https://example.org", "HTTP://x", "file:///etc/passwd", "content://x",
        "intent://x#Intent;end", "market://details?id=x", "javascript:alert(1)",
        "myapp://a b", "myapp://\n", "myapp://ç", "no-scheme", "myapp://" + "a" * 4096,
    ],
)
def test_other_addresses_are_refused(links, uri):
    with pytest.raises(ValueError):
        links.check_uri(uri)


def test_intent_and_market_need_their_own_reading(links):
    for uri in ("intent://x#Intent;scheme=myapp;end", "market://details?id=org.example.demo"):
        with pytest.raises(ValueError):
            links.check_uri(uri)


@pytest.fixture
def outbox(links, monkeypatch, tmp_path):
    """A fake Android /data with the helper installed as the test user."""
    module = sys.modules["multipak_outbox"]
    uid = os.getuid()
    monkeypatch.setattr(module, "FIRST_APP_UID", 0)
    monkeypatch.setattr(module, "SYSTEM_UIDS", (0, 1000, uid))
    system = tmp_path / "data/system"
    system.mkdir(parents=True)
    (system / "packages.list").write_text(
        f"com.example.other 10200 0 /data/user/0/com.example.other\n"
        f"io.github.multipak.gestures 10300 0 /data/user/0/io.github.multipak.gestures default\n"
        f"io.github.goulielmos_linux.multipak.controller {uid} 0 /data/user/0/io.github.goulielmos_linux.multipak.controller default\n"
    )
    folder = tmp_path / "data/data/io.github.goulielmos_linux.multipak.controller/files/outbox"
    folder.mkdir(parents=True)
    module.folder = folder
    return module


def request(folder, name, uri=None, data=None):
    path = folder / name
    path.write_bytes(data if data is not None else json.dumps({"v": 1, "uri": uri}).encode())
    return path


def test_outbox_links_are_returned_in_order_and_deleted(outbox):
    request(outbox.folder, "200-b.json", "mailto:teste@example.org?subject=Ola%20MultiPak")
    request(outbox.folder, "100-a.json", "https://example.org/teste?a=1&b=2")
    assert outbox.take() == [
        "https://example.org/teste?a=1&b=2",
        "mailto:teste@example.org?subject=Ola%20MultiPak",
    ]
    assert not list(outbox.folder.iterdir())


@pytest.mark.parametrize(
    "data",
    [
        b'{"v":1,"uri":"file:///etc/passwd"}',
        b'{"v":1,"uri":"javascript:alert(1)"}',
        b'{"v":1,"uri":"intent://x#Intent;end"}',
        b'{"v":1,"uri":"https:example.org"}',
        b'{"v":1,"uri":"https://exa mple.org"}',
        b'{"v":1,"uri":"https://example.org/\\n"}',
        b'{"v":1,"uri":"https://example.org/\\u00e7"}',
        b'{"v":1,"uri":"mailto:"}',
        b'{"v":1,"uri":"https://' + b"a" * 4096 + b'"}',
        b'{"v":2,"uri":"https://example.org"}',
        b'{"v":true,"uri":"https://example.org"}',
        b'{"v":"1","uri":"https://example.org"}',
        b'{"v":1,"uri":"https://example.org","extra":1}',
        b'{"v":1,"uri":"https://example.org","uri":"https://evil.example"}',
        b'{"v":1,"uri":["https://example.org"]}',
        b'["https://example.org"]',
        b'{"v":1,"uri":"https://example.org"',
        b'{"v":NaN,"uri":"https://example.org"}',
        '{"v":1,"uri":"https://exemplo.org/ç"}'.encode(),
        b"[" * 6000 + b"]" * 6000,
    ],
)
def test_malformed_requests_are_dropped(outbox, data):
    request(outbox.folder, "1-a.json", data=data)
    assert outbox.take() == []
    assert not list(outbox.folder.iterdir())


def test_requests_that_are_not_plain_files_from_the_helper_are_dropped(outbox, tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"v": 1, "uri": "https://secret.example"}))
    (outbox.folder / "1-a.json").symlink_to(secret)
    os.link(request(tmp_path, "linked.json", "https://linked.example"), outbox.folder / "2-a.json")
    os.mkfifo(outbox.folder / "3-a.json")
    (outbox.folder / "4-a.json").mkdir()
    request(outbox.folder, "5-a.json", data=b" " * 8193)
    old = request(outbox.folder, "6-a.json", "https://old.example")
    os.utime(old, (time.time() - 600,) * 2)
    request(outbox.folder, "unexpected-name.json", "https://name.example")
    assert outbox.take() == []
    assert secret.exists() and (tmp_path / "linked.json").exists()
    assert [p.name for p in outbox.folder.iterdir()] == ["4-a.json"]


def test_requests_must_belong_to_the_helper_user(outbox, tmp_path):
    packages = tmp_path / "data/system/packages.list"
    packages.write_text(packages.read_text().replace(f" {os.getuid()} ", f" {os.getuid() + 1} "))
    request(outbox.folder, "1-a.json", "https://example.org")
    assert outbox.take() == []
    assert not list(outbox.folder.iterdir())  # Read and refused, not skipped.


def test_missing_helper_or_outbox_is_not_an_error(outbox, tmp_path):
    outbox.folder.rmdir()
    assert outbox.take() == []
    (tmp_path / "data/system/packages.list").unlink()
    assert outbox.take() == []


def test_an_outbox_replaced_by_a_link_is_never_followed(outbox, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "outbox").mkdir(parents=True)
    request(elsewhere / "outbox", "1-a.json", "https://example.org")
    files = outbox.folder.parent
    outbox.folder.rmdir()
    outbox.folder.symlink_to(elsewhere / "outbox")
    assert outbox.take() == []
    outbox.folder.unlink()
    files.rmdir()
    files.symlink_to(elsewhere)
    assert outbox.take() == []
    assert (elsewhere / "outbox/1-a.json").exists()



def media(folder, name, state):
    return request(folder, name, data=json.dumps({"v": 1, "media": state}).encode())


def test_the_newest_media_state_is_kept_apart_from_the_links(outbox):
    playing = {"package": "org.example.music", "state": "playing", "title": "Canção 🎵", "artist": "Banda"}
    media(outbox.folder, "100-a.json", {"package": "org.example.music", "state": "paused"})
    request(outbox.folder, "150-b.json", "https://example.org")
    old = media(outbox.folder, "200-c.json", playing)
    os.utime(old, (time.time() - 3600, time.time() - 3600))  # A state holds until it changes.
    assert outbox.take() == ["https://example.org"]
    assert outbox.media() == playing
    assert not list(outbox.folder.iterdir())
    media(outbox.folder, "150-d.json", {"package": "org.example.music", "state": "stopped"})
    outbox.take()
    assert outbox.media() == playing  # An older file read later does not win.
    media(outbox.folder, "300-e.json", {})
    outbox.take()
    assert outbox.media() == {}


@pytest.mark.parametrize(
    "state",
    [
        {"package": "org.example.music"},
        {"state": "playing"},
        {"package": "org.example.music", "state": "loud"},
        {"package": "org.example.music", "state": "playing", "album": "x"},
        {"package": "Not a package", "state": "playing"},
        {"package": "org.example.music", "state": "playing", "title": "a\nb"},
        {"package": "org.example.music", "state": "playing", "title": "\ud800"},
        {"package": "org.example.music", "state": "playing", "title": ""},
        {"package": "org.example.music", "state": "playing", "title": "a" * 257},
        {"package": "org.example.music", "state": "playing", "artist": ["a"]},
        ["org.example.music"],
    ],
)
def test_malformed_media_states_are_dropped(outbox, state):
    media(outbox.folder, "100-a.json", {"package": "org.example.music", "state": "paused"})
    media(outbox.folder, "200-b.json", state)
    assert outbox.take() == []
    assert outbox.media() == {"package": "org.example.music", "state": "paused"}
    assert not list(outbox.folder.iterdir())


def test_a_media_state_with_other_keys_is_not_a_link(outbox):
    request(outbox.folder, "1-a.json", data=b'{"v":1,"uri":"https://example.org","media":{}}')
    assert outbox.take() == []
    assert outbox.media() == {}


def test_a_new_session_starts_without_media(outbox, monkeypatch):
    media(outbox.folder, "100-a.json", {"package": "org.example.music", "state": "playing"})
    outbox.take()

    class Monitor:
        def connect(self, *_):
            pass

    gio = SimpleNamespace(
        File=SimpleNamespace(new_for_path=lambda _: SimpleNamespace(monitor_directory=lambda *_: Monitor())),
        FileMonitorFlags=SimpleNamespace(WATCH_MOVES=0),
    )
    repository = ModuleType("gi.repository")
    repository.Gio, repository.GLib = gio, SimpleNamespace()
    monkeypatch.setitem(sys.modules, "gi", ModuleType("gi"))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    outbox.watch(lambda: None)
    assert outbox.media() == {}

def test_a_flood_of_links_is_limited(outbox):
    for number in range(100):
        request(outbox.folder, f"{number:03d}-a.json", f"https://example.org/{number}")
    now = time.time()
    first = outbox.take(now)
    assert len(first) == len(set(first)) == outbox.BURST
    assert len(list(outbox.folder.iterdir())) == 100 - outbox.MAX_SCAN
    assert outbox.take(now + 1) == []  # Read and deleted, but not opened.
    request(outbox.folder, "999-a.json", "https://example.org/later")
    assert outbox.take(now + outbox.BURST_SECONDS + 1) == ["https://example.org/later"]


INSTALLED = {"org.example.demo"}


def installed(package):
    return package in INSTALLED


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("myapp://login?code=1", ("android", "myapp://login?code=1")),
        (
            "intent://open/item?id=7#Intent;scheme=myapp;package=org.example.demo;"
            "action=android.intent.action.DELETE;component=org.evil/.X;S.token=abc;end",
            ("android", "myapp://open/item?id=7"),
        ),
        (
            "intent://open#Intent;scheme=myapp;package=org.example.missing;"
            "S.browser_fallback_url=https%3A%2F%2Fexample.org%2Fa%3Fb%3D1;end",
            ("linux", "https://example.org/a?b=1"),
        ),
        (
            "intent://maps.example.org/place#Intent;scheme=https;package=org.example.demo;end",
            ("linux", "https://maps.example.org/place"),
        ),
        (
            "intent://open#Intent;scheme=myapp;package=org.example.missing;end",
            ("linux", "https://play.google.com/store/apps/details?id=org.example.missing"),
        ),
        (
            "market://details?id=org.example.missing&referrer=x",
            ("linux", "https://play.google.com/store/apps/details?id=org.example.missing"),
        ),
    ],
)
def test_linux_addresses_are_routed(links, uri, expected):
    assert links.route(uri, installed) == expected


def test_market_opens_the_android_store_when_there_is_one(links):
    with_store = INSTALLED | {"org.fdroid.fdroid"}
    assert links.route("MARKET://details?id=org.example.x&url=evil", with_store.__contains__) == (
        "android", "market://details?id=org.example.x",
    )
    route = links.route(
        "intent://x#Intent;scheme=myapp;package=org.example.x;end", with_store.__contains__
    )
    assert route == ("android", "market://details?id=org.example.x")


@pytest.mark.parametrize(
    "uri",
    [
        "intent://x",
        "intent://x#Intent;scheme=myapp;package=org.example.demo",
        "intent://x#Other;scheme=myapp;end",
        "intent://x#Intent;scheme=myapp;scheme=other;package=org.example.demo;end",
        "intent://x#Intent;scheme=my_app;package=org.example.demo;end",
        "intent://x#Intent;scheme=myapp;package=bad;end",
        "intent://x#Intent;S.browser_fallback_url=javascript%3Aalert(1);end",
        "intent://x#Intent;S.browser_fallback_url=https%3A%2F%2Fa%20b;end",
        "intent://x#Intent;action=android.intent.action.VIEW;end",
        "intent://x#Intent;scheme=myapp;end",
        "market://search?q=demo",
        "market://details?id=bad",
        "market://details?id=org.a.b&id=org.c.d",
        "market://details?id=org.example.demo ",
    ],
)
def test_bad_intent_and_market_addresses_are_refused(links, uri):
    with pytest.raises(ValueError):
        links.route(uri, installed)


def test_one_shortcut_per_environment_opens_intent_and_market(links, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr("subprocess.run", lambda argv, **_: calls.append(argv))
    assert links.register(tmp_path) is True
    path = tmp_path / "multipak-waydroid.androida.links.desktop"
    assert path.read_text() == (
        "[Desktop Entry]\nType=Application\nName=Android links — Android 13\n"
        "Exec=/usr/local/bin/multipak-waydroid --instance androida open-uri %u\n"
        "Icon=waydroid\nTerminal=false\nNoDisplay=true\n"
        "MimeType=x-scheme-handler/intent;x-scheme-handler/market;\n"
    )
    assert calls == [["/usr/bin/update-desktop-database", str(tmp_path)], ["/usr/bin/kbuildsycoca6"]]
    assert links.register(tmp_path) is False and len(calls) == 2  # Unchanged: no re-indexing.
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_without_an_app_only_intent_and_market_are_read(links):
    assert links.route("market://details?id=org.example.x", installed, app=False)[0] == "linux"
    with pytest.raises(ValueError):
        links.route("myapp://login", installed, app=False)


@pytest.fixture
def app_manager(monkeypatch):
    calls = []
    module = ModuleType("tools.actions.app_manager")
    module.intent = lambda args: calls.append(("android", args.ACTION, args.URI))
    module.launch = lambda args: calls.append(("launch", args.PACKAGE))
    tools, actions = ModuleType("tools"), ModuleType("tools.actions")
    tools.actions, actions.app_manager = actions, module
    for name, value in (("tools", tools), ("tools.actions", actions), (module.__name__, module)):
        monkeypatch.setitem(sys.modules, name, value)
    return calls


def test_open_uri_takes_the_app_from_the_address(links, monkeypatch, app_manager):
    monkeypatch.setattr(links, "installed", installed)
    monkeypatch.setattr(links, "open_on_linux", lambda uri: app_manager.append(("linux", uri)))
    links.open_uri(SimpleNamespace(PACKAGE="market://details?id=org.example.x", URI=None))
    links.open_uri(SimpleNamespace(
        PACKAGE="intent://open#Intent;scheme=myapp;package=org.example.demo;end", URI=None
    ))
    links.open_uri(SimpleNamespace(PACKAGE="org.example.demo", URI="myapp://x"))
    links.open_uri(SimpleNamespace(PACKAGE="org.example.demo", URI=None))
    with pytest.raises(ValueError):
        links.open_uri(SimpleNamespace(PACKAGE="myapp://x", URI=None))
    assert app_manager == [
        ("linux", "https://play.google.com/store/apps/details?id=org.example.x"),
        ("android", links.VIEW, "myapp://open"),
        ("android", links.VIEW, "myapp://x"),
        ("launch", "org.example.demo"),
    ]
