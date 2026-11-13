"""Finding and installing new versions of managed AppImages, without any network."""

import hashlib
import json
import shutil
import struct
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

from multipak import appimage, appimage_container, appimage_updates
from multipak.appimage_updates import (
    Check,
    LinkMismatch,
    Release,
    Source,
    UpdateError,
    appstream_sources,
    choose,
    parse_link,
    parse_update_information,
    update_information,
)
from multipak.containers import ManagedContainer
from multipak.engine import Engine
from multipak.models import Transaction
from multipak.process import Runner
from multipak.store import Store

# Sources


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://github.com/owner/app", Source("github", "github.com", "owner/app")),
        (
            "https://github.com/owner/app/releases/tag/v1.0",
            Source("github", "github.com", "owner/app"),
        ),
        (
            "https://github.com/owner/app/releases/download/v1.2.0/App-1.2.0-x86_64.AppImage",
            Source("github", "github.com", "owner/app", pattern="App-*-x86_64.AppImage"),
        ),
        (
            "https://gitlab.com/group/sub/app/-/releases/2.0/downloads/app.AppImage",
            Source("gitlab", "gitlab.com", "group/sub/app", pattern="app.AppImage"),
        ),
        ("https://codeberg.org/owner/app.git", Source("gitea", "codeberg.org", "owner/app")),
        (
            "https://example.org/files/app.AppImage.zsync",
            Source("zsync", url="https://example.org/files/app.AppImage.zsync"),
        ),
        (
            "https://example.org/files/latest.AppImage#x",
            Source("file", url="https://example.org/files/latest.AppImage"),
        ),
    ],
)
def test_links_lead_to_their_source(link, expected):
    assert parse_link(link) == expected


@pytest.mark.parametrize(
    "link",
    ["http://github.com/owner/app", "https://example.org/", "ftp://x/app.AppImage", "github"],
)
def test_unsupported_or_insecure_links_are_refused(link):
    with pytest.raises(UpdateError):
        parse_link(link)


def test_embedded_update_information_kinds():
    assert parse_update_information(
        "gh-releases-zsync|neovim|neovim|latest|nvim-linux-x86_64.appimage.zsync"
    ) == Source(
        "github",
        "github.com",
        "neovim/neovim",
        pattern="nvim-linux-x86_64.appimage.zsync",
        zsync=True,
    )
    assert parse_update_information("zsync|https://example.org/a.zsync") == Source(
        "zsync", url="https://example.org/a.zsync"
    )
    for unsupported in ("zsync|http://example.org/a.zsync", "pling-v1-zsync|1|a", "", "bintray"):
        assert parse_update_information(unsupported) is None


def type2(path, *, name="Demo", desktop="demo.desktop", update="", files=None):
    """A real type-2 AppImage. Its ELF section table can carry update information."""
    if not shutil.which("mksquashfs") or not shutil.which("unsquashfs"):
        pytest.skip("squashfs-tools are required for real type-2 tests")
    source = path.parent / (path.name + "-source")
    source.mkdir()
    (source / desktop).write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nExec=AppRun\nX-AppImage-Version=1\n"
    )
    (source / "AppRun").write_text("#!/bin/sh\n")
    (source / "AppRun").chmod(0o755)
    for member, content in (files or {}).items():
        (source / member).parent.mkdir(parents=True, exist_ok=True)
        (source / member).write_text(content)
    payload = path.parent / (path.name + ".squashfs")
    subprocess.run(
        ["mksquashfs", str(source), str(payload), "-noappend", "-quiet"],
        check=True,
        capture_output=True,
    )
    header = bytearray(1024)
    header[:16] = b"\x7fELF\x02\x01\x01\x00AI\x02\x00\x00\x00\x00\x00"
    struct.pack_into("<H", header, 18, 62)
    names = b"\0.shstrtab\0.upd_info\0"
    header[64 : 64 + len(names)] = names
    data = update.encode() + b"\0"
    header[128 : 128 + len(data)] = data
    struct.pack_into("<Q", header, 40, 512)
    struct.pack_into("<HHH", header, 58, 64, 3, 1)
    struct.pack_into("<IIQQQQ", header, 512 + 64, 1, 3, 0, 0, 64, len(names))
    struct.pack_into("<IIQQQQ", header, 512 + 128, 11, 1, 0, 0, 128, 512 - 128)
    path.write_bytes(bytes(header[:704]) + payload.read_bytes())
    return path


def test_update_information_is_read_from_the_elf_section(tmp_path):
    path = type2(tmp_path / "a.AppImage", update="zsync|https://example.org/a.zsync")
    assert update_information(path) == "zsync|https://example.org/a.zsync"
    assert update_information(type2(tmp_path / "b.AppImage")) == ""
    other = tmp_path / "other"
    other.write_bytes(b"not an ELF file")
    assert update_information(other) == ""


def test_appstream_project_pages_are_found(tmp_path):
    metainfo = tmp_path / "usr/share/metainfo"
    metainfo.mkdir(parents=True)
    (metainfo / "app.metainfo.xml").write_text(
        "<component><url type='homepage'>https://example.org</url>"
        '<url type="vcs-browser">https://github.com/owner/app</url>'
        '<url type="bugtracker">https://github.com/owner/app/issues</url></component>'
    )
    (tmp_path / "usr/share/appdata").symlink_to("/etc")
    assert appstream_sources(tmp_path) == [Source("github", "github.com", "owner/app")]


def asset(name, size=None, sha256=""):
    return appimage_updates.Asset(name, f"https://files.example/{name}", size, sha256)


def test_the_file_for_this_computer_is_chosen():
    assets = [
        asset("App-2.0-aarch64.AppImage"),
        asset("App-2.0-x86_64.AppImage.zsync"),
        asset("App-2.0-x86_64.AppImage"),
        asset("App-2.0.tar.gz"),
        asset("Other-2.0.AppImage"),
    ]
    source = Source("github", "github.com", "o/a")
    assert choose(assets, source, machine="x86_64").name == "App-2.0-x86_64.AppImage"
    assert choose(assets, source, machine="aarch64").name == "App-2.0-aarch64.AppImage"
    zsync = Source("github", "github.com", "o/a", pattern="App-*-x86_64.AppImage.zsync", zsync=True)
    assert choose(assets, zsync, machine="x86_64").name == "App-2.0-x86_64.AppImage.zsync"
    named = Source("github", "github.com", "o/a", pattern="Other-*.AppImage")
    assert choose(assets, named, machine="x86_64").name == "Other-2.0.AppImage"
    assert choose([asset("App-arm64.AppImage")], source, machine="x86_64") is None


# Checking


class FakeHttp:
    def __init__(self):
        self.responses, self.sizes, self.files, self.requests = {}, {}, {}, []

    def json(self, url, headers=None):
        self.requests.append(url)
        if url not in self.responses:
            raise UpdateError("The address was not found.")
        return self.responses[url]

    def size(self, url):
        self.requests.append(url)
        return self.sizes.get(url)

    def prefix(self, url, limit):
        self.requests.append(url)
        return self.files[url][:limit]

    def download(self, url, target, *, progress=None, cancelled=None, limit=None):
        self.requests.append(url)
        Path(target).write_bytes(self.files[url])
        return len(self.files[url])


RELEASES = "https://api.github.com/repos/owner/app/releases?per_page=20"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data", tmp_path / "applications")


@pytest.fixture
def engine(store):
    return Engine(store, threading.Event())


def installed(store, tmp_path, content=b"version one", **options):
    tx = Transaction(
        "a" * 32,
        "",
        "",
        "Demo",
        "1",
        "appimage",
        hashlib.sha256(content).hexdigest(),
        method="appimage",
        status="installed",
        appimage_mode="extracted",
    )
    for key, value in options.items():
        setattr(tx, key, value)
    directory = store.appimage_directory(tx.id)
    (directory / "AppDir").mkdir(parents=True)
    (directory / "application.AppImage").write_bytes(content)
    store.save(tx)
    return tx


def github_release(tag, *assets_):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": item.name,
                "browser_download_url": item.url,
                "size": item.size,
                "digest": f"sha256:{item.sha256}" if item.sha256 else None,
            }
            for item in assets_
        ],
    }


def test_an_update_is_found_by_comparing_files_and_remembered(store, engine, tmp_path):
    tx = installed(store, tmp_path, appimage_update_source="https://github.com/owner/app")
    http = FakeHttp()
    new = hashlib.sha256(b"version two").hexdigest()
    http.responses[RELEASES] = [
        {"tag_name": "v3.0-rc", "draft": False, "prerelease": True, "assets": []},
        github_release("v2.0", asset("Demo-2.0-x86_64.AppImage", 11, new)),
    ]
    clock = [1000.0]
    results = appimage_updates.check(engine, http=http, now=lambda: clock[0], machine="x86_64")
    result = results[tx.id]
    assert result.status == "available"
    assert result.release.version == "2.0" and result.release.sha256 == new
    # A second look within six hours uses the remembered result.
    requests = len(http.requests)
    clock[0] += 3600
    again = appimage_updates.check(engine, http=http, now=lambda: clock[0], machine="x86_64")
    assert again[tx.id].status == "available" and len(http.requests) == requests
    # Reloading consults the server again.
    appimage_updates.check(engine, http=http, force=True, now=lambda: clock[0], machine="x86_64")
    assert len(http.requests) > requests


def test_the_installed_file_is_current(store, engine, tmp_path):
    tx = installed(store, tmp_path, appimage_update_source="https://github.com/owner/app")
    http = FakeHttp()
    http.responses[RELEASES] = [github_release("v1", asset("Demo.AppImage", 11, tx.sha256))]
    assert appimage_updates.check(engine, http=http, machine="x86_64")[tx.id].status == "current"


def test_without_a_digest_the_size_is_compared(store, engine, tmp_path):
    tx = installed(store, tmp_path, appimage_update_source="https://codeberg.org/owner/app")
    http = FakeHttp()
    http.responses["https://codeberg.org/api/v1/repos/owner/app/releases?limit=20"] = [
        {
            "tag_name": "2.0",
            "assets": [
                {"name": "Demo.AppImage", "size": 99, "browser_download_url": "https://c/x"}
            ],
        }
    ]
    assert appimage_updates.check(engine, http=http, machine="x86_64")[tx.id].status == (
        "available"
    )


def test_a_failed_check_shows_the_last_result(store, engine, tmp_path):
    tx = installed(store, tmp_path, appimage_update_source="https://github.com/owner/app")
    http = FakeHttp()
    http.responses[RELEASES] = [github_release("v2", asset("Demo.AppImage", 1, "f" * 64))]
    appimage_updates.check(engine, http=http, machine="x86_64")
    del http.responses[RELEASES]
    result = appimage_updates.check(engine, http=http, force=True, machine="x86_64")[tx.id]
    assert result.status == "available" and result.stale and result.error
    # Without an earlier result, the failure itself is reported.
    (store.root / "appimage-updates.json").unlink()
    result = appimage_updates.check(engine, http=http, force=True, machine="x86_64")[tx.id]
    assert result.status == "failed" and not result.stale


def test_embedded_zsync_information_is_compared_by_sha1(store, engine, tmp_path):
    path = type2(
        tmp_path / "embedded.AppImage",
        update="gh-releases-zsync|owner|app|latest|Demo-*-x86_64.AppImage.zsync",
    )
    content = path.read_bytes()
    tx = installed(store, tmp_path, content)
    zsync = "https://files.example/Demo-2-x86_64.AppImage.zsync"
    http = FakeHttp()
    http.responses[RELEASES] = [github_release("2", asset("Demo-2-x86_64.AppImage.zsync"))]
    sha1 = hashlib.sha1(content).hexdigest()
    http.files[zsync] = f"zsync: 0.6.2\nSHA-1: {sha1}\nURL: Demo-2-x86_64.AppImage\n\n".encode()
    result = appimage_updates.check(engine, http=http, machine="x86_64")[tx.id]
    assert result.status == "current"
    assert result.release.url == "https://files.example/Demo-2-x86_64.AppImage"
    http.files[zsync] = b"SHA-1: " + b"0" * 40 + b"\n\n"
    result = appimage_updates.check(engine, http=http, force=True, machine="x86_64")[tx.id]
    assert result.status == "available"


def test_the_appstream_project_is_used_only_when_it_publishes_this_file(store, engine, tmp_path):
    tx = installed(store, tmp_path)
    metainfo = store.appimage_directory(tx.id) / "AppDir/usr/share/metainfo"
    metainfo.mkdir(parents=True)
    (metainfo / "demo.xml").write_text(
        '<component><url type="vcs-browser">https://github.com/owner/app</url></component>'
    )
    http = FakeHttp()
    http.responses[RELEASES] = [github_release("v2.0", asset("Demo-2.0.AppImage", 5, "e" * 64))]
    result = appimage_updates.check(engine, http=http, machine="x86_64")[tx.id]
    assert result.status == "no-source"
    assert store.transactions()[0].appimage_update_source == ""
    http.responses[RELEASES].append(
        github_release("v1.0", asset("Demo-1.0.AppImage", 11, tx.sha256))
    )
    result = appimage_updates.check(engine, http=http, force=True, machine="x86_64")[tx.id]
    assert result.status == "available" and result.release.name == "Demo-2.0.AppImage"
    saved = store.transactions()[0].appimage_update_source
    assert saved == "https://github.com/owner/app/releases/download/v1.0/Demo-1.0.AppImage"
    assert parse_link(saved).pattern == "Demo-*.AppImage"


def test_an_entered_link_is_saved_once_it_proves_right(store, engine, tmp_path):
    tx = installed(store, tmp_path)
    http = FakeHttp()
    http.responses[RELEASES] = [github_release("v2", asset("Demo.AppImage", 5, "e" * 64))]
    result = appimage_updates.check_link(
        engine, tx.id, "https://github.com/owner/app", http=http, machine="x86_64"
    )
    # Not yet: the download must show it is the same application.
    assert result.status == "available" and result.link == "https://github.com/owner/app"
    assert store.transactions()[0].appimage_update_source == ""
    http.responses[RELEASES] = [github_release("v1", asset("Demo.AppImage", 11, tx.sha256))]
    result = appimage_updates.check_link(
        engine, tx.id, "https://github.com/owner/app", http=http, machine="x86_64"
    )
    assert result.status == "current"
    assert store.transactions()[0].appimage_update_source == "https://github.com/owner/app"
    with pytest.raises(UpdateError):
        appimage_updates.check_link(engine, tx.id, "https://example.org", http=http)


def test_cache_entries_follow_the_installed_copy(store, tmp_path):
    tx = installed(store, tmp_path)
    cache = appimage_updates.UpdateCache(store)
    cache.put(tx, Check("current", checked=1.0))
    cache.save()
    assert appimage_updates.UpdateCache(store).get(tx).status == "current"
    tx.sha256 = "0" * 64
    assert appimage_updates.UpdateCache(store).get(tx) is None


# Running copies


def test_running_copies_are_found_and_asked_to_close(store, tmp_path):
    tx = installed(store, tmp_path)
    appdir = store.appimage_directory(tx.id) / "AppDir"
    listing = [f"  41 {appdir}/usr/bin/demo --flag\n 52 /usr/bin/other\n"]
    assert appimage_updates.running(store, tx, lambda: listing[0]) == [41]
    signals = []

    def signal(pids):
        signals.append(pids)
        listing[0] = ""

    assert appimage_updates.close(
        store, tx, lister=lambda: listing[0], signal=signal, sleep=lambda _: None
    )
    assert signals == [[41]]
    listing[0] = f"41 {appdir}/AppRun\n"
    assert not appimage_updates.close(
        store, tx, lister=lambda: listing[0], signal=lambda _: None, wait=0.01, sleep=lambda _: None
    )


# Downloading and installing


def runner_engine(store):
    cancelled = threading.Event()
    engine = Engine(store, cancelled)
    engine.runner = Runner(cancelled, lambda _: None)
    return engine


def test_a_download_of_another_application_is_refused(store, tmp_path):
    old = type2(tmp_path / "old.AppImage", desktop="demo.desktop")
    other = type2(tmp_path / "other.AppImage", name="Other", desktop="other.desktop")
    tx = installed(store, tmp_path, old.read_bytes())
    engine = runner_engine(store)
    http = FakeHttp()
    http.files["https://f/other.AppImage"] = other.read_bytes()
    release = Release("https://f/other.AppImage")
    with pytest.raises(LinkMismatch):
        appimage_updates.download(engine, tx.id, release, link="https://f/x", http=http)
    with pytest.raises(UpdateError):
        appimage_updates.download(engine, tx.id, release, http=http)
    assert not (store.appimage_directory(tx.id) / "update.AppImage").exists()
    damaged = Release("https://f/other.AppImage", sha256="0" * 64)
    with pytest.raises(UpdateError, match="damaged"):
        appimage_updates.download(engine, tx.id, damaged, http=http)


def test_new_dependencies_are_reported_before_installing(store, tmp_path, monkeypatch):
    old = type2(tmp_path / "old.AppImage")
    new = type2(tmp_path / "new.AppImage", name="Demo Two")
    tx = installed(
        store,
        tmp_path,
        old.read_bytes(),
        appimage_container=True,
        appimage_mode="container",
        appimage_packages=["python3"],
    )
    engine = runner_engine(store)
    http = FakeHttp()
    http.files["https://f/new.AppImage"] = new.read_bytes()
    from multipak.appimage_dependencies import Dependency, DependencyReport

    report = DependencyReport(
        (),
        False,
        (
            Dependency("interpreter", "python3", "python3"),
            Dependency("library", "libnew.so.1", "libnew.so.1()(64bit)"),
        ),
    )
    monkeypatch.setattr("multipak.appimage_dependencies.analyze", lambda *a, **k: report)
    prepared = appimage_updates.download(
        engine, tx.id, Release("https://f/new.AppImage"), http=http
    )
    assert prepared.packages == ("libnew.so.1()(64bit)",)
    assert "libnew.so.1" in prepared.dependencies and "Python 3" not in prepared.dependencies
    assert prepared.version == "1" and prepared.name == "Demo Two"
    appimage_updates.discard(store, tx.id)
    assert not prepared.path.exists()


def test_installing_replaces_the_copy_and_keeps_the_shortcut(store, tmp_path, monkeypatch):
    old = type2(tmp_path / "old.AppImage")
    new = type2(tmp_path / "new.AppImage", name="Demo Two")
    engine = runner_engine(store)
    tx = installed(store, tmp_path, old.read_bytes())
    appimage._desktop(store, tx, appimage.inspect_appimage(old, engine.runner))
    store.save(tx)
    launcher = store.launcher(tx, f"multipak-{tx.id}-appimage.desktop")
    http = FakeHttp()
    http.files["https://f/new.AppImage"] = new.read_bytes()
    monkeypatch.setattr(
        "multipak.appimage_dependencies.analyze",
        lambda *a, **k: appimage_updates_report(),
    )
    prepared = appimage_updates.download(
        engine,
        tx.id,
        Release("https://f/new.AppImage"),
        link="https://github.com/owner/app",
        http=http,
    )
    result = appimage_updates.install(engine, prepared)
    directory = store.appimage_directory(tx.id)
    assert result.package_name == "Demo Two" and result.status == "installed"
    assert result.sha256 == hashlib.sha256(new.read_bytes()).hexdigest()
    assert (directory / "application.AppImage").read_bytes() == new.read_bytes()
    assert (directory / "AppDir/AppRun").is_file()
    assert not (directory / "previous.AppImage").exists()
    assert "Name=Demo Two" in launcher.read_text()
    saved = store.transactions()[0]
    assert saved.appimage_update_source == "https://github.com/owner/app"
    assert appimage_updates.UpdateCache(store).get(saved).status == "current"


def appimage_updates_report():
    from multipak.appimage_dependencies import DependencyReport

    return DependencyReport()


def test_a_failed_update_restores_the_previous_copy(store, tmp_path, monkeypatch):
    old = type2(tmp_path / "old.AppImage")
    new = type2(tmp_path / "new.AppImage", name="Demo Two")
    engine = runner_engine(store)
    tx = installed(store, tmp_path, old.read_bytes())
    directory = store.appimage_directory(tx.id)
    staged = directory / "update.AppImage"
    shutil.copy(new, staged)

    def broken(engine, package):
        raise RuntimeError("extraction failed")

    monkeypatch.setattr(appimage, "_complete", broken)
    with pytest.raises(RuntimeError, match="extraction failed"):
        appimage.update(engine, tx.id, staged)
    assert (directory / "application.AppImage").read_bytes() == old.read_bytes()
    assert (directory / "AppDir").is_dir()
    restored = store.transactions()[0]
    assert (restored.sha256, restored.package_name) == (tx.sha256, "Demo")
    # Nothing is left for the worker to clean up after a failure or cancellation.
    assert engine.tx is None


# Python modules from PyPI


def wheel(path, *members):
    with zipfile.ZipFile(path, "w") as archive:
        for member in members:
            archive.writestr(member, "")
    return path


def test_wheel_modules_are_read_from_its_file_list(tmp_path):
    path = wheel(
        tmp_path / "x.whl",
        "ttkthemes/__init__.py",
        "ttkthemes-3.2.2.dist-info/METADATA",
        "single.py",
        "_speedups.cpython-313-x86_64-linux-gnu.so",
    )
    assert appimage_container.wheel_modules(path) == {"ttkthemes", "single", "_speedups"}


class PipRunner:
    """Answers pip inside the container with a prepared wheel."""

    def __init__(self, members):
        self.members, self.calls = members, []

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["podman", "container", "inspect"]:
            return "c" * 64 + "\n"
        if "download" in argv:
            destination = Path(argv[argv.index("--dest") + 1])
            wheel(destination / "package-1.0-py3-none-any.whl", *self.members)
        return ""


def test_a_module_fedora_lacks_is_installed_from_pypi(engine):
    runner = PipRunner(["ttkthemes/__init__.py"])
    engine.runner = runner
    container = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    assert appimage_container.install_from_pypi(engine, container, "ttkthemes")
    install = next(argv for argv in runner.calls if "install" in argv and "pip" in argv)
    # Only the checked wheel is named, never the project, which PyPI could resolve anew.
    assert install[-2:-1] == ["--"]
    assert "sudo" in install and "--only-binary=:all:" in install
    assert install[-1].endswith(".whl")
    download = next(argv for argv in runner.calls if "download" in argv)
    assert "--no-deps" in download and "--only-binary=:all:" in download
    assert not any((engine.store.root / "pypi").iterdir())


def test_a_pypi_project_without_the_module_is_not_installed(engine):
    runner = PipRunner(["something_else/__init__.py"])
    engine.runner = runner
    container = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    assert not appimage_container.install_from_pypi(engine, container, "ttkthemes")
    assert not any("install" in argv and "pip" in argv for argv in runner.calls)


def test_install_packages_falls_back_to_pypi_for_python_modules(engine, monkeypatch):
    monkeypatch.setattr(
        appimage_container,
        "_install_fedora_packages",
        lambda engine, container, specs: ["python3dist(ttkthemes)", "libgone.so.1()(64bit)"],
    )
    monkeypatch.setattr(
        appimage_container, "install_from_pypi", lambda engine, container, name: True
    )
    installed_from_pypi = []
    missing = appimage_container.install_packages(
        engine, None, ["python3dist(ttkthemes)", "libgone.so.1()(64bit)"], installed_from_pypi
    )
    assert missing == ["libgone.so.1()(64bit)"]
    assert installed_from_pypi == ["ttkthemes"]


def test_results_survive_being_saved(store, tmp_path):
    check = Check("available", "https://github.com/o/a", Release("https://x", "x", "2", 5))
    assert Check.from_dict(json.loads(json.dumps(check.to_dict()))) == check
