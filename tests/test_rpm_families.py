"""RPM packages of Mageia, openSUSE and ALT Linux, installed by their own package managers."""

import json
from pathlib import Path

import pytest

from multipak import catalog
from multipak.environment_choices import container_base
from multipak.environments import release_identity
from multipak.metadata import inspect_package, rpm_metadata_hint, rpm_release_hints
from multipak.models import (
    ENVIRONMENTS,
    RPM_FAMILY_ENVIRONMENTS,
    ExistingEnvironment,
    Package,
    rpm_environment,
)
from multipak.suggestions import suggestion_details


@pytest.mark.parametrize(
    "release,distribution,hint",
    [
        ("2.mga10", "Mageia", "mageia-10"),
        ("1.mga9", "Mageia", "mageia-9"),
        ("lp156.2.1", "openSUSE Leap 15.6", "opensuse-15.6"),
        ("bp161.1.2", "openSUSE Backports SLE-16.1", "opensuse-16.1"),
        ("160100.2.1", "SUSE Linux 16", "opensuse-16.1"),
        ("150600.3.1", "SUSE Linux Enterprise 15", "opensuse-15.6"),
        ("3.9", "openSUSE Tumbleweed", "opensuse-tumbleweed"),
        ("1.1", "openSUSE Leap 16.0", "opensuse-16.0"),
        ("alt1", "ALT p11", "alt-p11"),
        ("alt1", "ALT Sisyphus", "alt"),
        ("alt0.p10.1", "ALT Sisyphus", "alt-p10"),
        ("alt2", "(none)", "alt"),
        ("1.fc44", "Fedora Project", "fedora-44"),
        ("1", "(none)", ""),
        ("1", "Some Vendor Linux", ""),
    ],
)
def test_rpm_header_names_the_distribution_and_release(release, distribution, hint):
    assert rpm_metadata_hint(release, distribution) == hint


def test_release_hints_do_not_read_versions_or_dates_as_distributions():
    assert rpm_release_hints("20261111") == []
    assert rpm_release_hints("2.1") == []
    assert rpm_release_hints("1.salt3") == []
    assert rpm_release_hints("gnome-calculator-48.1-160100.2.1.x86_64.rpm") == ["opensuse-16.1"]
    assert rpm_release_hints("galculator-2.1.4-alt1.x86_64.rpm") == ["alt"]
    assert rpm_release_hints("gpicview-0.2.5-3.mga10.x86_64.rpm") == ["mageia-10"]


def test_inspect_reads_the_distribution_field(tmp_path):
    path = tmp_path / "galculator.rpm"
    path.write_bytes(b"\xed\xab\xee\xdb\x03\x00\x00\x00")

    class Query:
        def run(self, argv, **options):
            if "--queryformat" in argv:
                return b"galculator\x1f2.1.4-3.9\x1fx86_64\x1fopenSUSE Tumbleweed"
            return "libgtk-3.so.0()(64bit)"

    package = inspect_package(path, Query())
    assert (package.distro_hint, package.distro_hint_source) == ("opensuse-tumbleweed", "metadata")


@pytest.mark.parametrize(
    "release,expected",
    [
        ({"ID": "mageia", "VERSION_ID": "10"}, ("mageia", "10")),
        ({"ID": "opensuse-leap", "VERSION_ID": "16.1"}, ("opensuse", "16.1")),
        ({"ID": "opensuse-tumbleweed", "VERSION_ID": "20261111"}, ("opensuse", "tumbleweed")),
        ({"ID": "altlinux", "VERSION_ID": "11", "ALT_BRANCH_ID": "p11"}, ("alt", "p11")),
        (
            {"ID": "altlinux", "VERSION_ID": "20260316", "ALT_BRANCH_ID": "sisyphus"},
            ("alt", "sisyphus"),
        ),
        ({"ID": "altlinux", "VERSION_ID": "10.1"}, ("alt", "p10")),
        ({"ID": "altlinux", "VERSION_ID": "20260316"}, ("alt", "sisyphus")),
        ({"ID": "alpine", "VERSION_ID": "3.24.3"}, ("alpine", "3.24")),
        ({"ID": "fedora", "VERSION_ID": "44"}, ("fedora", "44")),
    ],
)
def test_os_release_names_multipaks_family_and_release(release, expected):
    assert release_identity(release) == expected


@pytest.mark.parametrize(
    "environment_id,name",
    [
        ("mageia-10", "MultiPak-Mageia-10"),
        ("opensuse-16.1", "MultiPak-openSUSE-16.1"),
        ("opensuse-tumbleweed", "MultiPak-openSUSE-Tumbleweed"),
        ("alt-p11", "MultiPak-ALT-p11"),
        ("alt-sisyphus", "MultiPak-ALT-Sisyphus"),
    ],
)
def test_containers_keep_the_distributions_spelling(environment_id, name):
    environment = next(item for item in ENVIRONMENTS if item.id == environment_id)
    assert container_base(environment) == name
    family, _, version = environment_id.partition("-")
    existing = ExistingEnvironment(name, "id", family, version)
    assert existing.multipak_number == 1
    assert existing.matches(environment)
    assert existing.display_name == "MultiPak - " + name.removeprefix("MultiPak-").replace("-", " ")


def test_existing_release_does_not_match_another_line():
    leap = ExistingEnvironment("MultiPak-openSUSE-16.1", "id", "opensuse", "16.1")
    assert not leap.matches(rpm_environment("opensuse", "tumbleweed"))
    assert not leap.matches(rpm_environment("opensuse", "16.0"))


def test_rpm_environment_rejects_unknown_releases():
    for family, release in (("alt", "11"), ("opensuse", "16"), ("mageia", "cauldron"), ("x", "1")):
        with pytest.raises(ValueError):
            rpm_environment(family, release)


class TagRunner:
    def __init__(self, tags):
        self.tags = tags
        self.calls = []

    def run(self, argv, **_options):
        self.calls.append(argv)
        if argv[:2] == ["skopeo", "list-tags"]:
            repository = argv[2].removeprefix("docker://")
            if repository not in self.tags:
                raise catalog.CommandError(argv, 1, "unavailable")
            return json.dumps({"Tags": self.tags[repository]})
        return ""


TAGS = {
    "docker.io/library/mageia": ["3", "8", "9", "10", "cauldron", "latest", "rc"],
    "docker.io/opensuse/leap": ["15", "15.6", "16", "16.0", "16.1", "42", "42.3", "latest"],
    "docker.io/library/alt": ["latest", "p8", "p9", "p10", "p11", "sisyphus"],
}


def test_catalog_lists_supported_releases_then_the_rolling_line(monkeypatch):
    monkeypatch.setattr("multipak.catalog.host.which", lambda name: f"/usr/bin/{name}")
    runner = TagRunner(TAGS)
    found = {
        family: catalog.rpm_family_profiles(runner, family)
        for family in ("mageia", "opensuse", "alt")
    }
    assert [item.id for item in found["mageia"][0]] == ["mageia-10", "mageia-9"]
    assert [item.id for item in found["opensuse"][0]] == [
        "opensuse-16.1",
        "opensuse-16.0",
        "opensuse-tumbleweed",
    ]
    assert found["opensuse"][0][-1].image == "docker.io/opensuse/tumbleweed:latest"
    assert [item.id for item in found["alt"][0]] == ["alt-p11", "alt-p10", "alt-sisyphus"]
    assert all(refreshed for _, refreshed in found.values())


def test_catalog_offline_uses_cache_then_fallback(monkeypatch):
    monkeypatch.setattr("multipak.catalog.host.which", lambda name: f"/usr/bin/{name}")
    runner = TagRunner({})
    cached = (rpm_environment("alt", "p12"),)
    assert catalog.rpm_family_profiles(runner, "alt", cached) == ([cached[0]], False)
    profiles, refreshed = catalog.rpm_family_profiles(runner, "mageia")
    assert not refreshed
    assert [item.id for item in profiles] == ["mageia-10", "mageia-9"]


def test_cached_catalog_validates_rpm_family_profiles(tmp_path):
    from dataclasses import asdict, replace

    cache = tmp_path / "catalog.json"
    cache.write_text(json.dumps([asdict(item) for item in RPM_FAMILY_ENVIRONMENTS]))
    loaded = catalog.read_cached(cache)
    assert set(RPM_FAMILY_ENVIRONMENTS) <= set(loaded)
    forged = replace(rpm_environment("alt", "p11"), image="docker.io/evil/alt:p11")
    cache.write_text(json.dumps([asdict(forged)]))
    assert catalog.read_cached(cache) == ()


def test_older_caches_gain_the_new_distributions(tmp_path):
    from dataclasses import asdict

    fedora = next(item for item in ENVIRONMENTS if item.id == "fedora-44")
    cache = tmp_path / "catalog.json"
    cache.write_text(json.dumps([asdict(fedora)]))
    ids = {item.id for item in catalog.read_cached(cache)}
    assert {"mageia-10", "opensuse-tumbleweed", "alt-p11"} <= ids


@pytest.mark.parametrize(
    "hint,expected,image",
    [
        ("mageia-10", "mageia-10", "docker.io/library/mageia:10"),
        # An older release is still offered, for its libraries.
        ("mageia-8", "mageia-8", "docker.io/library/mageia:8"),
        ("opensuse-15.6", "opensuse-15.6", "docker.io/opensuse/leap:15.6"),
        # Newer than the stable releases: the rolling line, if the family has one.
        ("mageia-11", "mageia-10", "docker.io/library/mageia:10"),
        ("opensuse-16.2", "opensuse-tumbleweed", "docker.io/opensuse/tumbleweed:latest"),
        ("alt-p12", "alt-sisyphus", "docker.io/library/alt:sisyphus"),
        # ALT names no branch: its newest stable branch.
        ("alt", "alt-p11", "docker.io/library/alt:p11"),
        ("opensuse-tumbleweed", "opensuse-tumbleweed", "docker.io/opensuse/tumbleweed:latest"),
    ],
)
def test_hint_chooses_an_environment(hint, expected, image):
    environment = catalog.package_environment(hint, list(ENVIRONMENTS))
    assert environment is not None
    assert (environment.id, environment.image) == (expected, image)


def test_bad_hints_choose_nothing():
    for hint in ("alt-p11;rm", "opensuse-16", "mageia-x", "alt-11"):
        assert catalog.package_environment(hint, list(ENVIRONMENTS)) is None
    fedora_only = [item for item in ENVIRONMENTS if item.id.startswith("fedora-")]
    assert catalog.package_environment("mageia-10", fedora_only) is None


def test_alternatives_skip_rolling_lines():
    leap = rpm_environment("opensuse", "16.1")
    assert [item.id for item in catalog.alternatives(list(ENVIRONMENTS), leap)] == ["opensuse-16.0"]
    alt = rpm_environment("alt", "p11")
    assert [item.id for item in catalog.alternatives(list(ENVIRONMENTS), alt)] == ["alt-p10"]


def rpm_package(hint="", source="metadata"):
    return Package(Path("/tmp/app.rpm"), "rpm", "app", "1", "x86_64", "", hint, source)


@pytest.mark.parametrize(
    "hint,environment_id,strength,text",
    [
        (
            "mageia-10",
            "mageia-10",
            "High",
            "built for Mageia 10. MultiPak installs it in Mageia 10",
        ),
        ("opensuse-tumbleweed", "opensuse-tumbleweed", "High", "zypper"),
        ("alt-p11", "alt-p11", "High", "APT-RPM"),
        ("alt", "alt-p11", "Medium", "identifies ALT Linux but not the release"),
        ("opensuse-16.2", "opensuse-tumbleweed", "Medium", "newer than the releases"),
        ("mageia-10", "fedora-44", "Low", "could not offer an environment"),
    ],
)
def test_suggestions_explain_rpm_family_evidence(hint, environment_id, strength, text):
    environment = next(item for item in ENVIRONMENTS if item.id == environment_id)
    found, explanation = suggestion_details(rpm_package(hint), environment, environment.title)
    assert found == strength
    assert text in explanation


def test_filename_conflict_names_the_other_distribution():
    package = Package(
        Path("/tmp/app.mga10.rpm"),
        "rpm",
        "app",
        "1.fc44",
        "x86_64",
        "",
        "fedora-44",
        "metadata",
        "mageia-10",
    )
    fedora = next(item for item in ENVIRONMENTS if item.id == "fedora-44")
    _, explanation = suggestion_details(package, fedora, fedora.title)
    assert "built for Mageia 10" in explanation


def install_in(engine, monkeypatch, tmp_path, environment_id, release):
    from multipak.models import ExistingEnvironment

    path = tmp_path / "app.rpm"
    path.write_bytes(b"rpm fixture")
    package = Package(path, "rpm", "demo", "1-1", "x86_64")
    family, _, version = environment_id.partition("-")
    engine.runner.image_exists = True
    engine.runner.container = {
        "Name": "Rpm-apps",
        "Id": "container123",
        "Image": "sha256:image123",
        "Config": {"Labels": {"manager": "distrobox"}},
    }
    engine.runner.release = release
    existing = ExistingEnvironment("Rpm-apps", "container123", family, version)
    monkeypatch.setattr("multipak.engine.inspect_package", lambda *_: package)
    environment = next(item for item in ENVIRONMENTS if item.id == environment_id)
    return engine.install(package, environment, existing)


def package_commands(engine):
    return [
        call[call.index("--run") + 2 :] if "--run" in call else call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


def test_mageia_uses_dnf(fake_engine, monkeypatch, tmp_path):
    tx = install_in(fake_engine, monkeypatch, tmp_path, "mageia-10", "ID=mageia\nVERSION_ID=10\n")
    assert tx.status == "installed"
    commands = package_commands(fake_engine)
    assert any(command[:3] == ["dnf", "-y", "install"] for command in commands)
    assert not any("zypper" in command or "apt-get" in command for command in commands)


SIGNED = (
    "app.rpm:\n    Header V4 RSA/SHA512 Signature, key ID 09d9ea69: OK\n"
    "    Header SHA256 digest: OK\n"
)


def test_opensuse_uses_zypper_and_keeps_signature_checks_for_signed_packages(
    fake_engine, monkeypatch, tmp_path
):
    fake_engine.runner.rpm_signature = SIGNED
    tx = install_in(
        fake_engine,
        monkeypatch,
        tmp_path,
        "opensuse-tumbleweed",
        'ID="opensuse-tumbleweed"\nVERSION_ID="20261111"\n',
    )
    assert tx.status == "installed"
    assert tx.warnings == []
    commands = package_commands(fake_engine)
    assert ["zypper", "--non-interactive", "refresh"] in commands
    install = next(command for command in commands if "install" in command and "zypper" in command)
    assert install[:4] == ["zypper", "--non-interactive", "install", "--auto-agree-with-licenses"]
    assert "--allow-unsigned-rpm" not in install and "--no-gpg-checks" not in install
    prepare = next(i for i, command in enumerate(commands) if command[-1:] == ["--prepare"])
    assert commands[prepare][:1] == ["sh"] and commands[prepare][1].endswith("/rpm-setup.sh")
    assert prepare < commands.index(install)


@pytest.mark.parametrize("signature", ["app.rpm:\n    Header SHA256 digest: OK\n", None])
def test_zypper_installs_unverified_packages_with_a_warning(
    fake_engine, monkeypatch, tmp_path, signature
):
    fake_engine.runner.rpm_signature = signature
    tx = install_in(
        fake_engine,
        monkeypatch,
        tmp_path,
        "opensuse-16.1",
        'ID="opensuse-leap"\nVERSION_ID="16.1"\n',
    )
    assert tx.status == "installed"
    assert any("signature could not be verified" in item for item in tx.warnings)
    install = next(
        command
        for command in package_commands(fake_engine)
        if "install" in command and "zypper" in command
    )
    assert "--no-gpg-checks" in install and "--allow-unsigned-rpm" in install


def test_alt_uses_apt_rpm(fake_engine, monkeypatch, tmp_path):
    tx = install_in(
        fake_engine,
        monkeypatch,
        tmp_path,
        "alt-p11",
        'ID=altlinux\nVERSION_ID=11\nALT_BRANCH_ID="p11"\n',
    )
    assert tx.status == "installed"
    commands = package_commands(fake_engine)
    assert ["apt-get", "update"] in commands
    assert any(command[:3] == ["apt-get", "-y", "install"] for command in commands)
    assert not any("dnf" in command for command in commands)


def test_existing_container_of_another_line_is_refused(fake_engine, monkeypatch, tmp_path):
    tx = None
    with pytest.raises(RuntimeError, match="changed"):
        tx = install_in(
            fake_engine,
            monkeypatch,
            tmp_path,
            "opensuse-16.1",
            'ID="opensuse-tumbleweed"\nVERSION_ID="20261111"\n',
        )
    assert tx is None


def test_new_containers_are_named_for_the_release(fake_engine, monkeypatch, tmp_path):
    path = tmp_path / "app.rpm"
    path.write_bytes(b"rpm fixture")
    package = Package(path, "rpm", "demo", "1-1", "x86_64")
    monkeypatch.setattr("multipak.engine.inspect_package", lambda *_: package)
    fake_engine.runner.release = 'ID=altlinux\nVERSION_ID=20260316\nALT_BRANCH_ID="sisyphus"\n'
    tx = fake_engine.install(package, rpm_environment("alt", "sisyphus"))
    assert tx.status == "installed"
    assert tx.container == "MultiPak-ALT-Sisyphus"
    assert tx.environment_id == "alt-sisyphus"


def test_new_mageia_containers_get_the_locale_sources_first(fake_engine, monkeypatch, tmp_path):
    path = tmp_path / "app.rpm"
    path.write_bytes(b"rpm fixture")
    package = Package(path, "rpm", "demo", "1-1", "x86_64")
    monkeypatch.setattr("multipak.engine.inspect_package", lambda *_: package)
    fake_engine.runner.release = "ID=mageia\nVERSION_ID=10\n"
    tx = fake_engine.install(package, rpm_environment("mageia", "10"))
    assert tx.status == "installed"
    create = next(call for call in fake_engine.runner.calls if call[:2] == ["distrobox", "create"])
    hook = create[create.index("--pre-init-hooks") + 1]
    assert "glibc-i18ndata" in hook and "/usr/share/i18n/charmaps" in hook


def test_fedora_also_follows_the_host_theme(fake_engine, monkeypatch, tmp_path):
    tx = install_in(fake_engine, monkeypatch, tmp_path, "fedora-44", "ID=fedora\nVERSION_ID=44\n")
    assert tx.status == "installed"
    commands = package_commands(fake_engine)
    prepare = next(i for i, command in enumerate(commands) if command[-1:] == ["--prepare"])
    assert prepare < next(i for i, command in enumerate(commands) if "dnf" in command)


def run_theme_script(tmp_path, dark):
    """rpm-setup.sh with its package manager and system folders redirected."""
    import os
    import subprocess

    root = tmp_path / "root"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "zypper").write_text('#!/bin/sh\necho "$@" >> "$LOG"\n')
    (bin_dir / "zypper").chmod(0o755)
    host = root / "run/host/home/user/.config/gtk-3.0"
    host.mkdir(parents=True)
    (host / "settings.ini").write_text(
        f"[Settings]\ngtk-application-prefer-dark-theme={'true' if dark else 'false'}\n"
    )
    for theme in ("Breeze", "Breeze-Dark"):
        (root / f"usr/share/themes/{theme}/gtk-2.0").mkdir(parents=True)
        (root / f"usr/share/themes/{theme}/gtk-2.0/gtkrc").write_text("")
    source = Path(__file__).parents[1] / "src/multipak/assets/rpm-setup.sh"
    text = source.read_text()
    for path in ("/run/host/", "/etc/", "/usr/share/themes/"):
        text = text.replace(path, f"{root}{path}")
    script = tmp_path / "rpm-setup.sh"
    script.write_text(text)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LOG": str(tmp_path / "log"),
        "SUDO_UID": str(os.getuid()),
    }
    subprocess.run(["sh", str(script), "--prepare"], check=True, env=env)
    return root, (tmp_path / "log").read_text()


@pytest.mark.parametrize("dark", [True, False])
def test_theme_script_follows_the_host(tmp_path, dark):
    root, log = run_theme_script(tmp_path, dark)
    assert "gtk2-metatheme-breeze6" in log and "adwaita-icon-theme" in log
    gtk3 = (root / "etc/xdg/gtk-3.0/settings.ini").read_text()
    assert f"gtk-application-prefer-dark-theme={'true' if dark else 'false'}" in gtk3
    gtk2 = (root / "etc/gtk-2.0/gtkrc").read_text()
    theme = "Breeze-Dark" if dark else "Breeze"
    assert f'include "{root}/usr/share/themes/{theme}/gtk-2.0/gtkrc"' in gtk2


def test_rpm_warnings_are_not_listed_as_dependencies(tmp_path):
    path = tmp_path / "gpicview.rpm"
    path.write_bytes(b"\xed\xab\xee\xdb\x03\x00\x00\x00")

    class Query:
        def run(self, argv, **options):
            if "--queryformat" in argv:
                return b"gpicview\x1f0.2.5-3.9\x1fx86_64\x1fopenSUSE Tumbleweed"
            return (
                f"warning: {path}: Header OpenPGP V3 RSA/SHA512 signature, key ID 35a2f86e: NOKEY\n"
                "/bin/sh\nlibX11.so.6()(64bit)\n"
            )

    package = inspect_package(path, Query())
    assert package.dependencies == "/bin/sh\nlibX11.so.6()(64bit)"
    assert str(path) not in package.dependencies
