import importlib.util
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_android import FakeRunner, inspect_apk
from test_android import setup as android_setup

from multipak import android
from multipak import android_instances as instances
from multipak import android_instances_helper as helper
from multipak.models import Transaction

setup = android_setup
VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"


def context_module():
    name = "multipak_instance_test"
    spec = importlib.util.spec_from_file_location(name, VENDOR / "multipak_instance.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_two_instance_contexts_have_disjoint_resources():
    module = context_module()
    first = module.Instance("androida", 1000, "Android A", "20.0", 1, "/home/example")
    second = module.Instance("androidb", 1000, "Android B", "18.1", 2, "/home/example")
    for name in (
        "work",
        "user_state",
        "container_bus",
        "session_bus",
        "bridge",
        "subnet",
        "binderfs",
        "desktop_glob",
    ):
        assert getattr(first, name) != getattr(second, name), name
    for env in (first, second):
        assert len(env.bridge) <= 15
        assert str(env.work).startswith("/var/lib/multipak-waydroid/instances/")
        assert shlex.split(env.command("app", "launch", "org.example.demo")) == [
            instances.CLI,
            "--instance",
            env.id,
            "app",
            "launch",
            "org.example.demo",
        ]
        assert env.desktop_name("org.example.demo").startswith("multipak-waydroid." + env.id + ".")


@pytest.mark.parametrize("value", ["", "../waydroid", "a-b", "a;touch", "a" * 12, "0android"])
def test_instance_ids_cannot_escape_paths(value):
    assert not instances.valid_id(value)
    with pytest.raises(ValueError):
        context_module().Instance(value, 1000, "Example", "20.0", 1, "/home/user")


def test_profiles_filter_both_system_and_vendor_to_the_selected_android():
    module = context_module()
    module._current = module.Instance("androida", 1000, "Android A", "18.1", 1, "/home/u")
    records = [
        {"version": "20.0", "datetime": 100},
        {"version": "18.1", "datetime": 10},
        {"version": "18.1", "datetime": 20},
    ]
    assert [r["datetime"] for r in module.select_release(records)] == [20, 10]
    with pytest.raises(RuntimeError):
        module.select_release(records[:1])


def test_named_command_and_privileged_shell_never_use_system_waydroid(monkeypatch):
    from multipak import privileged

    monkeypatch.setattr(privileged, "ensure_installed", lambda _: None)
    calls = []
    process = SimpleNamespace(run=lambda args, **kw: calls.append(args) or "")
    monkeypatch.setattr(android.host, "which", lambda _: "/usr/bin/pkexec")
    waydroid = instances.InstanceWaydroid(process, "androida")
    waydroid.command("prop", "get", "ro.build.version.sdk")
    waydroid.privileged(android.WAYDROID, "shell", "--", "pm", "list", "packages")
    assert calls[0] == [
        instances.CLI,
        "--instance",
        "androida",
        "prop",
        "get",
        "ro.build.version.sdk",
    ]
    assert calls[1][:4] == ["pkexec", privileged.HELPER, "waydroid", "androida"]
    assert "/waydroid/data/" not in str(waydroid.staging_directory())
    assert str(waydroid.staging_directory()) == str(instances.BASE / "androida/exchange/apks")


def test_old_records_keep_the_original_waydroid_routing():
    engine = SimpleNamespace(runner=object())
    tx = Transaction("a" * 32, "Waydroid", "", "Demo", "1", "apk", "", method="waydroid")
    assert type(android.transaction_waydroid(engine, tx)) is android.Waydroid
    tx.android_instance_id = "androida"
    assert android.transaction_waydroid(engine, tx).instance_id == "androida"


def test_create_allocates_unique_subnets_and_preserves_existing_environments(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "BASE", tmp_path / "managed")
    real_path = Path
    monkeypatch.setattr(
        helper,
        "Path",
        lambda p: (
            tmp_path / "etc" / str(p).removeprefix("/etc/")
            if str(p).startswith("/etc/")
            else real_path(p)
        ),
    )
    calls = []

    def layout(directory, owner):
        target = directory / "private/data"
        target.parent.mkdir()
        return target

    monkeypatch.setattr(helper, "prepare_layout", layout)
    monkeypatch.setattr(helper, "label_layout", lambda _: None)

    def metadata(ident, owner):
        path = helper.BASE / "instances" / ident / "instance.json"
        info = json.loads(path.read_text())
        if info["owner"] != owner.pw_uid:
            raise PermissionError("wrong owner")
        return info

    monkeypatch.setattr(helper, "metadata", metadata)

    def run(args):
        calls.append(args)
        return json.dumps([{"dst": "10.203.1.0/24"}]) if "-j" in args else ""

    monkeypatch.setattr(helper, "run", run)
    owner = SimpleNamespace(pw_uid=1000, pw_dir=str(tmp_path / "home"))
    first = helper.allocate(owner, "androida", "Android A", "android-13")
    second = helper.allocate(owner, "androidb", "Android B", "android-11")
    assert (first["network_slot"], second["network_slot"]) == (2, 3)
    assert first["release"] == "20.0" and second["release"] == "18.1"
    original = (helper.BASE / "instances/androida/instance.json").read_bytes()
    assert helper.allocate(owner, "androida", "Android A", "android-13") == first
    assert (helper.BASE / "instances/androida/instance.json").read_bytes() == original
    with pytest.raises(PermissionError):
        helper.metadata("androida", SimpleNamespace(pw_uid=2000))
    assert all("/usr/share" not in str(a) for a in calls)


def test_runtime_archive_matches_helper_hash():
    source = Path(__file__).resolve().parents[1] / "src/multipak/assets/waydroid-runtime.tar.gz"
    import hashlib

    assert hashlib.sha256(source.read_bytes()).hexdigest() == helper.RUNTIME_SHA256


def test_runtime_desktop_cleanup_is_scoped_and_original_paths_are_not_active():
    source = (VENDOR / "tools/services/user_manager.py").read_text()
    assert 'glob("waydroid.*.desktop")' not in source
    assert "instance().desktop_glob" in source
    assert 'instance().command("app", "launch", packageName)' in source
    assert "main_app_path.unlink" not in source
    drivers = (VENDOR / "tools/helpers/drivers.py").read_text()
    private = drivers[
        drivers.index("def probeBinderDriver") : drivers.index("def probeAshmemDriver")
    ]
    assert '"/dev/binderfs"' not in private and '"ln"' not in private
    script = (VENDOR / "data/scripts/waydroid-net.sh").read_text()
    assert "table inet lxc" not in script and "192.168.240" not in script
    assert "${table}" in script and "10.203.${slot}" in script


def test_same_app_in_two_environments_and_removing_one_preserves_the_other(setup, monkeypatch):
    engine, apk = setup
    process = engine.runner
    states = {"androida": FakeRunner(), "androidb": FakeRunner()}
    calls = []

    def run(args, **kw):
        calls.append(args)
        from multipak.privileged import HELPER

        if args[:3] == ["pkexec", HELPER, "waydroid"]:
            selected = states[args[3]]
            return selected.run(["pkexec", android.WAYDROID, *args[4:]], **kw)
        logical = args[1:] if args[0] == "pkexec" else args
        if logical[0] == instances.CLI:
            assert logical[1] == "--instance"
            selected = states[logical[2]]
            mapped = [android.WAYDROID, *logical[3:]]
            if args[0] == "pkexec":
                mapped = ["pkexec", *mapped]
            return selected.run(mapped, **kw)
        return original_run(args, **kw)

    original_run = process.run
    process.run = run
    monkeypatch.setattr(instances.InstanceWaydroid, "ready", lambda *a, **kw: True)
    monkeypatch.setattr(instances, "discover", lambda *_: [{"id": "androida"}, {"id": "androidb"}])
    package = inspect_apk(apk)
    first = engine.install_android(
        package, instance_id="androida", android_profile="android-13", android_label="A"
    )
    second = engine.install_android(
        package, instance_id="androidb", android_profile="android-13", android_label="B"
    )
    for tx in (first, second):
        assert tx.status == "installed"
        text = engine.store.launcher(tx, tx.launchers[0]).read_text()
        assert "--instance" in text and tx.android_instance_id in text
    assert states["androida"].installed == states["androidb"].installed == {"org.example.demo"}
    monkeypatch.setattr(android, "Runner", lambda *_: process)
    engine.cleanup(first)
    assert not states["androida"].installed and states["androidb"].installed == {"org.example.demo"}
    assert [t.id for t in engine.store.transactions()] == [second.id]
    assert not any(args[0] == android.WAYDROID for args in calls)


def test_runtime_archive_contains_the_current_source_and_license():
    import tarfile

    archive = VENDOR.parents[1] / "src/multipak/assets/waydroid-runtime.tar.gz"
    with tarfile.open(archive) as payload:
        files = {m.name: payload.extractfile(m).read() for m in payload if m.isfile()}
    assert "LICENSE" in files and "MULTIPAK-NOTICE" in files
    for path in VENDOR.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            assert files[str(path.relative_to(VENDOR))] == path.read_bytes(), path


def test_deployment_is_private_and_the_runtime_is_readable_by_the_user(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "BASE", tmp_path / "var/lib/multipak-waydroid")
    monkeypatch.setattr(helper, "CLI", tmp_path / "usr/local/bin/multipak-waydroid")
    real_path = Path
    monkeypatch.setattr(
        helper,
        "Path",
        lambda p: (
            tmp_path / "etc" / str(p).removeprefix("/etc/")
            if str(p).startswith("/etc/")
            else real_path(p)
        ),
    )
    calls = []
    monkeypatch.setattr(helper, "run", lambda args: calls.append(args))
    helper.deploy(VENDOR.parents[1] / "src/multipak/assets/waydroid-runtime.tar.gz")
    runtime = helper.CLI.resolve().parent
    assert runtime.stat().st_mode & 0o777 == 0o755
    assert helper.CLI.resolve().stat().st_mode & 0o777 == 0o755
    units = list((tmp_path / "etc/systemd/system").glob("*.service"))
    assert [p.name for p in units] == ["multipak-waydroid@.service"]
    assert "id.waydro.MultiPak.Container.%i" in units[0].read_text()
    assert calls == [["/usr/bin/systemctl", "daemon-reload"]]


def test_scoped_removal_refuses_symlinked_parents(tmp_path):
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "precious").mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(OSError):
        helper.scoped_rmtree(alias / "precious")
    assert (protected / "precious").is_dir()


def test_cancelled_or_partial_setup_is_not_reported_as_initialized(tmp_path):
    module = context_module()
    module.BASE = tmp_path
    instance = module.Instance("androida", 1000, "Android A", "20.0", 1, "/home/example")
    instance.work.mkdir()
    (instance.work / "rootfs").mkdir()
    (instance.work / "waydroid.cfg").write_text("[properties]\n")
    assert not instance.initialized
    for name in (
        "waydroid_base.prop",
        "images/system.img",
        "images/vendor.img",
        "lxc/androida/config",
        "lxc/androida/config_nodes",
    ):
        path = instance.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    assert instance.initialized


def test_mount_guard_handles_spaces_and_path_boundaries():
    root = Path("/var/home/user name/android")
    mountinfo = r"1 0 0:1 / /var/home/user\040name/android/data rw - tmpfs tmpfs rw"
    assert helper.mounted_below(root, mountinfo)
    assert not helper.mounted_below(Path("/var/home/user name/and"), mountinfo)
    with pytest.raises(RuntimeError):
        helper.mounted_below(root, "invalid")


@pytest.mark.parametrize("mounted", [True, False])
def test_personal_discovery_only_reads_existing_environment(monkeypatch, mounted):
    monkeypatch.setattr(instances.host, "which", lambda _: "/usr/bin/waydroid")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "cat":
            if not mounted:
                raise RuntimeError("rootfs not mounted")
            return "ro.build.version.sdk=33\nro.build.version.release=13\n"
        return ""

    entry = instances.discover_system(SimpleNamespace(run=run))
    assert entry["personal"] and entry["id"] == "system-waydroid"
    assert entry["sdk"] == (33 if mounted else 0)
    assert calls == [
        ["test", "-s", android.CONFIG],
        ["test", "-d", android.ROOTFS],
        ["cat", android.ROOTFS + "/system/build.prop"],
    ]


def test_personal_discovery_does_not_initialize_missing_environment(monkeypatch):
    monkeypatch.setattr(instances.host, "which", lambda _: "/usr/bin/waydroid")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        raise RuntimeError("not initialized")

    assert instances.discover_system(SimpleNamespace(run=run)) is None
    assert calls == [["test", "-s", android.CONFIG]]


@pytest.mark.parametrize(
    "output,expected,can_stop",
    [
        ("Session:\tRUNNING\nContainer:\tRUNNING\n", "running", True),
        ("Session:\tRUNNING\nContainer:\tFROZEN\n", "frozen", True),
        ("Session:\tSTOPPED\nVendor type:\tMAINLINE\n", "stopped", False),
        ("Session:\tRUNNING\nContainer:\tSTOPPED\n", "stopped", False),
        ("unexpected response", "unavailable", False),
    ],
)
def test_owned_status_is_read_without_starting_android(monkeypatch, output, expected, can_stop):
    monkeypatch.setattr(
        instances,
        "discover",
        lambda _: [
            {"id": "androida", "label": "Games", "initialized": True},
            {"id": "androidb", "label": "Pending", "initialized": False},
        ],
    )
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 5
        return output

    rows = instances.discover_with_status(SimpleNamespace(run=run))
    assert rows[0]["state"] == expected and rows[0]["can_stop"] is can_stop
    assert rows[1]["state"] == "uninitialized"
    assert calls == [[instances.CLI, "--instance", "androida", "status"]]


def test_failed_status_query_keeps_container_visible_and_does_not_claim_stopped(monkeypatch):
    monkeypatch.setattr(
        instances, "discover", lambda _: [{"id": "androida", "label": "Games", "initialized": True}]
    )

    def run(*args, **kwargs):
        raise TimeoutError("status timeout")

    row = instances.discover_with_status(SimpleNamespace(run=run))[0]
    assert row["id"] == "androida" and row["state"] == "unavailable"
    assert row["state_explanation"] == "status timeout"
