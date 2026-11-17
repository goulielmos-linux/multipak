import io
import lzma
import tarfile
from pathlib import Path

import pytest
from test_puppy import SPECS, _elf, make_pet

from multipak import guest, metadata, puppy, slackware
from multipak.file_associations import FORMATS, FileAssociations
from multipak.metadata import inspect_package
from multipak.models import ENVIRONMENTS, package_environment_kinds, package_profiles
from multipak.suggestions import suggestion_details

DEBIAN = next(item for item in ENVIRONMENTS if item.id == "debian-12")
SLACKWARE = next(item for item in ENVIRONMENTS if item.id == "slackware-15.0")
ARCH = next(item for item in ENVIRONMENTS if item.id == "arch-rolling")


def full_pet(path):
    return make_pet(
        path,
        members=[
            ("demo-app-2.1/pet.specs", SPECS),
            ("demo-app-2.1/usr/bin/demo", _elf(62)),
            ("demo-app-2.1/usr/bin/demo-link", "demo"),
            ("demo-app-2.1/usr/share/applications/demo.desktop", b"[Desktop Entry]\n"),
            ("demo-app-2.1/pinstall.sh", b"#!/bin/sh\necho installed\n"),
            ("demo-app-2.1/puninstall.sh", b"#!/bin/sh\necho removed\n"),
        ],
    )


def guest_commands(engine):
    return [
        call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


@pytest.fixture
def x86(monkeypatch):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")


def _deb_members(path):
    data = path.read_bytes()
    assert data.startswith(b"!<arch>\n")
    offset, members = 8, {}
    while offset < len(data):
        header = data[offset : offset + 60]
        name, size = header[:16].decode().strip(), int(header[48:58])
        members[name] = data[offset + 60 : offset + 60 + size]
        offset += 60 + size + size % 2
    return members


def test_names_follow_each_distribution_rules():
    assert puppy.deb_name("LibreOffice_7") == "libreoffice-7"
    assert puppy.deb_name("X") == "pet-x"
    assert puppy.deb_version("2.1-3") == "2.1-3"
    assert puppy.deb_version("beta2") == "0~beta2"
    assert puppy.deb_version("1.0-rc-2") == "1.0.rc-2"
    assert puppy.deb_version("") == "0"
    assert puppy.slackware_file("demo-app", "2.1-3", "x86_64") == (
        "demo-app-2.1_3-x86_64-1_pet.txz"
    )
    assert puppy.slackware_file("tool", "", "noarch") == "tool-0-noarch-1_pet.txz"
    assert slackware.split_name(puppy.slackware_file("demo-app", "2.1-3", "x86_64"))


@pytest.mark.parametrize(
    "distro,release,expected",
    [
        ("debian", "bookworm", "debian-12"),
        ("Debian", "12", "debian-12"),
        ("ubuntu", "jammy", "ubuntu-22.04"),
        ("ubuntu", "focal", "ubuntu-20.04"),
        ("slackware", "15.0", "slackware-15.0"),
        ("slackware64", "15.0", "slackware-15.0"),
        ("slackware", "14.2", "slackware-14.2"),
        ("devuan", "daedalus", ""),
        ("puppy", "5", ""),
        ("debian", "sid", ""),
        ("", "", ""),
    ],
)
def test_maps_pet_specs_distribution_to_an_environment(distro, release, expected):
    assert puppy.compatible_environment(distro, release) == expected


def test_version_leaves_out_the_architecture_and_puppy_build(tmp_path):
    specs = SPECS.replace(b"|2.1|3|", b"|3.0.4-x86_64_s84||")
    path = make_pet(tmp_path / "a.pet", members=[("demo-app-2.1/pet.specs", specs)])
    assert (puppy.read_info(path).version, puppy.read_info(path).release) == ("3.0.4", "")


def test_reads_puppy_dependency_names():
    assert puppy.dependency_names("+gtk+3,+libfoo&ge1.2\n+bar|baz\n+gtk+3\n") == [
        "gtk+3",
        "libfoo",
        "bar",
    ]


def test_converts_to_a_slackware_package(tmp_path):
    source = full_pet(tmp_path / "demo.pet")
    target = tmp_path / puppy.slackware_file("demo-app", "2.1-3", "x86_64")
    puppy.to_slackware(source, target, puppy.read_info(source))
    info = slackware.read_info(target)
    assert (info.name, info.version, info.arch, info.build) == (
        "demo-app",
        "2.1_3",
        "x86_64",
        "1_pet",
    )
    with tarfile.open(target) as archive:
        names = archive.getnames()
        assert "pet.specs" not in " ".join(names)
        binary = archive.getmember("usr/bin/demo")
        assert (binary.uid, binary.gid, binary.uname) == (0, 0, "root")
        assert archive.getmember("usr/bin/demo-link").linkname == "demo"
        assert archive.getmember("usr").isdir()
        script = "usr/share/multipak/puppy/demo-app/pinstall.sh"
        assert archive.extractfile(script).read() == b"#!/bin/sh\necho installed\n"
        doinst = archive.extractfile("install/doinst.sh").read().decode()
        assert f"sh /{script}" in doinst and "|| echo" in doinst


def test_slackware_conversion_refuses_a_pet_with_an_install_folder(tmp_path):
    source = make_pet(
        tmp_path / "demo.pet",
        members=[("demo-app-2.1/pet.specs", SPECS), ("demo-app-2.1/install/x", b"x")],
    )
    with pytest.raises(ValueError, match="reserved folder"):
        puppy.to_slackware(
            source, tmp_path / "demo-app-2.1-noarch-1_pet.txz", puppy.read_info(source)
        )


def test_converts_to_a_debian_package(tmp_path):
    source = full_pet(tmp_path / "demo.pet")
    target = tmp_path / "package.deb"
    puppy.to_deb(source, target, puppy.read_info(source), "demo-app", "2.1-3")
    fields = metadata._deb_fields(target)
    assert (fields["Package"], fields["Version"], fields["Architecture"]) == (
        "demo-app",
        "2.1-3",
        "amd64",
    )
    assert "Depends" not in fields
    members = _deb_members(target)
    assert list(members) == ["debian-binary", "control.tar.xz", "data.tar.xz"]
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(members["control.tar.xz"]))) as tar:
        postinst = tar.extractfile("./postinst").read().decode()
        prerm = tar.extractfile("./prerm").read().decode()
        assert tar.getmember("./postinst").mode == 0o755
    assert '[ "$1" = configure ] || exit 0' in postinst and postinst.endswith("exit 0\n")
    assert "puninstall.sh" in prerm
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(members["data.tar.xz"]))) as tar:
        assert "./usr/bin/demo" in tar.getnames()
        assert "./usr/share/multipak/puppy/demo-app/puninstall.sh" in tar.getnames()
    assert not list(tmp_path.glob("*.data.tar.xz"))


def test_conversion_drops_setuid_bits_and_refuses_devices(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo("demo-app-2.1/usr/bin/demo")
        member.mode, member.size = 0o4755, 4
        archive.addfile(member, io.BytesIO(b"demo"))
    import gzip
    import hashlib

    data = gzip.compress(stream.getvalue())
    path = tmp_path / "demo.pet"
    path.write_bytes(data + hashlib.md5(data, usedforsecurity=False).hexdigest().encode())
    target = tmp_path / "package.deb"
    puppy.to_deb(path, target, puppy.read_info(path), "demo-app", "2.1")
    data_tar = lzma.decompress(_deb_members(target)["data.tar.xz"])
    with tarfile.open(fileobj=io.BytesIO(data_tar)) as tar:
        assert tar.getmember("./usr/bin/demo").mode == 0o755

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        device = tarfile.TarInfo("demo-app-2.1/dev/null")
        device.type = tarfile.CHRTYPE
        archive.addfile(device)
        archive.addfile(tarfile.TarInfo("demo-app-2.1/usr/bin/x"), io.BytesIO(b""))
    data = gzip.compress(stream.getvalue())
    path.write_bytes(data + hashlib.md5(data, usedforsecurity=False).hexdigest().encode())
    with pytest.raises(ValueError, match="special file"):
        puppy.to_deb(path, tmp_path / "other.deb", puppy.read_info(path), "demo-app", "2.1")


def test_installs_a_pet_in_debian_as_a_converted_package(fake_engine, tmp_path, x86):
    engine = fake_engine
    engine.runner.release = 'ID=debian\nVERSION_ID="12"\n'
    engine.runner.puppy_result = {"added": ["libgtk-3-0"], "missing": ["libpuppy.so.1"]}
    package = inspect_package(full_pet(tmp_path / "demo.pet"), engine.runner)
    tx = engine.install(package, DEBIAN)
    assert tx.status == "installed"
    assert (tx.package_kind, tx.package_name, tx.converted_from) == ("deb", "demo-app", "puppy")
    assert tx.puppy_dependencies == ["library", "tool"]
    staged = f"/tmp/multipak-{tx.id}/package.deb"
    commands = guest_commands(engine)
    dependencies = next(
        index for index, item in enumerate(commands) if "--puppy-deb-dependencies" in item
    )
    assert commands[dependencies][-4:] == ["--puppy-deb-dependencies", staged, "library", "tool"]
    installation = next(
        index
        for index, item in enumerate(commands)
        if "apt-get" in item and item[-2:] == ["install", staged]
    )
    assert dependencies < installation
    assert any("libpuppy.so.1" in warning for warning in tx.warnings)
    assert not list(engine.store.directory(tx.id).glob("*.pet"))


def test_installs_a_pet_in_slackware_with_its_dependency_names(fake_engine, tmp_path, x86):
    engine = fake_engine
    engine.runner.release = "ID=slackware\nVERSION_ID=15.0\n"
    package = inspect_package(full_pet(tmp_path / "demo.pet"), engine.runner)
    tx = engine.install(package, SLACKWARE)
    assert tx.status == "installed"
    assert (tx.package_kind, tx.package_file) == ("slackware", "demo-app-2.1_3-x86_64-1_pet.txz")
    commands = guest_commands(engine)
    staged = f"/tmp/multipak-{tx.id}/demo-app-2.1_3-x86_64-1_pet.txz"
    assert ["sudo", "-n", "/sbin/installpkg", staged] in commands
    dependencies = next(item for item in commands if "--slackware-dependencies" in item)
    assert dependencies[-4:] == ["light", "demo-app", "library", "tool"]


def test_missing_libraries_point_to_other_pets(fake_engine, tmp_path, x86):
    engine = fake_engine
    engine.runner.release = "ID=slackware\nVERSION_ID=15.0\n"
    engine.runner.slackware_result = {"added": [], "missing": ["libgoffice-0.10.so.10"]}
    tx = engine.install(inspect_package(full_pet(tmp_path / "demo.pet"), engine.runner), SLACKWARE)
    assert tx.warnings == [
        "Some libraries were not found in the distribution's packages: libgoffice-0.10.so.10. "
        "Puppy Linux may provide them in other .pet packages; install those in the same "
        "environment first. Otherwise the application may not open."
    ]


def test_refuses_an_environment_that_cannot_receive_a_pet(fake_engine, tmp_path, x86):
    package = inspect_package(full_pet(tmp_path / "demo.pet"), fake_engine.runner)
    with pytest.raises(ValueError, match="does not match the package format"):
        fake_engine.install(package, ARCH)


def test_pets_are_offered_slackware_and_debian_environments(tmp_path, x86):
    package = inspect_package(full_pet(tmp_path / "demo.pet"), None)
    assert package_environment_kinds(package) == ("slackware", "deb")
    strength, text = suggestion_details(package, DEBIAN, "Debian 12")
    assert "pet.specs says this package was built for Debian 12" in text
    unknown = inspect_package(
        make_pet(tmp_path / "old.pet", members=[("old-tool-1.2/usr/bin/x", b"#!/bin/sh\n")]),
        None,
    )
    strength, text = suggestion_details(unknown, DEBIAN, "Debian 12")
    assert "Compatibility is uncertain" in text


def test_deb_providers_prefer_system_library_folders(monkeypatch):
    class Result:
        returncode = 0
        stdout = (
            "someapp: /opt/someapp/lib/libfoo.so.1\n"
            "libfoo1: /usr/lib/x86_64-linux-gnu/libfoo.so.1\n"
            "libfoo1-dbgsym: /usr/lib/debug/libfoo.so.1\n"
            "libbar2: /usr/lib/x86_64-linux-gnu/libbar.so.2\n"
        )

    calls = []
    monkeypatch.setattr(guest.subprocess, "run", lambda argv, **_: calls.append(argv) or Result())
    assert guest.deb_providers({"libfoo.so.1", "libbar.so.2", "libnone.so"}) == {
        "libfoo.so.1": "libfoo1",
        "libbar.so.2": "libbar2",
    }
    assert ["apt-file", "update"] in calls


def test_runs_a_converted_pet_script_and_ignores_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(guest, "PUPPY_SCRIPTS", tmp_path)
    marker = tmp_path / "ran"
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "puninstall.sh").write_text(f"touch '{marker}'\nexit 3\n")
    guest.run_puppy_script("demo", "puninstall.sh")
    assert marker.exists()
    guest.run_puppy_script("../demo", "puninstall.sh")
    guest.run_puppy_script("missing", "puninstall.sh")


def test_association_registers_the_puppy_type(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    settings = FileAssociations(
        config_home=tmp_path / "config",
        data_home=tmp_path / "data",
        refresher=lambda: None,
        reader=lambda _mime: "other.desktop",
    )
    settings.apply({key: True for key in FORMATS})
    text = settings.puppy_xml_path.read_text()
    assert 'type="application/x-puppy-pet"' in text and 'pattern="*.pet"' in text
    settings.apply({key: key != "puppy" for key in FORMATS})
    assert not settings.puppy_xml_path.exists()
    desktop = Path(__file__).parents[1] / "data/io.github.multipak.installer.desktop"
    assert "application/x-puppy-pet;" in desktop.read_text()


def test_offers_only_the_family_pet_specs_names(tmp_path, x86):
    package = inspect_package(full_pet(tmp_path / "demo.pet"), None)
    assert {item.id.partition("-")[0] for item in package_profiles(package, ENVIRONMENTS)} == {
        "debian"
    }
    old = SPECS.replace(b"|debian|bookworm|", b"|slackware64|14.0|")
    path = make_pet(tmp_path / "old.pet", members=[("demo-app-2.1/pet.specs", old)])
    package = inspect_package(path, None)
    assert package.distro_hint == "slackware-14.0"
    assert {item.kind for item in package_profiles(package, ENVIRONMENTS)} == {"slackware"}
    unknown = SPECS.replace(b"|debian|bookworm|", b"|||")
    path = make_pet(tmp_path / "any.pet", members=[("demo-app-2.1/pet.specs", unknown)])
    package = inspect_package(path, None)
    assert {item.kind for item in package_profiles(package, ENVIRONMENTS)} == {"slackware", "deb"}
