import gzip
import hashlib
import io
import json
import lzma
import shlex
import stat
import tarfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_slitaz import _cpio, elf, make_tazpkg

from multipak import catalog, guest, slitaz_image, updates
from multipak.environment_choices import container_base
from multipak.file_associations import FORMATS, FileAssociations
from multipak.metadata import inspect_package
from multipak.models import (
    ENVIRONMENTS,
    SLITAZ_ENVIRONMENT,
    SLITAZ_ENVIRONMENTS,
    SLITAZ_I486_ENVIRONMENT,
    ExistingEnvironment,
    slitaz_environment,
)
from multipak.process import CommandError

RELEASE = slitaz_image.os_release("x86_64").decode()


@pytest.fixture
def slitaz_engine(fake_engine, monkeypatch):
    fake_engine.runner.release = RELEASE
    fake_engine.built = []
    monkeypatch.setattr("multipak.metadata.platform.machine", lambda: "x86_64")
    monkeypatch.setattr(
        "multipak.engine.slitaz_image.ensure_image",
        lambda runner, workspace, environment, **_: fake_engine.built.append(
            (workspace, environment)
        ),
    )
    return fake_engine


def guest_commands(engine):
    return [
        call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


def test_installs_with_tazpkg_after_refreshing_the_lists(slitaz_engine, tmp_path):
    engine = slitaz_engine
    path = make_tazpkg(tmp_path / "demo-editor-2.4-2-x86_64.tazpkg", program=elf(2, 62))
    tx = engine.install(inspect_package(path, engine.runner), SLITAZ_ENVIRONMENT)
    assert tx.status == "installed"
    assert tx.container == "MultiPak-SliTaz-cooking"
    assert tx.environment_id == "slitaz-cooking"
    assert engine.built == [(engine.store.root.parent / "slitaz-image", SLITAZ_ENVIRONMENT)]
    assert not any(call[:2] == ["podman", "pull"] for call in engine.runner.calls)
    commands = guest_commands(engine)
    recharge = commands.index(
        ["sudo", "-n", "sh", "/usr/libexec/multipak-slitaz-setup.sh", "--recharge"]
    )
    installation = commands.index(
        ["sudo", "-n", "tazpkg", "install", f"/tmp/multipak-{tx.id}/package.slitaz"]
    )
    translations = commands.index(["sudo", "-n", "tazpkg", "get-install", "demo-editor-lang"])
    assert recharge < installation < translations


def test_32_bit_packages_use_their_own_container(slitaz_engine, tmp_path):
    engine = slitaz_engine
    engine.runner.release = slitaz_image.os_release("i486").decode()
    path = make_tazpkg(tmp_path / "demo-editor-2.4-2.tazpkg", program=elf(1, 3))
    package = inspect_package(path, engine.runner)
    assert package.architecture == "i486"
    assert slitaz_environment(package.architecture) == SLITAZ_I486_ENVIRONMENT
    tx = engine.install(package, SLITAZ_I486_ENVIRONMENT)
    assert tx.container == "MultiPak-SliTaz-cooking-i486"
    create = next(call for call in engine.runner.calls if call[:2] == ["distrobox", "create"])
    assert create[create.index("--image") + 1] == "localhost/multipak-slitaz:cooking-i486"


def test_new_container_runs_the_bundled_setup_script(slitaz_engine, tmp_path):
    engine = slitaz_engine
    path = make_tazpkg(tmp_path / "demo-editor.tazpkg", program=elf(2, 62))
    engine.install(inspect_package(path, engine.runner), SLITAZ_ENVIRONMENT)
    create = next(call for call in engine.runner.calls if call[:2] == ["distrobox", "create"])
    hook = shlex.split(create[create.index("--pre-init-hooks") + 1])
    assert hook[0] == "sh"
    script = Path(hook[1])
    assert script.parent == engine.environment_home
    text = script.read_text()
    assert "touch /.containersetupdone" in text
    assert slitaz_image.BOOTSTRAP_DIRECTORY in text
    assert "python3 could not be installed" in text
    assert "tazpkg up --auto" in text
    assert "packages.md5" in text
    assert "gtk-application-prefer-dark-theme" in text


def test_slitaz_environments_are_told_apart_by_release():
    box = ExistingEnvironment("MultiPak-SliTaz-cooking", "id", "slitaz", "cooking", "image")
    assert box.matches(SLITAZ_ENVIRONMENT)
    assert not box.matches(SLITAZ_I486_ENVIRONMENT)
    assert box.multipak_managed
    assert container_base(SLITAZ_I486_ENVIRONMENT) == "MultiPak-SliTaz-cooking-i486"


def test_catalog_offers_the_slitaz_environments(tmp_path):
    class Runner:
        def run(self, *_args, **_kwargs):
            raise CommandError(["distrobox"], 1, "offline")

    profiles = catalog.load_catalog(Runner())
    assert tuple(item for item in profiles if item.kind == "slitaz") == SLITAZ_ENVIRONMENTS
    for item in SLITAZ_ENVIRONMENTS:
        assert catalog.package_environment(item.id, list(profiles)) == item
    cache = tmp_path / "catalog.json"
    older = [item for item in ENVIRONMENTS if item.kind != "slitaz"]
    cache.write_text(json.dumps([item.__dict__ for item in older]))
    cached = catalog.read_cached(cache)
    assert tuple(item for item in cached if item.kind == "slitaz") == SLITAZ_ENVIRONMENTS
    tampered = {**SLITAZ_ENVIRONMENT.__dict__, "image": "docker.io/someone/slitaz:cooking"}
    cache.write_text(json.dumps([tampered]))
    assert catalog.read_cached(cache) == ()


def test_association_registers_the_slitaz_type(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    settings = FileAssociations(
        config_home=tmp_path / "config",
        data_home=tmp_path / "data",
        refresher=lambda: None,
        reader=lambda _mime: "other.desktop",
    )
    settings.apply({key: True for key in FORMATS})
    text = settings.slitaz_xml_path.read_text()
    assert 'type="application/x-tazpkg"' in text and 'pattern="*.tazpkg"' in text
    settings.apply({key: key != "slitaz" for key in FORMATS})
    assert not settings.slitaz_xml_path.exists()


@pytest.fixture
def slitaz_root(tmp_path, monkeypatch):
    database = tmp_path / "installed"
    database.mkdir()
    info = tmp_path / "installed.info"
    info.write_text("")
    monkeypatch.setattr(guest, "SLITAZ_DATABASE", database)
    monkeypatch.setattr(guest, "SLITAZ_INSTALLED", info)

    def add(name, files=(), needs=()):
        entry = database / name
        entry.mkdir()
        (entry / "receipt").write_text(f'PACKAGE="{name}"\n')
        (entry / "files.list").write_text("".join(f"{item}\n" for item in files))
        fields = [name, "1.0", "misc", "Demo", "https://example.org", "", "1K 2K", " ".join(needs)]
        with info.open("a") as stream:
            stream.write("\t".join(fields) + "\t\n")

    return add


def test_guest_reads_the_installed_file_list(slitaz_root):
    slitaz_root("demo-editor", ("/usr/bin/demo", "/usr/share/applications/demo.desktop"))
    assert guest.package_present("slitaz", "demo-editor")
    assert not guest.package_present("slitaz", "demo")
    assert not guest.package_present("slitaz", "../installed")
    assert guest.slitaz_files("demo-editor") == [
        "/usr/bin/demo",
        "/usr/share/applications/demo.desktop",
    ]


def test_guest_refuses_to_remove_a_package_others_need(slitaz_root, monkeypatch):
    slitaz_root("libdemo")
    slitaz_root("demo-editor", needs=("glibc", "libdemo"))
    monkeypatch.setattr(guest.subprocess, "run", lambda *_a, **_k: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="demo-editor"):
        guest.remove_package("slitaz", "libdemo")


def test_guest_removes_with_tazpkg_without_a_terminal(slitaz_root, monkeypatch):
    slitaz_root("demo-editor")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        guest.SLITAZ_DATABASE.joinpath("demo-editor", "receipt").unlink()

    monkeypatch.setattr(guest.subprocess, "run", run)
    assert guest.remove_package("slitaz", "demo-editor") == {"present": False}
    assert calls[0][0] == ["tazpkg", "remove", "demo-editor"]
    assert calls[0][1]["stdin"] is guest.subprocess.DEVNULL


def test_container_update_uses_the_setup_script_for_slitaz(store, monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return ""

    monkeypatch.setattr(updates, "read_os_release", lambda *_: {"ID": "slitaz"})
    engine = SimpleNamespace(store=store, runner=SimpleNamespace(run=run), progress=lambda *_: 0)
    updates.update_container(engine, "MultiPak-SliTaz-cooking")
    script = "/usr/libexec/multipak-slitaz-setup.sh"
    copy = next(call for call in calls if call[:2] == ["podman", "cp"])
    assert copy[2].endswith("slitaz-setup.sh") and copy[3] == f"MultiPak-SliTaz-cooking:{script}"
    assert calls[-1][-5:] == ["sudo", "-n", "sh", script, "--upgrade"]
    assert ["distrobox", "upgrade", "MultiPak-SliTaz-cooking"] not in calls


# Building the image from the Base ISO, with synthetic ISO and CPIO data.

SECTOR = slitaz_image.SECTOR


def _record(name: bytes, extent: int, size: int, directory: bool) -> bytes:
    length = 33 + len(name) + (len(name) + 1) % 2
    record = bytearray(length)
    record[0] = length
    record[2:6] = extent.to_bytes(4, "little")
    record[10:14] = size.to_bytes(4, "little")
    record[25] = 2 if directory else 0
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    return bytes(record)


def make_iso(rootfs: bytes) -> bytes:
    """An ISO 9660 image with /boot/rootfs.gz, laid out as mkisofs does."""
    root, boot, data = 18, 19, 20
    listing = lambda own, parent, *entries: (  # noqa: E731
        _record(b"\0", own, SECTOR, True) + _record(b"\1", parent, SECTOR, True) + b"".join(entries)
    ).ljust(SECTOR, b"\0")
    image = bytearray(SECTOR * 16)
    volume = bytearray(SECTOR)
    volume[:6] = b"\x01CD001"
    volume[156:190] = _record(b"\0", root, SECTOR, True)
    image += volume + bytes(SECTOR)
    image += listing(root, root, _record(b"BOOT", boot, SECTOR, True))
    image += listing(boot, root, _record(b"ROOTFS.GZ;1", data, len(rootfs), False))
    image += rootfs.ljust(-(-len(rootfs) // SECTOR) * SECTOR, b"\0")
    return bytes(image)


def _linked(name, data, inode, links):
    """A CPIO member sharing an inode, as cpio writes hard links."""
    return name, data, stat.S_IFREG | 0o755, inode, links


def system_cpio(*, extra=()):
    archive = bytearray()
    members = [
        ("bin", b"", stat.S_IFDIR | 0o755, 1, 1),
        ("bin/busybox", b"BUSYBOX", stat.S_IFREG | 0o4755, 2, 1),
        ("bin/sh", b"busybox", stat.S_IFLNK | 0o777, 3, 1),
        ("dev/console", b"", stat.S_IFCHR | 0o600, 4, 1),
        ("boot", b"", stat.S_IFDIR | 0o755, 5, 1),
        ("boot/vmlinuz", b"KERNEL", stat.S_IFREG | 0o644, 6, 1),
        ("init", b"#!/bin/sh\n", stat.S_IFREG | 0o755, 7, 1),
        ("etc", b"", stat.S_IFDIR | 0o755, 8, 1),
        ("etc/os-release", b"ID=old\n", stat.S_IFREG | 0o644, 9, 1),
        ("var/lib/tazpkg/installed/busybox", b"", stat.S_IFDIR | 0o755, 10, 1),
        ("var/lib/tazpkg/installed/glibc-base", b"", stat.S_IFDIR | 0o755, 11, 1),
        # New ASCII CPIO keeps hard-linked data with the last name.
        _linked("sbin/e2label", b"", 12, 2),
        _linked("sbin/tune2fs", b"TUNE", 12, 2),
        *extra,
        ("TRAILER!!!", b"", 0, 0, 1),
    ]
    for name, data, mode, inode, links in members:
        name = name.encode() + b"\0"
        fields = (inode, mode, 0, 0, links, 0, len(data), 0, 1, 0, 0, len(name), 0)
        archive.extend(b"070701" + "".join(f"{field:08x}" for field in fields).encode() + name)
        archive.extend(b"\0" * (-len(archive) % 4))
        archive.extend(data + b"\0" * (-len(data) % 4))
    return bytes(archive)


def tar_members(path):
    with tarfile.open(path) as tar:
        return {
            member.name: (member, tar.extractfile(member).read() if member.isfile() else None)
            for member in tar.getmembers()
        }


def test_finds_a_file_in_an_iso_image():
    rootfs = b"SYSTEM" * 1000
    stream = io.BytesIO(make_iso(rootfs))
    offset, size = slitaz_image.iso_member(stream, "boot/rootfs.gz")
    stream.seek(offset)
    assert stream.read(size) == rootfs
    with pytest.raises(RuntimeError, match="does not contain"):
        slitaz_image.iso_member(stream, "boot/rootfs1.gz")
    with pytest.raises(RuntimeError, match="not valid"):
        slitaz_image.iso_member(io.BytesIO(bytes(SECTOR * 17)), "boot/rootfs.gz")


def test_decompresses_concatenated_lzma_streams(tmp_path):
    first, second = system_cpio(), _cpio([("usr", b"", stat.S_IFDIR | 0o755)])
    data = (
        lzma.compress(first, format=lzma.FORMAT_ALONE)
        + b"\0" * 3
        + lzma.compress(second, format=lzma.FORMAT_ALONE)
    )
    target = tmp_path / "rootfs.cpio"
    slitaz_image.decompress(io.BytesIO(b"xx" + data), 2, len(data), target)
    assert target.read_bytes() == first + second
    gzipped = gzip.compress(first)
    slitaz_image.decompress(io.BytesIO(gzipped), 0, len(gzipped), target)
    assert target.read_bytes() == first


def test_limits_the_decompressed_system(tmp_path, monkeypatch):
    monkeypatch.setattr(slitaz_image, "MAX_ROOTFS", 100)
    data = lzma.compress(system_cpio(), format=lzma.FORMAT_ALONE)
    with pytest.raises(RuntimeError, match="too large"):
        slitaz_image.decompress(io.BytesIO(data), 0, len(data), tmp_path / "rootfs.cpio")


def test_converts_the_system_to_a_tar_for_podman(tmp_path):
    archive = tmp_path / "rootfs.tar"
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:
        slitaz_image.cpio_to_tar(
            io.BytesIO(system_cpio()),
            tar,
            {"etc/os-release": b"ID=slitaz\n", "var/cache/multipak/bootstrap/01-a.tazpkg": b"A"},
        )
    members = tar_members(archive)
    assert members["bin/busybox"][1] == b"BUSYBOX"
    assert members["bin/busybox"][0].mode == 0o4755
    assert members["bin/sh"][0].issym() and members["bin/sh"][0].linkname == "busybox"
    assert members["sbin/tune2fs"][1] == b"TUNE"
    assert members["sbin/e2label"][0].islnk()
    assert members["sbin/e2label"][0].linkname == "sbin/tune2fs"
    assert members["etc/os-release"][1] == b"ID=slitaz\n"
    assert members["var/cache/multipak/bootstrap/01-a.tazpkg"][1] == b"A"
    assert "var/cache/multipak" in members
    assert not any(name.startswith(("dev", "boot")) or name == "init" for name in members)
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert len(names) == len(set(names))


def test_rejects_paths_outside_the_system(tmp_path):
    bad = system_cpio(extra=[("../etc/passwd", b"x", stat.S_IFREG | 0o644, 20, 1)])
    with tarfile.open(tmp_path / "rootfs.tar", "w") as tar, pytest.raises(RuntimeError):
        slitaz_image.cpio_to_tar(io.BytesIO(bad), tar, {})


def test_reads_the_installed_packages():
    found = slitaz_image.installed_packages(io.BytesIO(system_cpio()))
    assert found == {"busybox", "glibc-base"}


INFO = "\n".join(
    "\t".join([name, version, "misc", "", "", "", "1K 2K", depends, "receipt-sum", "", "1"])
    for name, version, depends in (
        ("wget", "1.21.3", "openssl libidn glibc-base"),
        ("openssl", "3.5.3", "libatomic"),
        ("libatomic", "6.3.0", ""),
        ("libidn", "1.38", "glibc-base"),
        ("cacerts", "20250709", ""),
    )
)
SUMS = {
    "wget-1.21.3-x86_64.tazpkg": "a" * 32,
    "openssl-3.5.3-x86_64.tazpkg": "b" * 32,
    "libatomic-6.3.0-x86_64.tazpkg": "c" * 32,
    "libidn-1.38-x86_64.tazpkg": "d" * 32,
    "cacerts-20250709-any.tazpkg": "e" * 32,
}


def md5_list(sums):
    return "".join(f"{value}  {name}\n" for name, value in sums.items())


def test_bootstrap_packages_come_with_dependencies_first():
    order = slitaz_image.bootstrap_packages(INFO, md5_list(SUMS), {"glibc-base"})
    assert order == [
        ("cacerts-20250709-any.tazpkg", "e" * 32),
        ("libatomic-6.3.0-x86_64.tazpkg", "c" * 32),
        ("openssl-3.5.3-x86_64.tazpkg", "b" * 32),
        ("libidn-1.38-x86_64.tazpkg", "d" * 32),
        ("wget-1.21.3-x86_64.tazpkg", "a" * 32),
    ]
    missing = {name: value for name, value in SUMS.items() if not name.startswith("libidn")}
    with pytest.raises(RuntimeError, match="libidn"):
        slitaz_image.bootstrap_packages(INFO, md5_list(missing), {"glibc-base"})


class Reply(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class Mirror:
    def __init__(self, files):
        self.files, self.requests = files, []

    def open(self, request, timeout):
        self.requests.append(request.full_url)
        name = request.full_url.removeprefix(slitaz_image.MIRROR + "/")
        if name not in self.files:
            raise urllib.error.URLError("not found")
        return Reply(self.files[name])


class ImageRunner:
    def __init__(self, exists=False):
        self.exists, self.calls, self.members = exists, [], {}

    def run(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:3] == ["podman", "image", "exists"]:
            if not self.exists:
                raise CommandError(argv, 1, "")
        if argv[:2] == ["podman", "import"]:
            self.members = tar_members(argv[-2])
        return ""


def mirror_files(arch="x86_64", *, iso=None):
    iso = iso or make_iso(lzma.compress(system_cpio(), format=lzma.FORMAT_ALONE))
    packages = {name: name.encode() * 10 for name in SUMS}
    sums = {name: hashlib.md5(data).hexdigest() for name, data in packages.items()}
    base = f"{arch}/cooking/iso/slitaz-base-{arch}-cooking"
    return {
        f"{base}.iso": iso,
        f"{base}.md5": f"{hashlib.md5(iso).hexdigest()}  slitaz-base-{arch}-cooking.iso\n".encode(),
        f"{arch}/cooking/packages/packages.info": INFO.encode(),
        f"{arch}/cooking/packages/packages.md5": md5_list(sums).encode(),
        **{f"{arch}/cooking/packages/{name}": data for name, data in packages.items()},
    }


def test_builds_the_image_from_the_base_iso(tmp_path):
    runner, mirror = ImageRunner(), Mirror(mirror_files())
    reported = []
    slitaz_image.ensure_image(
        runner,
        tmp_path / "work",
        SLITAZ_ENVIRONMENT,
        opener=mirror,
        progress=lambda *item: reported.append(item),
    )
    command = runner.calls[-1]
    assert command[:4] == ["podman", "import", "--arch", "amd64"]
    assert command[-1] == SLITAZ_ENVIRONMENT.image
    members = runner.members
    assert members["etc/os-release"][1] == slitaz_image.os_release("x86_64")
    assert members["var/lib/tazpkg/mirror"][1] == (
        b"https://mirror.slitaz.org/x86_64/cooking/packages/\n"
    )
    bootstrap = sorted(
        name for name in members if name.startswith(slitaz_image.BOOTSTRAP_DIRECTORY + "/")
    )
    assert bootstrap[-1].endswith("05-wget-1.21.3-x86_64.tazpkg")
    assert members[bootstrap[-1]][1] == b"wget-1.21.3-x86_64.tazpkg" * 10
    assert "bin/busybox" in members
    assert any(label.startswith("Downloading") for _percent, label in reported)
    # The work area is removed.
    assert list((tmp_path / "work").iterdir()) == []


def test_the_32_bit_image_uses_its_own_repository(tmp_path):
    runner, mirror = ImageRunner(), Mirror(mirror_files("i486"))
    slitaz_image.ensure_image(runner, tmp_path, SLITAZ_I486_ENVIRONMENT, opener=mirror)
    assert runner.calls[-1][:4] == ["podman", "import", "--arch", "386"]
    assert runner.members["etc/os-release"][1] == slitaz_image.os_release("i486")
    assert all("/i486/" in url for url in mirror.requests)


def test_an_existing_image_is_not_rebuilt(tmp_path):
    runner, mirror = ImageRunner(exists=True), Mirror({})
    slitaz_image.ensure_image(runner, tmp_path, SLITAZ_ENVIRONMENT, opener=mirror)
    assert mirror.requests == [] and len(runner.calls) == 1


def test_refuses_a_download_that_fails_verification(tmp_path):
    files = mirror_files()
    files["x86_64/cooking/packages/wget-1.21.3-x86_64.tazpkg"] = b"tampered"
    runner = ImageRunner()
    with pytest.raises(RuntimeError, match="failed verification"):
        slitaz_image.ensure_image(runner, tmp_path, SLITAZ_ENVIRONMENT, opener=Mirror(files))
    assert not any(call[:2] == ["podman", "import"] for call in runner.calls)
    files = mirror_files()
    files["x86_64/cooking/iso/slitaz-base-x86_64-cooking.md5"] = b"not a checksum\n"
    with pytest.raises(RuntimeError, match="invalid checksum"):
        slitaz_image.ensure_image(runner, tmp_path, SLITAZ_ENVIRONMENT, opener=Mirror(files))


def test_limits_the_size_of_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(slitaz_image, "MAX_ISO", 1000)
    with pytest.raises(RuntimeError, match="larger than expected"):
        slitaz_image.ensure_image(
            ImageRunner(), tmp_path, SLITAZ_ENVIRONMENT, opener=Mirror(mirror_files())
        )


def test_redirects_must_stay_on_https():
    handler = next(
        item
        for item in slitaz_image._opener().handlers
        if isinstance(item, slitaz_image.urllib.request.HTTPRedirectHandler)
    )
    request = slitaz_image.urllib.request.Request("https://mirror.slitaz.org/x")
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(request, None, 302, "Found", {}, "http://mirror.slitaz.org/x")
