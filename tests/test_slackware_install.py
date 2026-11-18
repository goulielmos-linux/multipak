import gzip
import json
import shlex
from pathlib import Path

import pytest
from test_slackware import make_slackware

from multipak import catalog, guest
from multipak.environments import read_os_release
from multipak.file_associations import FORMATS, FileAssociations
from multipak.metadata import inspect_package
from multipak.models import (
    ENVIRONMENTS,
    FULL_LABEL,
    SLACKWARE_ENVIRONMENTS,
    ExistingEnvironment,
)
from multipak.process import CommandError

LIGHT = next(item for item in ENVIRONMENTS if item.id == "slackware-15.0")
FULL = next(item for item in ENVIRONMENTS if item.id == "slackware-15.0-full")
RELEASE = "ID=slackware\nVERSION_ID=15.0\n"


@pytest.fixture
def slackware_engine(fake_engine):
    fake_engine.runner.release = RELEASE
    return fake_engine


def install(engine, path, environment):
    return engine.install(inspect_package(path, engine.runner), environment)


def guest_commands(engine):
    return [
        call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


def test_light_environment_installs_with_installpkg_and_adds_libraries(slackware_engine, tmp_path):
    engine = slackware_engine
    path = make_slackware(tmp_path / "demo-app-1.2-x86_64-1_SBo.txz")
    tx = install(engine, path, LIGHT)
    assert tx.status == "installed"
    assert tx.container == "MultiPak-Slackware-15.0"
    assert tx.environment_id == "slackware-15.0"
    commands = guest_commands(engine)
    # installpkg takes the package name from the file name, so the copy keeps it.
    staged = f"/tmp/multipak-{tx.id}/demo-app-1.2-x86_64-1_SBo.txz"
    update = commands.index(
        ["sudo", "-n", "/usr/sbin/slackpkg", "-batch=on", "-default_answer=y", "update"]
    )
    installation = commands.index(["sudo", "-n", "/sbin/installpkg", staged])
    dependencies = next(
        index for index, item in enumerate(commands) if "--slackware-dependencies" in item
    )
    assert update < installation < dependencies
    assert commands[dependencies][-3:] == ["--slackware-dependencies", "light", "demo-app"]
    assert FULL_LABEL not in engine.runner.container["Config"]["Labels"]


def test_new_slackware_container_trusts_the_bundled_signing_key(slackware_engine, tmp_path):
    engine = slackware_engine
    install(engine, make_slackware(tmp_path / "demo-app-1-x86_64-1.txz"), LIGHT)
    create = next(call for call in engine.runner.calls if call[:2] == ["distrobox", "create"])
    hook = create[create.index("--pre-init-hooks") + 1]
    key = Path(shlex.split(hook)[3].rstrip(";"))
    assert key.parent == engine.environment_home
    assert b"BEGIN PGP PUBLIC KEY BLOCK" in key.read_bytes()
    assert "--homedir /root/.gnupg --import" in hook
    assert create[create.index("--additional-packages") + 1] == "python3"


def test_full_environment_uses_the_same_image_with_its_package_sets(slackware_engine, tmp_path):
    engine = slackware_engine
    tx = install(engine, make_slackware(tmp_path / "demo-app-1-x86_64-1.txz"), FULL)
    assert FULL.image == LIGHT.image
    assert tx.container == "MultiPak-Slackware-15.0-full"
    assert tx.environment_id == "slackware-15.0-full"
    assert engine.runner.container["Config"]["Labels"][FULL_LABEL] == "1"
    dependencies = next(
        item for item in guest_commands(engine) if "--slackware-dependencies" in item
    )
    assert dependencies[-3:] == ["--slackware-dependencies", "full", "demo-app"]


def test_long_extension_is_staged_with_the_short_one(slackware_engine, tmp_path):
    engine = slackware_engine
    tx = install(
        engine, make_slackware(tmp_path / "demo-app-1-x86_64-1.tar.gz", compression="gz"), LIGHT
    )
    assert tx.package_file == "demo-app-1-x86_64-1.tgz"
    assert ["sudo", "-n", "/sbin/installpkg", f"/tmp/multipak-{tx.id}/demo-app-1-x86_64-1.tgz"] in (
        guest_commands(engine)
    )


def test_required_packages_are_passed_and_missing_libraries_warn(slackware_engine, tmp_path):
    engine = slackware_engine
    engine.runner.slackware_result = {"added": ["gtk+3"], "missing": ["libodd.so.1"]}
    path = make_slackware(
        tmp_path / "demo-app-1-x86_64-1.txz",
        members=(
            ("install/slack-desc", b"demo-app: Demo\n"),
            ("install/slack-required", b"zlib\n"),
        ),
    )
    tx = install(engine, path, LIGHT)
    dependencies = next(
        item for item in guest_commands(engine) if "--slackware-dependencies" in item
    )
    assert dependencies[-2:] == ["demo-app", "zlib"]
    assert any("libodd.so.1" in warning for warning in tx.warnings)


def test_a_tampered_staged_name_is_refused(slackware_engine, tmp_path):
    engine = slackware_engine
    tx = install(engine, make_slackware(tmp_path / "demo-app-1-x86_64-1.txz"), LIGHT)
    tx.package_file = "../../etc/passwd"
    engine.tx = tx
    with pytest.raises(ValueError, match="Slackware package"):
        _ = engine.guest_package


def existing(full, names=("MultiPak-Slackware-15.0",)):
    return ExistingEnvironment(
        names[0],
        "id",
        "slackware",
        "15.0",
        "image",
        ("docker.io/aclemons/slackware:15.0",),
        full_variant=full,
    )


def test_light_and_full_containers_are_told_apart_by_their_label():
    light, full = existing(False), existing(True, ("MultiPak-Slackware-15.0-full",))
    assert light.matches(LIGHT) and not light.matches(FULL)
    assert full.matches(FULL) and not full.matches(LIGHT)
    assert light.multipak_managed and full.multipak_managed
    assert full.default_names == ("MultiPak-Slackware-15.0-full",)


def test_catalog_offers_both_slackware_environments(tmp_path):
    class Runner:
        def run(self, *_args, **_kwargs):
            raise CommandError(["distrobox"], 1, "offline")

    profiles = catalog.load_catalog(Runner())
    assert [item for item in profiles if item.kind == "slackware"] == list(SLACKWARE_ENVIRONMENTS)
    assert catalog.package_environment("slackware-15.0", list(profiles)) == LIGHT
    assert catalog.release_tag("slackware", "15.0")
    assert not catalog.release_tag("slackware", "15.0-full")


def test_cached_catalog_keeps_slackware_and_adds_it_to_older_caches(tmp_path):
    cache = tmp_path / "catalog.json"
    others = [item for item in ENVIRONMENTS if item.kind != "slackware"]
    cache.write_text(json.dumps([item.__dict__ for item in others]))
    assert [item for item in catalog.read_cached(cache) if item.kind == "slackware"] == list(
        SLACKWARE_ENVIRONMENTS
    )
    cache.write_text(json.dumps([item.__dict__ for item in ENVIRONMENTS]))
    assert catalog.read_cached(cache) == ENVIRONMENTS
    tampered = {**LIGHT.__dict__, "image": "docker.io/someone/slackware:15.0"}
    cache.write_text(json.dumps([tampered]))
    assert catalog.read_cached(cache) == ()


def test_os_release_falls_back_to_etc_for_slackware():
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(argv)
            if argv[1] == "exec":
                raise CommandError(argv, 125, "container is not running")
            if argv[2].endswith(":/usr/lib/os-release"):
                raise CommandError(argv, 125, "no such file")
            Path(argv[3]).write_text(RELEASE)
            return ""

    runner = Runner()
    assert read_os_release(runner, "box") == {"ID": "slackware", "VERSION_ID": "15.0"}
    assert runner.calls[-1][2] == "box:/etc/os-release"


def test_association_registers_only_package_shaped_names(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    settings = FileAssociations(
        config_home=tmp_path / "config",
        data_home=tmp_path / "data",
        refresher=lambda: None,
        reader=lambda _mime: "other.desktop",
    )
    settings.apply({key: True for key in FORMATS})
    text = settings.slackware_xml_path.read_text()
    assert 'glob pattern="*-*-x86_64-*.txz"' in text
    assert '"*.txz"' not in text and '"*.tar.xz"' not in text
    settings.apply({key: key != "slackware" for key in FORMATS})
    assert not settings.slackware_xml_path.exists()


@pytest.fixture
def slackware_root(tmp_path, monkeypatch):
    database = tmp_path / "packages"
    lists = tmp_path / "slackpkg"
    database.mkdir()
    lists.mkdir()
    monkeypatch.setattr(guest, "SLACKWARE_DATABASE", database)
    monkeypatch.setattr(guest, "SLACKPKG_LISTS", lists)
    return database, lists


def test_guest_reads_the_installed_file_list(slackware_root):
    database, _ = slackware_root
    (database / "demo-app-1.2-x86_64-1_SBo").write_text(
        "PACKAGE NAME: demo-app-1.2-x86_64-1_SBo\nFILE LIST:\n./\ninstall/\n"
        "install/slack-desc\nusr/\nusr/bin/demo\nusr/share/applications/demo.desktop\n"
    )
    (database / "demo-app-extra-1-x86_64-1").write_text("FILE LIST:\n")
    assert guest.package_present("slackware", "demo-app")
    assert not guest.package_present("slackware", "demo")
    assert guest.slackware_files("demo-app") == [
        "/usr/",
        "/usr/bin/demo",
        "/usr/share/applications/demo.desktop",
    ]
    assert guest.slackware_installed() == {"demo-app", "demo-app-extra"}


def test_guest_finds_the_package_of_each_library(slackware_root):
    _, lists = slackware_root
    with gzip.open(lists / "slackware64-filelist.gz", "wt") as stream:
        stream.write(
            "./l/gtk+3-3.24.31-x86_64-1.txz ./ usr/lib64/libgtk-3.so.0.2404.27 usr/bin/x\n"
            "./l/openjpeg-2.4.0-x86_64-1.txz ./ usr/lib64/libopenjp2.so.2.4.0\n"
            "./l/zlib-1.2.11-x86_64-6.txz ./ lib64/libz.so.1.2.11\n"
        )
    with gzip.open(lists / "patches-filelist.gz", "wt") as stream:
        stream.write("./patches/packages/zlib-1.2.13-x86_64-1_slack15.0.txz lib64/libz.so.1.2.13\n")
    assert guest.slackware_providers(
        {"libgtk-3.so.0", "libopenjp2.so.7", "libz.so.1", "libno.so.3"}
    ) == {
        "libgtk-3.so.0": "gtk+3",
        "libopenjp2.so.7": "openjpeg",
        "libz.so.1": "zlib",
    }


def test_guest_full_set_skips_kernels_and_other_series(slackware_root):
    _, lists = slackware_root
    (lists / "pkglist").write_text(
        "slackware64 gtk+3 3.24.31 x86_64 1 gtk+3-3.24.31-x86_64-1 ./slackware64/l txz\n"
        "slackware64 kernel-generic 5.15 x86_64 2 kernel-generic-5.15-x86_64-2 "
        "./slackware64/a txz\n"
        "slackware64 grub 2.06 x86_64 4 grub-2.06-x86_64-4 ./slackware64/a txz\n"
        "slackware64 plasma 5 x86_64 1 plasma-5-x86_64-1 ./slackware64/kde txz\n"
        "slackware64 gcc 11 x86_64 1 gcc-11-x86_64-1 ./slackware64/d txz\n"
        "extra flashplayer 1 x86_64 1 flashplayer-1-x86_64-1 ./extra/flash txz\n"
    )
    assert guest.slackware_full_set() == ["gtk+3"]
    assert guest.slackware_available() == {
        "gtk+3",
        "kernel-generic",
        "grub",
        "plasma",
        "gcc",
        "flashplayer",
    }


def test_guest_installs_providers_until_nothing_is_missing(slackware_root, monkeypatch):
    database, lists = slackware_root
    (lists / "pkglist").write_text(
        "slackware64 xkeyboard-config 2 noarch 1 xkeyboard-config-2-noarch-1 ./slackware64/x txz\n"
    )
    missing = [{"libgtk-3.so.0"}, {"libcairo.so.2"}, set()]
    installs = []
    monkeypatch.setattr(guest, "missing_libraries", lambda _name: missing.pop(0))
    providers = {"libgtk-3.so.0": "gtk+3", "libcairo.so.2": "cairo"}
    monkeypatch.setattr(
        guest,
        "slackware_providers",
        lambda libraries: {item: providers[item] for item in libraries},
    )

    def fake_install(packages):
        installs.append(list(packages))
        for package in packages:
            (database / f"{package}-1-x86_64-1").write_text("FILE LIST:\n")

    monkeypatch.setattr(guest, "slackpkg_install", fake_install)
    result = guest.slackware_dependencies("light", "demo-app", [])
    assert installs == [["xkeyboard-config", "gtk+3"], ["cairo"]]
    assert result == {"added": ["xkeyboard-config", "gtk+3", "cairo"], "missing": []}
