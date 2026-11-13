"""Exercise the native Waydroid modules with temporary files and fake Android services."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
VENDOR = PROJECT / "vendor/waydroid"


def child(identifier, root):
    sys.path.insert(0, str(VENDOR))
    import multipak_instance as context

    context.BASE = root / "instances"
    instance = context.Instance(
        identifier,
        os.getuid(),
        identifier,
        "20.0",
        1 if identifier == "androida" else 2,
        str(root / "home"),
    )
    context._current = instance
    import tools.config
    import tools.helpers.lxc as lxc
    import tools.services.user_manager as manager

    assert tools.config.defaults["work"] == str(instance.work)
    assert tools.config.session_defaults["waydroid_data"] == str(instance.user_state / "data")
    instance.work.mkdir(parents=True)
    args = SimpleNamespace(work=str(instance.work))

    def execute(_args, argv, **kwargs):
        assert argv[0] in ("mkdir", "sh", "sed", "cp", "mv"), argv
        result = subprocess.run(argv, check=True, capture_output=True, text=True)
        return result.stdout

    with (
        patch.object(lxc, "get_lxc_version", return_value=6),
        patch.object(lxc, "get_apparmor_status", return_value=False),
        patch.object(lxc, "generate_nodes_lxc_config", return_value=[]),
        patch("tools.helpers.run.user", execute),
    ):
        lxc.set_lxc_config(args)
    generated = (instance.work / "lxc" / identifier / "config").read_text()
    assert "lxc.apparmor.profile" not in generated
    assert "/var/lib/waydroid" not in generated
    assert str(instance.work / "rootfs") in generated and instance.bridge in generated
    import tools.actions.container_manager as container

    connection = SimpleNamespace(get_object=lambda *args: None)
    caller = SimpleNamespace(GetConnectionUnixUser=lambda _: os.getuid())
    service = SimpleNamespace(args=args)
    with (
        patch.object(container.dbus, "Interface", return_value=caller),
        patch.object(lxc, "status", return_value="RUNNING"),
        patch.object(
            container.subprocess,
            "run",
            return_value=SimpleNamespace(stdout="package:org.example.demo\n"),
        ) as query,
    ):
        assert container.DbusContainerManager.ListPackages(service, "sender", connection) == [
            "org.example.demo"
        ]
        command = query.call_args.args[0]
        assert command[command.index("-n") + 1] == identifier
        assert command[-6:] == ["/system/bin/cmd", "package", "list", "packages", "--user", "0"]
        caller.GetConnectionUnixUser = lambda _: os.getuid() + 1
        try:
            container.DbusContainerManager.ListPackages(service, "sender", connection)
        except PermissionError:
            pass
        else:
            raise AssertionError("Another user must not query this Android environment")
    apps = root / "applications"
    apps.mkdir(exist_ok=True)
    personal = apps / "waydroid.org.example.demo.desktop"
    personal.write_text("personal shortcut must remain unchanged")
    session = {
        "xdg_data_home": str(root),
        "waydroid_user_state": str(instance.user_state),
        "waydroid_data": str(instance.user_state / "data"),
    }
    platform = SimpleNamespace(
        getAppsInfo=lambda: [
            {
                "packageName": "org.example.demo",
                "name": "Demo",
                "categories": ["android.intent.category.LAUNCHER"],
            }
        ]
    )

    def monitor(_args, unlocked, changed):
        manager.stopping = True
        unlocked(os.getuid())

    with (
        patch.object(manager.IPlatform, "get_service", return_value=platform),
        patch(
            "tools.helpers.ipc.DBusContainerService",
            return_value=SimpleNamespace(ListPackages=lambda: ["org.example.demo"]),
        ),
        patch.object(manager.IUserMonitor, "add_service", side_effect=monitor),
        patch("tools.config.load", return_value={"waydroid": {"auto_adb": "False"}}),
    ):
        manager.start(args, session)
        args.user_manager.join(5)
        assert not args.user_manager.is_alive()
    assert personal.read_text() == "personal shortcut must remain unchanged"
    desktop = apps / instance.desktop_name("org.example.demo")
    text = desktop.read_text()
    assert "--instance " + identifier in text
    print(identifier, "native config and scoped desktop export verified")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        child(sys.argv[1], Path(sys.argv[2]))
    else:
        with tempfile.TemporaryDirectory(prefix="multipak-runtime-check-") as directory:
            for identifier in ("androida", "androidb"):
                subprocess.run([sys.executable, __file__, identifier, directory], check=True)
            files = list((Path(directory) / "applications").glob("multipak-waydroid.*.desktop"))
            assert len(files) == 2
        print("Two independent native contexts verified without host configuration changes.")
