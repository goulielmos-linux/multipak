"""Regression tests for untrusted packages, privileged system installation and
privileged Android boundaries."""

import configparser
import hashlib
import importlib.util
import json
import os
import pwd
import socket
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from multipak import android_data_helper as data
from multipak import privileged, privileged_files, system_install_helper
from multipak.desktop import parse_desktop, render_desktop
from multipak.metadata import inspect_package
from multipak.process import Runner


@pytest.mark.parametrize("key", ["Exec", "Name", "Icon", "Comment", "Actions"])
def test_desktop_continuations_are_rejected_in_every_value(key):
    content = f"[Desktop Entry]\nType=Application\nExec=demo\n{key}=text\n\tExec=/host/bypass\n"
    with pytest.raises((ValueError, configparser.Error)):
        render_desktop(content, "guest", "/usr/bin/distrobox")


def test_desktop_duplicate_exec_and_defaults_are_rejected():
    for content in (
        "[Desktop Entry]\nExec=demo\nExec=/host/bypass\n",
        "[DEFAULT]\nExec=/host/bypass\n[Desktop Entry]\nType=Application\n",
    ):
        with pytest.raises((ValueError, configparser.Error)):
            parse_desktop(content)


@pytest.mark.parametrize(
    "record",
    [
        b"foo\nbash\x1f1-1\x1fnoarch",
        b"foo\x1f1-1\x1fnoarch\n",
        b"foo\x1f1-1\x1fnoarch\x1fextra",
        b"foo\x1f\x1fnoarch",
        b"foo\x00bar\x1f1-1\x1fnoarch",
        b"foo\x1f1-1\x1fnoarch\r",
    ],
)
def test_rpm_identity_requires_exactly_one_complete_record(tmp_path, record):
    path = tmp_path / "malformed.rpm"
    path.write_bytes(b"\xed\xab\xee\xdb\x03\0\0\0")

    class Query:
        def run(self, argv, **options):
            return record if options.get("binary") else ""

    with pytest.raises(ValueError, match="metadata"):
        inspect_package(path, Query())


def test_binary_record_retains_stdout_without_signature_warnings():
    runner = Runner(threading.Event(), lambda _: None)
    result = runner.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('warning: unsigned package\\n'); "
            "sys.stdout.buffer.write(b'foo\\x1f1-1\\x1fnoarch')",
        ],
        binary=True,
        output_limit=1024,
    )
    assert result == b"foo\x1f1-1\x1fnoarch"


def test_installed_helper_is_the_only_operational_entry_point(monkeypatch):
    calls = []
    digest = (
        Path(privileged.__file__)
        .with_name("assets")
        .joinpath("privileged.sha256")
        .read_text()
        .strip()
    )

    def run(argv, **kwargs):
        calls.append(argv)
        if argv == ["cat", privileged.MANIFEST]:
            return json.dumps({"bundle_sha256": digest})
        return "done"

    assert (
        privileged.call(SimpleNamespace(run=run), "system-install", "/tmp/reviewed.rpm", "a" * 64)
        == "done"
    )
    assert calls[-1] == [
        "pkexec",
        privileged.HELPER,
        "system-install",
        "/tmp/reviewed.rpm",
        "a" * 64,
    ]
    assert not any("python3" in arg for command in calls for arg in command)


def test_user_input_rejects_symlinks_and_nonregular_files(tmp_path):
    owner = pwd.getpwuid(os.getuid())
    source = tmp_path / "symlink"
    source.symlink_to("/etc/passwd")
    with pytest.raises(OSError):
        privileged_files.open_user_file(owner, source)
    with pytest.raises(ValueError):
        privileged_files.open_user_file(owner, tmp_path)


def test_system_snapshot_has_verified_bytes_and_a_private_parent(tmp_path, monkeypatch):
    from tempfile import TemporaryDirectory

    monkeypatch.setattr(
        system_install_helper.tempfile,
        "TemporaryDirectory",
        lambda **kwargs: TemporaryDirectory(prefix="snapshot-", dir=tmp_path),
    )
    owner = pwd.getpwuid(os.getuid())
    source = tmp_path / "source.rpm"
    source.write_bytes(b"reviewed bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    calls = []

    def execute(argv, **options):
        target = Path(argv[-1])
        source.write_bytes(b"changed while authorization was open")
        assert target.read_bytes() == b"reviewed bytes"
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.stat().st_mode) == 0o400
        assert options["env"] == privileged_files.CLEAN_ENV
        calls.append(argv)

    system_install_helper.install(owner, source, digest, execute=execute)
    assert len(calls) == 1 and not Path(calls[0][-1]).exists()
    with pytest.raises(ValueError, match="changed"):
        system_install_helper.install(owner, source, digest, execute=execute)
    assert len(calls) == 1


def test_privileged_bundle_contains_exact_reviewed_sources():
    import io
    import tarfile

    from multipak import bootstrap_helpers

    root = Path(privileged.__file__).parent
    payload = (root / "assets/privileged.tar.gz").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == bootstrap_helpers.BUNDLE_SHA256
    assert (
        bootstrap_helpers.BUNDLE_SHA256 == (root / "assets/privileged.sha256").read_text().strip()
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        files = {entry.name: archive.extractfile(entry).read() for entry in archive.getmembers()}
    for name, content in files.items():
        if name == "multipak.policy":
            continue
        assert content == (root / name).read_bytes(), name


def test_cancellation_watch_pins_the_original_file(tmp_path):
    owner = pwd.getpwuid(os.getuid())
    path = tmp_path / "watch"
    path.touch()
    with privileged_files.cancellation_watch(owner, path) as cancelled:
        path.unlink()
        path.symlink_to("/etc/shadow")
        assert cancelled()


def test_copy_preserves_hardlinks_symlinks_attributes_and_originals(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir(mode=0o750)
    target.mkdir()
    (source / "saved-game").write_bytes(b"personal data")
    (source / "nested").mkdir()
    os.link(source / "saved-game", source / "nested/linked-game")
    (source / "host-link").symlink_to("/etc/shadow")
    os.setxattr(source / "saved-game", "user.multipak-test", b"attribute")
    incoming = privileged_files.open_directory(source)
    outgoing = privileged_files.open_directory(target)
    try:
        data._copy_tree(incoming, outgoing)
        assert data._fingerprint(incoming) == data._fingerprint(outgoing)
    finally:
        os.close(incoming)
        os.close(outgoing)
    assert (target / "saved-game").stat().st_ino == (target / "nested/linked-game").stat().st_ino
    assert (target / "host-link").readlink() == Path("/etc/shadow")
    assert os.getxattr(target / "saved-game", "user.multipak-test") == b"attribute"
    assert stat.S_IMODE(target.stat().st_mode) == 0o750
    assert (source / "saved-game").read_bytes() == b"personal data"


def test_copy_rejects_special_files_and_directory_symlinks(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    os.mkfifo(source / "unexpected")
    incoming, outgoing = (
        privileged_files.open_directory(source),
        privileged_files.open_directory(target),
    )
    try:
        with pytest.raises(ValueError, match="special file"):
            data._copy_tree(incoming, outgoing)
    finally:
        os.close(incoming)
        os.close(outgoing)
    alias = tmp_path / "alias"
    alias.symlink_to(source)
    with pytest.raises(OSError):
        privileged_files.open_directory(alias)


@pytest.fixture
def session_module(monkeypatch, tmp_path):
    owner = os.getuid()
    current = SimpleNamespace(
        owner=owner,
        data_layout=2,
        work=tmp_path / "instance",
        data=tmp_path / "instance/private/data",
        exchange=tmp_path / "instance/exchange",
        sockets=tmp_path / "runtime/sockets",
        user_state=tmp_path / "prefs",
    )
    monkeypatch.setitem(sys.modules, "multipak_instance", SimpleNamespace(current=lambda: current))
    source = Path(__file__).resolve().parents[1] / "vendor/waydroid/multipak_session.py"
    spec = importlib.util.spec_from_file_location("multipak_session_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, current


@pytest.mark.parametrize(
    "display", ["../../etc/shadow", "/etc/shadow", "wayland-0\n", "../socket", "wayland/0"]
)
def test_session_rejects_traversal_before_touching_host_paths(session_module, display):
    module, current = session_module
    with pytest.raises(ValueError, match="Wayland"):
        module.prepare(
            {"user_id": str(current.owner), "pid": "42", "wayland_display": display},
            current.owner,
            42,
        )
    assert not current.sockets.exists()


def test_session_rejects_forged_identity_and_unmigrated_data(session_module):
    module, current = session_module
    request = {"user_id": str(current.owner), "pid": "42", "wayland_display": "wayland-0"}
    with pytest.raises(PermissionError):
        module.prepare(request, current.owner + 1, 42)
    with pytest.raises(ValueError, match="process"):
        module.prepare(request, current.owner, 43)
    current.data_layout = 1
    with pytest.raises(RuntimeError, match="migrate"):
        module.prepare(request, current.owner, 42)


def test_socket_symlink_is_rejected_before_any_mount(session_module, tmp_path, monkeypatch):
    module, current = session_module
    (tmp_path / "fake-display").symlink_to("/etc/shadow")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("must not mount"))
    with pytest.raises(PermissionError):
        module._pin(tmp_path, ("fake-display",), current.sockets / "wayland", current.owner)


def test_socket_mount_uses_pinned_descriptor_not_a_user_path(session_module, tmp_path, monkeypatch):
    module, current = session_module
    current.sockets.mkdir(parents=True)
    calls = []
    endpoint = tmp_path / "wayland-0"
    with socket.socket(socket.AF_UNIX) as connection:
        try:
            connection.bind(str(endpoint))
        except PermissionError:
            pytest.skip(
                "Unix sockets are unavailable in this sandbox; also run this test on the host"
            )

        def mount(argv, **options):
            fd = options["pass_fds"][0]
            assert stat.S_ISSOCK(os.fstat(fd).st_mode)
            target = Path(argv[-1])
            target.unlink()
            os.link(endpoint, target)
            endpoint.unlink()
            endpoint.symlink_to("/etc/shadow")
            assert stat.S_ISSOCK(os.fstat(fd).st_mode)
            assert argv[1:3] == ["--no-canonicalize", "--bind"]
            assert argv[3] == f"/proc/self/fd/{fd}"
            calls.append(argv)

        monkeypatch.setattr(module.subprocess, "run", mount)
        module._pin(tmp_path, ("wayland-0",), current.sockets / "wayland", current.owner)
    assert len(calls) == 1


def test_notification_uses_a_pid_descriptor_and_checks_all_process_uids(
    session_module, monkeypatch
):
    module, current = session_module
    sent = []
    monkeypatch.setattr(
        module.signal, "pidfd_send_signal", lambda descriptor, sig: sent.append(descriptor)
    )
    module.notify_stopped({"pid": str(os.getpid())})
    assert len(sent) == 1
    current.owner += 1
    with pytest.raises(PermissionError):
        module.notify_stopped({"pid": str(os.getpid())})
    assert len(sent) == 1


@pytest.fixture
def migration_layout(tmp_path, monkeypatch):
    directory = tmp_path / "instance"
    directory.mkdir()
    info = {"id": "androida", "home": str(tmp_path / "home"), "owner": os.getuid()}
    old = Path(info["home"]) / ".local/share/multipak/android-instances/androida/data"
    old.mkdir(parents=True)
    (old / "progress").write_bytes(b"saved game")

    def layout(root, owner):
        (root / "private").mkdir(exist_ok=True)
        (root / "exchange/icons").mkdir(parents=True, exist_ok=True)
        return root / "private/data"

    monkeypatch.setattr(data, "prepare_layout", layout)
    return directory, info, old, pwd.getpwuid(os.getuid())


def test_migration_is_resumable_and_retains_the_original(migration_layout):
    directory, info, old, owner = migration_layout
    target = directory / "private/data"
    migrated = data.migrate(directory, info, owner, is_mounted=lambda _: False)
    assert migrated["data_layout"] == 2
    assert (target / "progress").read_bytes() == (old / "progress").read_bytes() == b"saved game"
    assert (target / "waydroid_tmp").is_dir() and (target / "icons").is_dir()
    assert data.migrate(directory, info, owner, is_mounted=lambda _: False) == migrated
    assert data.migrate(directory, migrated, owner, is_mounted=lambda _: False) == migrated


@pytest.mark.parametrize("schema", [1, 2])
def test_migration_never_touches_data_while_a_mount_remains(migration_layout, monkeypatch, schema):
    directory, info, old, owner = migration_layout
    monkeypatch.setattr(data, "prepare_layout", lambda *_: pytest.fail("mounted data was touched"))
    with pytest.raises(RuntimeError, match="mounts"):
        data.migrate(directory, {**info, "data_layout": schema}, owner, is_mounted=lambda _: True)
    assert (old / "progress").read_bytes() == b"saved game"


def test_migration_detects_data_changed_during_copy(migration_layout, monkeypatch):
    directory, info, old, owner = migration_layout
    copy = data._copy_tree

    def changed(*args, **kwargs):
        copy(*args, **kwargs)
        (old / "progress").write_bytes(b"changed by another process")

    monkeypatch.setattr(data, "_copy_tree", changed)
    with pytest.raises(ValueError, match="changed"):
        data.migrate(directory, info, owner, is_mounted=lambda _: False)
    assert (old / "progress").is_file()
    assert not (directory / "private/data").exists()
    assert not list((directory / "private").glob(".migration-*"))


def test_migration_refuses_a_symlink_in_the_source_path(migration_layout):
    directory, info, old, owner = migration_layout
    real = old.with_name("preserved")
    old.rename(real)
    old.symlink_to(real)
    with pytest.raises(OSError):
        data.migrate(directory, info, owner, is_mounted=lambda _: False)
    assert (real / "progress").read_bytes() == b"saved game"
    assert not (directory / "private/data").exists()


def test_session_ignores_caller_mount_paths_and_derives_them_from_identity(
    session_module, monkeypatch, tmp_path
):
    module, current = session_module

    class StagingPath(type(tmp_path)):
        def stat(self, *, follow_symlinks=True):
            original = super().stat(follow_symlinks=follow_symlinks)
            fields = list(original)
            fields[4] = (
                0  # Model the root-created staging directory for this unprivileged unit test.
            )
            return os.stat_result(fields)

    current.sockets = StagingPath(current.sockets)
    current.sockets.parent.mkdir()
    pins = []
    monkeypatch.setattr(module, "validate_layout", lambda: None)
    monkeypatch.setattr(module, "_protected", lambda _: None)
    monkeypatch.setattr(
        module, "_directory", lambda _: os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    )
    monkeypatch.setattr(module, "_pin", lambda *args: pins.append(args))
    request = dict(
        user_id=str(current.owner),
        pid="42",
        wayland_display="wayland-1",
        xdg_runtime_dir="/etc",
        waydroid_data="/etc",
        pulse_runtime_path="/root",
        host_user="/root",
        group_id="0",
        user_name="root",
    )
    trusted = module.prepare(request, current.owner, 42)
    assert trusted["waydroid_data"] == str(current.data)
    assert trusted["xdg_runtime_dir"] == f"/run/user/{current.owner}"
    assert trusted["user_name"] == pwd.getpwuid(current.owner).pw_name
    assert trusted["group_id"] == str(pwd.getpwuid(current.owner).pw_gid)
    assert pins[0][0] == Path(f"/run/user/{current.owner}")
    assert pins[0][1] == ("wayland-1",)
    assert all("/etc" not in str(args) and "/root" not in str(args) for args in pins)


@pytest.mark.parametrize("already_labeled", [True, False])
def test_selinux_layout_uses_existing_waydroid_policy_without_disabling_it(
    tmp_path, monkeypatch, already_labeled
):
    from multipak import android_instances_helper as helper

    pattern = r"/var/lib/multipak-waydroid/instances/[^/]+/(private|exchange)(/.*)?"
    monkeypatch.setitem(
        sys.modules,
        "selinux",
        SimpleNamespace(
            is_selinux_enabled=lambda: 1,
            matchpathcon=lambda *a: (0, "system_u:object_r:waydroid_data_t:s0"),
        ),
    )
    monkeypatch.setattr(helper.shutil, "which", lambda name, **kw: "/usr/bin/" + name)
    calls = []

    def run(argv):
        calls.append(argv)
        return (
            pattern + " all files system_u:object_r:waydroid_data_t:s0" if already_labeled else ""
        )

    monkeypatch.setattr(helper, "run", run)
    helper.label_layout(tmp_path)
    assert calls[0] == ["/usr/bin/semanage", "fcontext", "-l", "-C"]
    if not already_labeled:
        assert calls[1] == ["/usr/bin/semanage", "fcontext", "-a", "-t", "waydroid_data_t", pattern]
    assert calls[-1] == [
        "/usr/bin/restorecon",
        "-R",
        str(tmp_path / "private"),
        str(tmp_path / "exchange"),
    ]
    assert not any("setenforce" in str(command) or "semodule" in str(command) for command in calls)


@pytest.mark.parametrize("cancelled", [True, False])
def test_flatpak_exchange_uses_regular_user_operations_and_cleans_up(
    tmp_path, monkeypatch, cancelled
):
    from multipak import android_staging as staging

    monkeypatch.setattr(staging.host, "is_flatpak", lambda: True)
    monkeypatch.setattr(staging.host, "temporary_directory", lambda: tmp_path)
    shared = Path("/var/lib/multipak-waydroid/instances/androida/exchange/apks")
    target = shared / "multipak-Ab1234567890"
    calls = []
    cleanup = []

    def run(argv, **options):
        calls.append(argv)
        assert "pkexec" not in argv
        if argv[0] == "mktemp":
            return str(target) + "\n"
        if argv[0] == "cp":
            assert Path(argv[-2]).read_bytes() == b"verified APK"
            assert argv[-1] == str(target / "000.apk")
        return ""

    monkeypatch.setattr(
        staging.host,
        "run",
        lambda argv, **kw: cleanup.append(argv) or SimpleNamespace(returncode=0),
    )
    waydroid = SimpleNamespace(
        instance_id="androida", runner=SimpleNamespace(run=run), staging_directory=lambda: shared
    )

    def exercise():
        with staging.directory(waydroid, "multipak-") as local:
            assert local.name == target.name and local.is_relative_to(tmp_path)
            (local / "000.apk").write_bytes(b"verified APK")
            staging.publish(waydroid, local)
            if cancelled:
                raise RuntimeError("cancelled")

    if cancelled:
        with pytest.raises(RuntimeError, match="cancelled"):
            exercise()
    else:
        exercise()
    assert calls[0][0] == "mktemp" and calls[1] == ["chmod", "0755", "--", str(target)]
    assert cleanup == [["rm", "-rf", "--", str(target)]]
    assert not list(tmp_path.iterdir())


def test_lxc_mount_configuration_contains_only_fixed_sources_and_real_tmpfs(tmp_path, monkeypatch):
    import ast

    source = Path(__file__).resolve().parents[1] / "vendor/waydroid/tools/helpers/lxc.py"
    tree = ast.parse(source.read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("add_node_entry", "generate_session_lxc_config")
    ]
    current = SimpleNamespace(
        id="androida",
        owner=1000,
        data_layout=2,
        data=tmp_path / "instance/private/data",
        exchange=tmp_path / "instance/exchange",
        sockets=tmp_path / "sockets",
    )
    destination = tmp_path / "lxc/androida"
    destination.mkdir(parents=True)
    checked = []
    monkeypatch.setitem(
        sys.modules,
        "multipak_session",
        SimpleNamespace(validate_layout=lambda **kw: checked.append(kw)),
    )
    namespace = {
        "os": os,
        "Path": Path,
        "instance": lambda: current,
        "tools": SimpleNamespace(
            config=SimpleNamespace(
                defaults={"lxc": str(tmp_path / "lxc"), "container_xdg_runtime_dir": "/run/xdg"}
            )
        ),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    request = dict(
        user_id="1000",
        waydroid_data=str(current.data),
        xdg_runtime_dir="/etc",
        pulse_runtime_path="/root",
        wayland_display="../../shadow",
    )
    namespace["generate_session_lxc_config"](SimpleNamespace(), request)
    rendered = (destination / "config_session").read_text()
    assert checked == [{"sockets": True}]
    assert "tmpfs run/xdg tmpfs create=dir 0 0" in rendered
    assert str(current.data) + " data none rbind 0 0" in rendered
    assert str(current.exchange / "apks") + " data/waydroid_tmp none" in rendered
    assert "/etc" not in rendered and "/root" not in rendered and "../" not in rendered
    with pytest.raises(PermissionError):
        namespace["generate_session_lxc_config"](
            SimpleNamespace(), {**request, "waydroid_data": "/etc"}
        )
    assert (destination / "config_session").read_text() == rendered


def test_migration_space_is_checked_before_any_service_is_stopped(tmp_path, monkeypatch):
    from multipak import android_instances_helper as helper

    directory = tmp_path / "instances/androida"
    directory.mkdir(parents=True)
    old = tmp_path / "home/.local/share/multipak/android-instances/androida/data"
    old.mkdir(parents=True)
    info = {"id": "androida", "owner": os.getuid(), "home": str(tmp_path / "home")}
    (directory / "instance.json").write_text(json.dumps(info))
    monkeypatch.setattr(helper, "BASE", tmp_path)
    monkeypatch.setattr(helper, "metadata", lambda *_: info)
    monkeypatch.setattr(helper.shutil, "disk_usage", lambda _: SimpleNamespace(free=100))
    calls = []
    monkeypatch.setattr(helper, "run", lambda argv: calls.append(argv) or "1000000 original-data")
    with pytest.raises(RuntimeError, match="space"):
        helper.check_migration_space(pwd.getpwuid(os.getuid()))
    assert len(calls) == 1 and calls[0][0] == "/usr/bin/du"
    assert not any("systemctl" in str(command) for command in calls)


def test_fingerprint_reads_all_entries_even_with_a_reused_directory_cursor(tmp_path):
    (tmp_path / "progress").write_bytes(b"personal data")
    descriptor = privileged_files.open_directory(tmp_path)
    try:
        expected = data._fingerprint(descriptor)
        os.lseek(descriptor, 2**63 - 1, os.SEEK_SET)
        assert data._fingerprint(descriptor) == expected
    finally:
        os.close(descriptor)


def test_copy_restarts_a_previously_used_directory_cursor(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "progress").write_bytes(b"personal data")
    incoming = privileged_files.open_directory(source)
    outgoing = privileged_files.open_directory(target)
    try:
        os.lseek(incoming, 2**63 - 1, os.SEEK_SET)
        data._copy_tree(incoming, outgoing)
        assert data._fingerprint(incoming) == data._fingerprint(outgoing)
    finally:
        os.close(incoming)
        os.close(outgoing)
    assert (target / "progress").read_bytes() == b"personal data"
