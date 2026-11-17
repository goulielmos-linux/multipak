"""Snaps without snapd: metadata, the store, the bubblewrap view, installation."""

import dataclasses
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from multipak import snap, snap_meta, snap_store
from multipak.models import Transaction
from multipak.snap_sandbox import Entry, Mount, Request, build
from multipak.snap_yaml import YamlError, loads

FIREFOX = """name: firefox
version: 145.0.1-1
summary: Mozilla Firefox web browser
description: |-
  Firefox is a powerful, extensible web browser.

  Second paragraph: with a colon.
base: core22
apps:
  firefox:
    command: firefox.launcher
    command-chain:
    - snap/command-chain/desktop-launch
    desktop: meta/gui/firefox.desktop
    environment:
      GTK_USE_PORTAL: '1'
      LD_LIBRARY_PATH: ${SNAP_LIBRARY_PATH}:$SNAP/gnome-platform/usr/lib
    plugs:
    - desktop
    - home
  geckodriver:
    command: usr/lib/firefox/geckodriver
    plugs: [network, network-bind]
architectures:
- amd64
confinement: strict
environment:
  SNAP_DESKTOP_RUNTIME: $SNAP/gnome-platform
plugs:
  gnome-42-2204:
    interface: content
    target: $SNAP/gnome-platform
    default-provider: gnome-42-2204
  gtk-3-themes:
    interface: content
    target: $SNAP/data-dir/themes
    default-provider: gtk-common-themes:gtk-3-themes
layout:
  /usr/share/libdrm:
    bind: $SNAP/gnome-platform/usr/share/libdrm
  /usr/lib/x86_64-linux-gnu/webkit2gtk-4.0: {bind: $SNAP/gnome-platform/webkit}
"""


# Reading snap.yaml


def test_yaml_reader_matches_the_structure_snapcraft_writes():
    data = loads(FIREFOX)
    assert data["description"] == (
        "Firefox is a powerful, extensible web browser.\n\nSecond paragraph: with a colon."
    )
    assert data["apps"]["firefox"]["command-chain"] == ["snap/command-chain/desktop-launch"]
    assert data["apps"]["geckodriver"]["plugs"] == ["network", "network-bind"]
    assert data["apps"]["firefox"]["environment"]["GTK_USE_PORTAL"] == "1"
    assert data["layout"]["/usr/lib/x86_64-linux-gnu/webkit2gtk-4.0"] == {
        "bind": "$SNAP/gnome-platform/webkit"
    }


@pytest.mark.parametrize(
    "text",
    [
        "a: &x 1\nb: *x\n",  # Anchors and aliases are not part of snap.yaml.
        "a: 1\na: 2\n",
        "a:\n\t- tab\n",
        "a: 'unfinished\n",
        "a: !!python/object x\n",
    ],
)
def test_yaml_reader_rejects_what_it_does_not_understand(text):
    with pytest.raises(YamlError):
        loads(text)


def test_yaml_reader_agrees_with_pyyaml_on_generated_documents():
    yaml = pytest.importorskip("yaml")
    import random
    import string

    def normal(value):
        if value is None:
            return ""
        if isinstance(value, dict):
            return {str(k): normal(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normal(v) for v in value]
        return str(value)

    alphabet = string.ascii_letters + string.digits + " _-./$:{}'\"#,[]|>\\"

    def value(depth=0):
        choice = random.random()
        if depth > 3 or choice < 0.4:
            return "".join(random.choice(alphabet) for _ in range(random.randint(1, 20)))
        if choice < 0.7:
            return {value(9).strip() or "k": value(depth + 1) for _ in range(random.randint(1, 4))}
        return [value(depth + 1) for _ in range(random.randint(1, 4))]

    random.seed(7)
    checked = 0
    for index in range(1500):
        text = yaml.dump({f"k{index}": value()}, default_flow_style=False, width=60)
        try:
            ours = loads(text)
        except YamlError:
            continue  # Refusing is safe; reading differently is not.
        assert normal(ours) == normal(yaml.load(text, Loader=yaml.BaseLoader)), text
        checked += 1
    assert checked > 1200


def test_yaml_reader_reads_long_values_wrapped_over_several_lines():
    # Older snapcraft wrote snap.yaml with PyYAML, which wraps long values at 80
    # columns, quoted or not (core20, among others).
    text = (
        "name: core20\n"
        'description: "The base snap based on the Ubuntu 20.04 release, with a description\n'
        "  long enough to wrap\\\n"
        "  \\ onto the next lines.\n"
        "\n"
        '  A second paragraph."\n'
        "summary: 'It''s a single quoted value\n"
        "  that goes on'\n"
        "plain: one two\n"
        "  - three four\n"
        "list:\n"
        "- first item\n"
        "  continued\n"
    )
    data = loads(text)
    assert data["description"] == (
        "The base snap based on the Ubuntu 20.04 release, with a description long enough"
        " to wrap onto the next lines.\nA second paragraph."
    )
    assert data["summary"] == "It's a single quoted value that goes on"
    assert data["plain"] == "one two - three four"
    assert data["list"] == ["first item continued"]
    # How PyYAML writes a value ending in a line break, as in core20's own snap.yaml.
    core20 = "description: 'The base snap based on the Ubuntu 20.04 release.\n\n  '\nname: core20\n"
    assert loads(core20) == {
        "description": "The base snap based on the Ubuntu 20.04 release.\n",
        "name": "core20",
    }
    with pytest.raises(YamlError):
        loads("a: one\n  b: two\n")  # A key cannot continue a value.
    with pytest.raises(YamlError):
        loads('a: "open\nb: x\n')


def test_yaml_reader_reads_every_long_value_pyyaml_wraps():
    yaml = pytest.importorskip("yaml")
    import random
    import string

    random.seed(11)
    alphabet = string.ascii_letters + "   .,;-'\"!?\\:#\n\t()[]{}&*|>é"
    for _ in range(600):
        document = {
            f"k{n}": "".join(random.choice(alphabet) for _ in range(random.randint(0, 300)))
            for n in range(3)
        }
        document["list"] = [document["k0"][::-1]]
        for style in (None, '"', "'"):
            try:
                text = yaml.dump(document, allow_unicode=True, default_style=style, width=60)
            except yaml.YAMLError:
                continue
            assert loads(text) == yaml.safe_load(text), text


# Metadata rules


def test_metadata_reads_apps_content_and_layouts():
    info = snap_meta.parse(FIREFOX)
    assert (info.name, info.base, info.confinement, info.kind) == (
        "firefox",
        "core22",
        "strict",
        "app",
    )
    assert info.app("firefox").command_chain == ("snap/command-chain/desktop-launch",)
    assert [(p.name, p.provider) for p in info.content] == [
        ("gnome-42-2204", "gnome-42-2204"),
        ("gtk-3-themes", "gtk-common-themes"),
    ]
    assert snap.runtime_names(info) == ["core22", "gnome-42-2204", "gtk-common-themes"]
    assert snap_meta.refusal(info, "x86_64") == ""


def test_defaults_follow_snapd():
    info = snap_meta.parse("name: tool\napps:\n  tool:\n    command: bin/tool\n")
    assert (info.base, info.confinement, info.version) == ("core", "strict", "Not provided")


@pytest.mark.parametrize(
    "extra,reason",
    [
        ("    daemon: simple\n    activates-on: [x]\n", "services that MultiPak cannot run"),
        ("    daemon: dbus\n", "cannot run yet: server (dbus)"),
        ("    daemon: simple\n    timer: 'someday'\n", "cannot run yet: server (timer)"),
        ("    daemon: simple\n    sockets:\n      s:\n        listen-stream: 80\n", "(sockets)"),
        ("    plugs: [docker-support]\n", "system-level permissions"),
    ],
)
def test_services_and_system_permissions_are_refused(extra, reason):
    info = snap_meta.parse(f"name: server\napps:\n  server:\n    command: bin/s\n{extra}")
    assert reason in snap_meta.refusal(info, "x86_64")


def test_system_components_and_other_architectures_are_refused():
    base = snap_meta.parse("name: core24\ntype: base\n")
    assert "system component" in snap_meta.refusal(base, "x86_64")
    arm = snap_meta.parse("name: a\narchitectures: [arm64]\napps:\n  a:\n    command: a\n")
    assert "cannot run on this computer" in snap_meta.refusal(arm, "x86_64")
    assert snap_meta.refusal(arm, "aarch64") == ""


def test_components_are_reported_not_refused():
    info = snap_meta.parse(
        "name: a\napps:\n  a:\n    command: a\ncomponents:\n  models:\n    type: standard\n"
    )
    assert info.components == ("models",)
    assert snap_meta.refusal(info, "x86_64") == ""
    (warning,) = snap_meta.limitations(info)
    assert "optional components are not installed: models" in warning
    assert "may not work" in warning
    assert snap_meta.limitations(info, installed=("models",)) == []
    assert info.component_details == (snap_meta.Component("models", "standard", ""),)


def _with_users(names):
    users = "".join(f"  {name}: shared\n" for name in names)
    return snap_meta.parse(f"name: a\napps:\n  a:\n    command: a\nsystem-usernames:\n{users}")


def test_only_snapds_shared_system_accounts_are_provided():
    assert snap_meta.refusal(_with_users(["snap_daemon", "_daemon_"]), "x86_64") == ""
    refusal = snap_meta.refusal(_with_users(["snap_daemon", "snap_microk8s"]), "x86_64")
    assert "system user accounts (snap_microk8s)" in refusal
    # A name not fit to show is still a name the snap needs: refused, never ignored.
    assert "system user accounts" in snap_meta.refusal(_with_users(["'odd name'"]), "x86_64")


@pytest.fixture
def subordinate(monkeypatch):
    """A computer with newuidmap and subordinate IDs, as rootless Podman needs."""
    import pwd
    from types import SimpleNamespace

    from multipak import host

    name = pwd.getpwuid(os.getuid()).pw_name
    files = {
        "/etc/subuid": f"other:100000:65536\n{name}:524288:65536\n",
        "/etc/subgid": f"{os.getuid()}:600000:65536\n",
        "/etc/passwd": "root:x:0:0::/root:/bin/sh\nsnap_daemon:x:1:1::/:/bin/sh\n",
        "/etc/group": "root:x:0:\n",
    }

    def run(argv, **_kwargs):
        return SimpleNamespace(stdout=files.get(argv[-1], ""), returncode=0)

    monkeypatch.setattr(host, "run", run)
    monkeypatch.setattr(host, "which", lambda tool: f"/usr/bin/{tool}")
    return files


def test_system_users_get_the_last_subordinate_ids(subordinate):
    mapping = snap.user_mapping(_with_users(["_daemon_", "snap_daemon"]))
    uid, gid = os.getuid(), os.getgid()
    assert mapping == [
        "unshare",
        f"--map-users=0:{uid}:1",
        f"--map-groups=0:{gid}:1",
        "--map-users=584788:589823:1",
        "--map-groups=584788:665535:1",
        "--map-users=584792:589822:1",
        "--map-groups=584792:665534:1",
        "--setuid",
        "0",
        "--setgid",
        "0",
        "--",
    ]
    assert snap.user_mapping(_with_users([])) == []
    options = ["--unshare-user", "--uid", "0", "--gid", "0", "--die-with-parent"]
    command = snap.bubble_command(_with_users(["snap_daemon"]), options, ["run"])
    # bubblewrap joins the namespace unshare made, instead of making one with root only.
    assert command[0] == "unshare" and command[-4:] == ["bwrap", "--die-with-parent", "--", "run"]
    # Apps run as the user, as always.
    app = snap.bubble_command(_with_users(["snap_daemon"]), ["--die-with-parent"], ["run"])
    assert app == ["bwrap", "--die-with-parent", "--", "run"]
    # As plan_launch gives them to a service: the identity after other options.
    service = snap.bubble_command(
        _with_users(["snap_daemon"]), ["--unshare-pid", "--clearenv", *options], ["run"]
    )
    assert service[0] == "unshare"
    assert service[service.index("bwrap") :] == [
        "bwrap",
        "--unshare-pid",
        "--clearenv",
        "--die-with-parent",
        "--",
        "run",
    ]


def test_without_subordinate_ids_system_users_are_explained(subordinate, monkeypatch):
    subordinate["/etc/subgid"] = ""
    with pytest.raises(RuntimeError, match="subordinate IDs"):
        snap.user_mapping(_with_users(["snap_daemon"]))


def test_the_bubble_knows_the_system_users(subordinate, store):
    from multipak.models import Transaction

    tx = Transaction("e" * 32, "", "", "a", "1", "snap", "", method="snap")
    (passwd, local_passwd), (group, local_group) = snap._accounts(
        store, tx, _with_users(["snap_daemon"])
    )
    assert (passwd, group) == ("/etc/passwd", "/etc/group")
    # The computer's own snap_daemon line, if any, gives way to snapd's.
    assert Path(local_passwd).read_text().splitlines() == [
        "root:x:0:0::/root:/bin/sh",
        "snap_daemon:x:584788:584788::/nonexistent:/usr/bin/false",
    ]
    assert Path(local_group).read_text() == "root:x:0:\nsnap_daemon:x:584788:\n"


def test_content_without_a_provider_is_reported():
    info = snap_meta.parse(
        "name: a\napps:\n  a:\n    command: a\nplugs:\n"
        "  themes:\n    interface: content\n    content: gtk-3-themes\n"
        "    target: $SNAP/data-dir/themes\n"
        "  odd:\n    interface: content\n    content: <b>x</b>\n    target: $SNAP/odd\n"
        "  unused:\n    interface: content\n    content: nothing\n"
    )
    # Not mounted, not downloaded, but shown; markup from the package is never shown.
    assert info.content == () and info.unprovided == ("gtk-3-themes", "odd")
    assert snap_meta.refusal(info, "x86_64") == ""
    (warning,) = snap_meta.limitations(info)
    assert "gtk-3-themes, odd" in warning


@pytest.mark.parametrize(
    "path,source",
    [
        ("/tmp/x", "$SNAP/x"),
        ("/var/lib/snapd/x", "$SNAP/x"),
        ("/usr/share/x", "/etc/shadow"),
        ("/usr/share/../x", "$SNAP/x"),
    ],
)
def test_layouts_snapd_would_refuse_are_refused(path, source):
    info = snap_meta.parse(
        f"name: a\napps:\n  a:\n    command: a\nlayout:\n  {path}:\n    bind: {source}\n"
    )
    variables = {"SNAP": "/snap/a/1", "SNAP_DATA": "/var/snap/a/1", "SNAP_COMMON": "/var/snap/a/c"}
    with pytest.raises(ValueError):
        snap_meta.check_layouts(info, variables)


def test_commands_cannot_leave_the_snap():
    with pytest.raises(ValueError):
        snap_meta.parse("name: a\napps:\n  a:\n    command: ../../usr/bin/sh\n")


# The Snap Store


@pytest.mark.parametrize(
    "text,name",
    [
        ("hello-world", "hello-world"),
        ("snap:hello-world", "hello-world"),
        # The Snap Store website's "View in Desktop store" button opens snap://NAME.
        ("snap://mindustry", "mindustry"),
        ("snap://mindustry/", "mindustry"),
        ("snap://mindustry?channel=latest/stable", "mindustry"),
        ("https://snapcraft.io/hello-world", "hello-world"),
        ("https://snapcraft.io/install/hello-world/fedora", ""),
        ("https://snapcraft.io/hello-world?x=1", "hello-world"),
        ("../etc", ""),
        ("Hello", ""),
    ],
)
def test_store_names_and_links(text, name):
    assert snap_store.store_name(text) == name


class Opener:
    def __init__(self, replies):
        self.replies, self.requests = replies, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        reply = self.replies[request.full_url.split("?")[0]]
        return io.BytesIO(reply)


def store_reply(payload, **download):
    return json.dumps(
        {
            "channel-map": [
                {
                    "channel": {"architecture": "amd64", "risk": "stable", "track": "latest"},
                    "revision": 42,
                    "version": "1.0",
                    "base": "core22",
                    "confinement": "strict",
                    "download": {
                        "url": "https://api.snapcraft.io/download/hello_42.snap",
                        "sha3-384": hashlib.sha3_384(payload).hexdigest(),
                        "size": len(payload),
                    }
                    | download,
                }
            ]
        }
    ).encode()


def test_store_release_and_verified_download(tmp_path):
    payload = b"hsqs" + b"x" * 100
    opener = Opener(
        {
            snap_store.API + "hello": store_reply(payload),
            "https://api.snapcraft.io/download/hello_42.snap": payload,
        }
    )
    release = snap_store.release("hello", opener=opener, arch="amd64")
    assert (release.revision, release.base, release.size) == ("42", "core22", 104)
    assert opener.requests[0].get_header("Snap-device-series") == "16"
    target = snap_store.download(release, tmp_path / "hello.snap", opener=opener)
    assert target.read_bytes() == payload


def test_a_download_that_does_not_match_the_digest_is_discarded(tmp_path):
    opener = Opener(
        {
            snap_store.API + "hello": store_reply(b"good"),
            "https://api.snapcraft.io/download/hello_42.snap": b"evil",
        }
    )
    release = snap_store.release("hello", opener=opener, arch="amd64")
    with pytest.raises(RuntimeError, match="verification"):
        snap_store.download(release, tmp_path / "hello.snap", opener=opener)
    assert list(tmp_path.iterdir()) == []


def test_insecure_download_addresses_are_refused():
    opener = Opener({snap_store.API + "hello": store_reply(b"x", url="http://example.org/x")})
    with pytest.raises(RuntimeError):
        snap_store.release("hello", opener=opener, arch="amd64")


# The bubblewrap view


def base_tree(root):
    for name in ("usr/bin", "usr/lib", "usr/share", "etc", "var/lib", "home", "run", "tmp"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "bin").symlink_to("usr/bin")
    (root / "usr/share/doc").mkdir()
    (root / "usr/share/icons").mkdir()
    (root / "meta").mkdir()
    (root / "meta/snap.yaml").write_text("name: core22\ntype: base\n")
    return root


def request_for(tmp_path, *, classic=False, layouts=(), content=()):
    base = base_tree(tmp_path / "base")
    app = tmp_path / "app"
    (app / "meta").mkdir(parents=True)
    return Request(
        snap=Mount("hello", "x1", str(app)),
        base=Mount("core22", "7", str(base)),
        classic=classic,
        providers=(),
        content=content,
        layouts=layouts,
        data=str(tmp_path / "data"),
        common=str(tmp_path / "common"),
        uid=1000,
        home="/var/home/user",
        environment=(("SNAP", "/snap/hello/x1"),),
        argv=(),
        workdir="/var/home/user/snap/hello/x1",
    )


def pairs(arguments):
    """(option, destination) in order, for checking the mount order."""
    result, index = [], 0
    while index < len(arguments):
        option = arguments[index]
        if option in ("--ro-bind", "--bind", "--dev-bind", "--symlink", "--setenv"):
            result.append((option, arguments[index + 2]))
            index += 3
        elif option in ("--tmpfs", "--dir", "--proc", "--chdir"):
            result.append((option, arguments[index + 1]))
            index += 2
        else:
            result.append((option, None))
            index += 1
    return result


HOST = {"/": [Entry("usr", "d"), Entry("home", "l", "var/home"), Entry("var", "d")]}
EXISTS = {"/etc", "/home", "/run", "/var/tmp", "/var/home", "/run/user/1000"}


def test_strict_snaps_see_their_base_with_host_directories_on_top(tmp_path):
    arguments, _ = build(request_for(tmp_path), HOST.get, EXISTS.__contains__)
    order = pairs(arguments)
    assert order[1] == ("--tmpfs", "/")
    assert ("--ro-bind", "/usr") in order and ("--symlink", "/bin") in order
    assert ("--ro-bind", "/etc") in order  # The base's, with the computer's entries.
    assert ("--bind", "/var/home") in order  # Atomic systems keep homes there.
    assert ("--ro-bind", "/var/lib/snapd/hostfs") in order
    assert ("--ro-bind", "/snap/hello/x1") in order
    assert ("--symlink", "/snap/core22/current") in order
    assert ("--dir", "/run/user/1000/snap.hello") in order
    # Parents are always mounted before their children.
    destinations = [d for o, d in order if d and o not in ("--setenv", "--chdir")]
    for index, path in enumerate(destinations):
        for parent in destinations[index + 1 :]:
            assert not (path.startswith(parent + "/") and parent != "/"), (path, parent)


def test_the_snaps_own_tmp_replaces_a_tmp_of_one_run(tmp_path):
    exists = EXISTS | {"/tmp/.X11-unix"}
    plain = request_for(tmp_path)
    order = pairs(build(plain, HOST.get, exists.__contains__)[0])
    assert ("--tmpfs", "/tmp") in order
    request = dataclasses.replace(plain, tmp=str(tmp_path / "own-tmp"))
    arguments = build(request, HOST.get, exists.__contains__)[0]
    order = pairs(arguments)
    assert ("--tmpfs", "/tmp") not in order
    index = arguments.index(str(tmp_path / "own-tmp"))
    assert arguments[index - 1 : index + 2] == ["--bind", str(tmp_path / "own-tmp"), "/tmp"]
    # The X server's sockets still come from the computer, on top of it.
    assert order.index(("--bind", "/tmp")) < order.index(("--ro-bind", "/tmp/.X11-unix"))


def test_the_snaps_own_tmp_is_made_private_and_kept(private_tmp_root):
    tx = Transaction("c" * 32, "", "", "served", "1", "snap", "", method="snap")
    tmp = snap.private_tmp(tx)
    assert tmp == private_tmp_root / tx.id / "tmp"
    assert stat.S_IMODE(private_tmp_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((private_tmp_root / tx.id).stat().st_mode) == 0o700
    # As /tmp: sticky and open to all, also for the snap's system users.
    assert stat.S_IMODE(tmp.stat().st_mode) == 0o1777
    (tmp / "mysql.sock").write_text("")
    assert snap.private_tmp(tx) == tmp and (tmp / "mysql.sock").exists()
    other = Transaction("d" * 32, "", "", "other", "1", "snap", "", method="snap")
    assert snap.private_tmp(other) != tmp
    with pytest.raises(ValueError):
        snap.private_tmp(Transaction("../x", "", "", "x", "1", "snap", "", method="snap"))


def test_a_planted_link_is_never_used_as_the_snaps_tmp(tmp_path, monkeypatch):
    # In the computer's /tmp, anyone could have made the folder first.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root = tmp_path / "root"
    root.symlink_to(elsewhere)
    monkeypatch.setattr(snap, "PRIVATE_TMP_ROOT", str(root))
    tx = Transaction("c" * 32, "", "", "served", "1", "snap", "", method="snap")
    with pytest.raises(RuntimeError, match="/tmp folder"):
        snap.private_tmp(tx)
    assert list(elsewhere.iterdir()) == []


def test_mounts_below_a_linked_base_directory_follow_the_link(tmp_path):
    # core24 makes /lib a link to usr/lib; the computer's kernel modules then belong at
    # /usr/lib/modules, where snapd's mount also ends up.
    request = request_for(tmp_path)
    (tmp_path / "base/lib").symlink_to("usr/lib")
    (tmp_path / "base/usr/lib/modules").mkdir()
    exists = EXISTS | {"/lib/modules", "/usr/lib/modules", "/lib/firmware"}
    order = pairs(build(request, HOST.get, exists.__contains__)[0])
    assert ("--symlink", "/lib") in order
    assert ("--ro-bind", "/usr/lib/modules") in order
    # Missing in the base: /usr/lib is recreated with its entries to hold the mount point.
    assert ("--ro-bind", "/usr/lib/firmware") in order
    assert ("--tmpfs", "/usr/lib") in order
    assert not [d for _o, d in order if d and d.startswith("/lib/")]


@pytest.mark.parametrize("target", ["/usr/lib", "./usr/lib", "../../../usr/lib", "usr/x/../lib"])
def test_links_are_followed_inside_the_bubble_whatever_their_form(tmp_path, target):
    # Absolute and relative targets alike are paths inside the bubble, never on the
    # computer, and ".." cannot climb above the bubble's root.
    request = request_for(tmp_path)
    (tmp_path / "base/lib").symlink_to(target)
    (tmp_path / "base/usr/lib/modules").mkdir()
    exists = EXISTS | {"/lib/modules"}
    order = pairs(build(request, HOST.get, exists.__contains__)[0])
    assert ("--ro-bind", "/usr/lib/modules") in order
    assert not [d for _o, d in order if d and (d.startswith("/lib/") or ".." in d)]


@pytest.mark.parametrize("links", [{"lib": "lib"}, {"lib": "lib2", "lib2": "/lib"}])
def test_circular_links_stop_the_launch(tmp_path, links):
    request = request_for(tmp_path)
    for name, target in links.items():
        (tmp_path / "base" / name).symlink_to(target)
    exists = EXISTS | {"/lib/modules"}
    with pytest.raises(ValueError, match="too many symbolic links"):
        build(request, HOST.get, exists.__contains__)


def test_a_layout_inside_a_content_target_binds_the_providers_file(tmp_path):
    # Okular binds $SNAP/gpu-2404/libdrm, which mesa-2404 provides, at /usr/share/libdrm.
    provider = tmp_path / "mesa"
    (provider / "libdrm").mkdir(parents=True)
    (provider / "escape").symlink_to("/etc")
    content = (("/snap/hello/x1/gpu-2404", str(provider)),)
    layouts = (
        snap_meta.Layout("/usr/share/libdrm", "bind", "/snap/hello/x1/gpu-2404/libdrm"),
        snap_meta.Layout("/usr/share/gone", "bind", "/snap/hello/current/gpu-2404/gone"),
    )
    arguments, _ = build(
        request_for(tmp_path, layouts=layouts, content=content), HOST.get, EXISTS.__contains__
    )
    index = arguments.index("/usr/share/libdrm")
    assert arguments[index - 2 : index] == ["--ro-bind", str(provider / "libdrm")]
    # A source the provider does not have is left out, instead of failing to start.
    assert "/usr/share/gone" not in arguments
    # A link out of the provider is never followed.
    escape = (snap_meta.Layout("/usr/share/x", "bind", "/snap/hello/x1/gpu-2404/escape"),)
    with pytest.raises(ValueError, match="leaves"):
        build(
            request_for(tmp_path / "again", layouts=escape, content=content),
            HOST.get,
            EXISTS.__contains__,
        )


def test_a_layout_on_a_missing_path_recreates_its_parent_with_the_original_entries(tmp_path):
    layout = snap_meta.Layout("/usr/share/libdrm", "bind", "/snap/hello/x1/libdrm")
    (tmp_path / "app/libdrm").mkdir(parents=True)
    arguments, _ = build(request_for(tmp_path, layouts=(layout,)), HOST.get, EXISTS.__contains__)
    order = pairs(arguments)
    mimic = order.index(("--tmpfs", "/usr/share"))
    assert order.index(("--ro-bind", "/usr")) < mimic
    assert ("--ro-bind", "/usr/share/doc") in order[mimic:]
    assert ("--ro-bind", "/usr/share/icons") in order[mimic:]
    assert order.index(("--ro-bind", "/usr/share/libdrm")) > mimic
    source = arguments[arguments.index("/usr/share/libdrm") - 1]
    assert source == str(tmp_path / "app/libdrm")


def test_classic_snaps_see_the_host_with_snap_added(tmp_path):
    arguments, _ = build(request_for(tmp_path, classic=True), HOST.get, EXISTS.__contains__)
    order = pairs(arguments)
    assert ("--bind", "/usr") in order and ("--symlink", "/home") in order
    assert ("--ro-bind", "/snap/core22/7") in order
    assert not any(dest == "/var/lib/snapd/hostfs" for _option, dest in order)


def test_etc_is_the_bases_with_the_computers_own_entries(tmp_path):
    request = request_for(tmp_path)
    base = Path(request.base.path)
    (base / "etc/ssl/certs").mkdir(parents=True)
    (base / "etc/passwd").write_text("root:x:0:0::/root:/bin/sh\n")
    (base / "etc/localtime").symlink_to("/usr/share/zoneinfo/Etc/UTC")
    listings = dict(HOST)
    listings["/etc"] = [
        Entry("passwd", "f"),
        Entry("fonts", "d"),
        Entry("localtime", "l", "../usr/share/zoneinfo/America/Sao_Paulo"),
        Entry("resolv.conf", "l", "../run/systemd/resolve/stub-resolv.conf"),
        Entry("ssl", "d"),
        Entry("pki", "d"),
    ]
    exists = EXISTS | {f"/etc/{e.name}" for e in listings["/etc"]}
    arguments, _ = build(request, listings.get, exists.__contains__)
    order = pairs(arguments)

    def source(destination):
        return arguments[len(arguments) - 1 - arguments[::-1].index(destination) - 1]

    # The base's /etc, recreated so that the computer's entries can be added.
    assert ("--tmpfs", "/etc") in order
    assert source("/etc/ssl") == str(base / "etc/ssl")
    for name in ("passwd", "fonts", "localtime"):
        assert source(f"/etc/{name}") == f"/etc/{name}"
    # A link into /run stays a link, so it follows the live file.
    assert ("--symlink", "/etc/resolv.conf") in order
    assert "/etc/pki" not in arguments
    # On Debian and Ubuntu, the computer's certificates are used, as snapd does.
    debian = exists | {"/etc/debian_version"}
    arguments, _ = build(request, listings.get, debian.__contains__)
    assert source("/etc/ssl") == "/etc/ssl"


def test_declared_interfaces_bring_the_host_directories_snapd_mounts(tmp_path):
    from dataclasses import replace

    listings = dict(HOST)
    listings["/usr/share"] = [Entry("fonts", "d"), Entry("doc", "d"), Entry("nvidia", "d")]
    listings["/var/cache"] = [Entry("fontconfig", "d")]
    exists = EXISTS | {"/usr/share/fonts", "/usr/share/doc", "/usr/share/nvidia"}
    exists |= {"/var/cache/fontconfig"}
    plain = request_for(tmp_path)
    arguments, _ = build(plain, listings.get, exists.__contains__)
    assert "/usr/share/fonts" not in arguments
    request = replace(
        plain, interfaces=frozenset({"desktop", "opengl"}), font_cache=False, private_shm=True
    )
    arguments, _ = build(request, listings.get, exists.__contains__)
    order = pairs(arguments)
    assert ("--ro-bind", "/usr/share/fonts") in order
    assert ("--ro-bind", "/usr/share/nvidia") in order
    assert "/var/cache/fontconfig" not in arguments  # Not on Fedora and Arch.
    assert "/usr/share/doc" not in arguments[arguments.index("/usr/share/fonts") :]
    assert order.index(("--dev-bind", "/dev")) < order.index(("--tmpfs", "/dev/shm"))
    request = replace(request, font_cache=True, interfaces=frozenset({"desktop"}))
    arguments, _ = build(request, listings.get, exists.__contains__)
    assert ("--ro-bind", "/var/cache/fontconfig") in pairs(arguments)


def test_interfaces_include_plugs_named_after_them():
    info = snap_meta.parse(
        "name: a\napps:\n  a:\n    command: a\n    plugs: [desktop, dot-config]\n"
        "plugs:\n  dot-config:\n    interface: personal-files\n"
        "    write: [$HOME/.config/a/settings, $HOME/../etc, /etc/x]\n"
    )
    assert snap_meta.interfaces(info) == {"desktop", "personal-files"}
    assert snap_meta.personal_directories(info, "/home/u") == ["/home/u/.config/a"]


def test_nvidia_drivers_are_exposed_like_snapd_does(tmp_path):
    listings = dict(HOST)
    listings["/usr/lib64"] = [Entry("libnvidia-glcore.so.1", "f"), Entry("libz.so.1", "f")]
    exists = EXISTS | {"/sys/module/nvidia"}
    arguments, _ = build(request_for(tmp_path), listings.get, exists.__contains__)
    assert "/var/lib/snapd/lib/gl/libnvidia-glcore.so.1" in arguments
    assert "/var/lib/snapd/lib/gl/libz.so.1" not in arguments


# Environment and command


def installed(info_text, revision="x1"):
    info = snap_meta.parse(info_text)
    tx = Transaction("a" * 32, "", "", info.name, info.version, "snap", "", method="snap")
    tx.snap_revision = revision
    return info, tx


def test_environment_follows_snap_exec_order_and_expansion():
    info, tx = installed(
        "name: a\nenvironment:\n  A: one\n  B: $A-two\napps:\n  a:\n    command: bin/a\n"
        "    environment:\n      B: $B-three\n      C: ${SNAP}/lib:$HOST_VALUE\n"
    )
    env, variables = snap.environment(
        info, info.app("a"), tx, {"HOME": "/home/u", "HOST_VALUE": "h"}, 1000
    )
    assert env["B"] == "one-two-three"
    assert env["C"] == "/snap/a/x1/lib:h"
    assert env["HOME"] == "/home/u/snap/a/x1"
    assert env["SNAP_REAL_HOME"] == "/home/u"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000/snap.a"
    assert env["PATH"].startswith("/snap/a/x1/usr/sbin:")


def test_classic_snaps_keep_the_real_home():
    info, tx = installed("name: c\nconfinement: classic\napps:\n  c:\n    command: bin/c\n")
    env, _variables = snap.environment(info, info.app("c"), tx, {"HOME": "/home/u"}, 1000)
    assert env["HOME"] == "/home/u" and "XDG_RUNTIME_DIR" not in env


def test_command_chain_and_arguments_like_snap_exec():
    info, tx = installed(
        "name: a\napps:\n  a:\n    command: bin/a --rev $SNAP_REVISION $UNSET\n"
        "    command-chain: [snap/command-chain/desktop-launch]\n"
    )
    env, _variables = snap.environment(info, info.app("a"), tx, {"HOME": "/home/u"}, 1000)
    assert snap.command(info, info.app("a"), env, ["file name"]) == [
        "/snap/a/x1/snap/command-chain/desktop-launch",
        "/snap/a/x1/bin/a",
        "--rev",
        "x1",
        "file name",
    ]


def test_content_slots_are_mounted_at_the_plug_target(tmp_path):
    info, _tx = installed(
        "name: a\napps:\n  a:\n    command: a\nplugs:\n  themes:\n    interface: content\n"
        "    target: $SNAP/data-dir/themes\n    default-provider: gtk-common-themes\n"
    )
    provider = tmp_path / "gtk"
    (provider / "meta").mkdir(parents=True)
    (provider / "share/themes/Adwaita").mkdir(parents=True)
    (provider / "share/themes/Yaru").mkdir(parents=True)
    (provider / "meta/snap.yaml").write_text(
        "name: gtk-common-themes\nslots:\n  themes:\n    interface: content\n"
        "    source:\n      read:\n      - $SNAP/share/themes/Adwaita\n"
        "      - $SNAP/share/themes/Yaru\n"
    )
    mounts = snap.content_mounts(
        info,
        {"gtk-common-themes": Mount("gtk-common-themes", "5", str(provider))},
        {"SNAP": "/snap/a/x1", "SNAP_DATA": "/var/snap/a/x1", "SNAP_COMMON": "/var/snap/a/c"},
    )
    assert mounts == (
        ("/snap/a/x1/data-dir/themes/Adwaita", str(provider / "share/themes/Adwaita")),
        ("/snap/a/x1/data-dir/themes/Yaru", str(provider / "share/themes/Yaru")),
    )


def test_content_slot_paths_without_a_variable_are_inside_the_provider(tmp_path):
    # gnome-core26 shares itself with "read: [/]"; snapd reads that as $SNAP.
    info, _tx = installed(
        "name: a\napps:\n  a:\n    command: a\nplugs:\n  gnome-core26:\n"
        "    interface: content\n    target: $SNAP/gnome-platform\n"
        "    default-provider: gnome-core26\n"
    )
    provider = tmp_path / "gnome"
    (provider / "meta").mkdir(parents=True)
    (provider / "command-chain").mkdir()
    (provider / "meta/snap.yaml").write_text(
        "name: gnome-core26\nslots:\n  gnome-core26:\n    interface: content\n    read:\n    - /\n"
    )
    variables = {"SNAP": "/snap/a/x1", "SNAP_DATA": "/var/snap/a/x1", "SNAP_COMMON": "/c"}
    providers = {"gnome-core26": Mount("gnome-core26", "5", str(provider))}
    assert snap.content_mounts(info, providers, variables) == (
        ("/snap/a/x1/gnome-platform", str(provider)),
    )
    # Relative paths too, but never outside the provider, nor its data folders.
    for read, expected in (
        ("command-chain", (("/snap/a/x1/gnome-platform", str(provider / "command-chain")),)),
        ("../../etc", ()),
        ("$SNAP_DATA/x", ()),
    ):
        (provider / "meta/snap.yaml").write_text(
            "name: gnome-core26\nslots:\n  gnome-core26:\n    interface: content\n"
            f"    read:\n    - '{read}'\n"
        )
        assert snap.content_mounts(info, providers, variables) == expected, read


# Installing real snap files


needs_squashfs = pytest.mark.skipif(
    not (shutil.which("mksquashfs") and shutil.which("unsquashfs")),
    reason="squashfs-tools are needed",
)


def make_snap(directory, files, destination):
    for name, (content, mode) in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(mode)
    subprocess.run(
        ["mksquashfs", str(directory), str(destination), "-noappend", "-quiet", "-all-root"],
        check=True,
        capture_output=True,
    )
    return destination


def minimal_base(directory):
    """A base made from this machine's shell, enough to run shell scripts."""
    (directory / "usr/bin").mkdir(parents=True)
    (directory / "usr/lib").mkdir(parents=True)
    for tool in (
        "sh",
        "cat",
        "readlink",
        "test",
        "echo",
        "mkdir",
        "env",
        "mktemp",
        "mkfifo",
        "rm",
        "id",
        "touch",
        "ls",
        "grep",
        "unshare",
        "sed",
        "tr",
        "sleep",
    ):
        found = shutil.which(tool)
        if found:
            shutil.copy(os.path.realpath(found), directory / "usr/bin" / tool)
    libraries = set()
    for tool in (directory / "usr/bin").iterdir():
        result = subprocess.run(["ldd", str(tool)], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            parts = line.split()
            if "=>" in parts and len(parts) > 2 and parts[2].startswith("/"):
                libraries.add(parts[2])
            elif parts and parts[0].startswith("/"):
                libraries.add(parts[0])
    for library in libraries:
        target = directory / library.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.realpath(library), target)
    for name in ("bin", "lib", "lib64"):
        if not (directory / name).exists():
            (directory / name).symlink_to("usr/" + name if name != "lib64" else "usr/lib64")
    (directory / "usr/lib64").mkdir(exist_ok=True)
    for name in ("dev", "etc", "home", "run", "tmp", "var/lib", "var/tmp", "usr/share"):
        (directory / name).mkdir(parents=True, exist_ok=True)
    (directory / "meta").mkdir()
    (directory / "meta/snap.yaml").write_text("name: core24\ntype: base\n")


@pytest.fixture
def fixtures(tmp_path):
    out = tmp_path / "snaps"
    out.mkdir()
    base_dir = tmp_path / "base"
    minimal_base(base_dir)
    (base_dir / "var/lib/snapd").mkdir(parents=True)
    subprocess.run(
        # Real bases ship device nodes, which only root can create when extracting, and
        # a folder nobody may open (var/lib/snapd/void).
        [
            "mksquashfs",
            str(base_dir),
            str(out / "core24.snap"),
            "-noappend",
            "-quiet",
            "-p",
            "dev/null c 666 root root 1 3",
            "-p",
            "dev/zero c 666 root root 1 5",
            "-p",
            "var/lib/snapd/void d 000 root root",
        ],
        check=True,
        capture_output=True,
    )
    make_snap(
        tmp_path / "platform",
        {
            "meta/snap.yaml": (
                "name: fake-platform\nslots:\n  fake-platform:\n    interface: content\n"
                "    read:\n    - $SNAP/share\n",
                0o644,
            ),
            "share/hello.txt": ("from the platform\n", 0o644),
        },
        out / "fake-platform.snap",
    )
    script = (
        '#!/bin/sh\necho "ARGS:$*"\necho "CHAIN:$CHAINED"\n'
        'cat "$SNAP/platform/hello.txt"\ncat /usr/share/hello-layout/inside.txt\n'
        'test -d /snap/core24/current && echo BASE\necho data > "$SNAP_DATA/file" && echo DATA\n'
    )
    make_snap(
        tmp_path / "app",
        {
            "meta/snap.yaml": (
                "name: hello-multipak\nversion: '1.0'\nbase: core24\napps:\n  hello:\n"
                "    command: bin/hello --flag $SNAP_REVISION\n"
                "    command-chain: [snap/command-chain/launch]\nplugs:\n  platform:\n"
                "    interface: content\n    content: fake-platform\n    target: $SNAP/platform\n"
                "    default-provider: fake-platform\nlayout:\n  /usr/share/hello-layout:\n"
                "    bind: $SNAP/layout-data\n",
                0o644,
            ),
            "bin/hello": (script, 0o755),
            "snap/command-chain/launch": ('#!/bin/sh\nexport CHAINED=yes\nexec "$@"\n', 0o755),
            "layout-data/inside.txt": ("layout file\n", 0o644),
            "platform/.keep": ("", 0o644),
            "meta/gui/hello.desktop": (
                "[Desktop Entry]\nType=Application\nName=Hello\n"
                "Exec=hello-multipak.hello %U\nIcon=${SNAP}/meta/gui/icon.png\n",
                0o644,
            ),
            "meta/gui/icon.png": ("PNG", 0o644),
            # Named icons, as snapd exports them; only snap.<name>.* outside snap.*.
            "meta/gui/icons/hicolor/48x48/apps/snap.hello-multipak.hello.png": ("I", 0o644),
            "meta/gui/icons/hicolor/scalable/apps/snap.hello-multipak.hello.svg": ("<svg/>", 0o644),
            "meta/gui/icons/hicolor/48x48/apps/other.png": ("O", 0o644),
            "meta/gui/icons/hicolor/48x48/apps/snap.firefox.firefox.png": ("F", 0o644),
            "meta/gui/icons/snap.hidden/snap.hello-multipak.x.png": ("H", 0o644),
        },
        out / "hello-multipak.snap",
    )
    hook = (
        "#!/bin/sh\nset -e\nsnapctl set greeting=hello 'nested.answer=42'\n"
        'id -u > "$SNAP_DATA/hook-uid"\necho "$HOME" > "$SNAP_DATA/hook-home"\n'
        'touch "$HOME/made-by-hook"\n'
    )
    configure = '#!/bin/sh\nsnapctl get greeting > "$SNAP_COMMON/configured"\n'
    reader = (
        "#!/bin/sh\nsnapctl get greeting\nsnapctl get nested.answer\n"
        "snapctl is-connected home && echo CONNECTED\n"
        "snapctl install +extra || echo NO-COMPONENTS\n"
    )
    make_snap(
        tmp_path / "hooked",
        {
            "meta/snap.yaml": (
                "name: hooked\nversion: '1'\nbase: core24\napps:\n  read:\n"
                "    command: bin/read\n    plugs: [home]\n  users:\n    command: bin/users\n",
                0o644,
            ),
            "bin/read": (reader, 0o755),
            "bin/users": ("#!/bin/sh\ncat /etc/passwd\n", 0o755),
            "meta/hooks/install": (hook, 0o755),
            "meta/hooks/configure": (configure, 0o755),
        },
        out / "hooked.snap",
    )
    served = (
        "name: served\nversion: '1'\nbase: core24\napps:\n  open:\n    command: bin/web\n"
        "  web:\n    command: bin/web\n    daemon: simple\n    plugs: [network-bind]\n"
        "  cron:\n    command: bin/web\n    daemon: oneshot\n    after: [web]\n"
        "    install-mode: disable\n"
        "  tick:\n    command: bin/web\n    daemon: dbus\n"
    )
    make_snap(
        tmp_path / "served",
        {
            "meta/snap.yaml": (served, 0o644),
            "bin/web": (
                '#!/bin/sh\necho "$SNAP_UID $SNAP_APP_NAME $HOME" > "$SNAP_DATA/ran"\n'
                "snapctl services\n",
                0o755,
            ),
            # Asking from a script waits for the script to end, as with snapd.
            "meta/hooks/configure": ("#!/bin/sh\nsnapctl restart served.web\n", 0o755),
        },
        out / "served.snap",
    )
    kinds = (
        "name: kinds\nversion: '1'\nbase: core24\napps:\n"
        "  forked:\n    command: bin/forked\n    daemon: forking\n"
        "  listener:\n    command: bin/listener\n    daemon: simple\n"
        "    sockets:\n      control:\n        listen-stream: $SNAP_COMMON/control.sock\n"
        "        socket-mode: 0600\n"
        "  mine:\n    command: bin/mine\n    daemon: simple\n    daemon-scope: user\n"
    )
    make_snap(
        tmp_path / "kinds",
        {
            "meta/snap.yaml": (kinds, 0o644),
            # Starts its daemon and returns at once.
            "bin/forked": (
                '#!/bin/sh\n( sleep 1; echo done > "$SNAP_DATA/forked" ) &\nexit 0\n',
                0o755,
            ),
            "bin/listener": (
                "#!/bin/sh\n"
                '[ "$LISTEN_PID" = "$$" ] && [ -S /proc/self/fd/3 ] && '
                'echo "$LISTEN_FDS $LISTEN_FDNAMES" > "$SNAP_COMMON/activated"\n',
                0o755,
            ),
            "bin/mine": ('#!/bin/sh\necho "$SNAP_UID $HOME" > "$SNAP_USER_COMMON/mine"\n', 0o755),
        },
        out / "kinds.snap",
    )
    make_snap(
        tmp_path / "users",
        {
            "meta/snap.yaml": (
                "name: users\nversion: '1'\nbase: core24\napps:\n  list:\n"
                "    command: bin/list\nsystem-usernames:\n  snap_daemon: shared\n",
                0o644,
            ),
            "bin/list": ("#!/bin/sh\ncat /etc/passwd /etc/group\n", 0o755),
        },
        out / "users.snap",
    )
    stopping = (
        "name: stopping\nversion: '1'\nbase: core24\napps:\n"
        "  daemon:\n    command: bin/daemon\n    daemon: simple\n    stop-mode: sighup\n"
        "    stop-command: bin/stop\n    post-stop-command: bin/after\n"
        "    reload-command: bin/reload\n    install-mode: disable\n"
    )
    make_snap(
        tmp_path / "stopping",
        {
            "meta/snap.yaml": (stopping, 0o644),
            # Waits until HUP ends it, as many daemons do.
            "bin/daemon": (
                "#!/bin/sh\n"
                'trap \'echo "got HUP" >> "$SNAP_DATA/log"; exit 0\' HUP\n'
                'touch "$SNAP_DATA/started"\n'
                "while :; do sleep 0.1; done\n",
                0o755,
            ),
            "bin/stop": ('#!/bin/sh\necho stop-command >> "$SNAP_DATA/log"\n', 0o755),
            "bin/after": ('#!/bin/sh\necho post-stop >> "$SNAP_DATA/log"\n', 0o755),
            "bin/reload": ('#!/bin/sh\ntouch "$SNAP_DATA/reloaded"\n', 0o755),
        },
        out / "stopping.snap",
    )
    make_snap(
        tmp_path / "broken",
        {
            "meta/snap.yaml": (
                "name: broken\nversion: '1'\nbase: core24\napps:\n  b:\n    command: b\n",
                0o644,
            ),
            "b": ("#!/bin/sh\n", 0o755),
            "meta/hooks/install": ("#!/bin/sh\necho cannot prepare >&2\nexit 3\n", 0o755),
        },
        out / "broken.snap",
    )
    make_snap(
        tmp_path / "parts",
        {
            "meta/snap.yaml": (
                "name: parts\nversion: '2'\nbase: core24\napps:\n  show:\n"
                "    command: bin/show\ncomponents:\n  extra:\n    type: standard\n"
                "    summary: Extra data\n  more:\n    type: standard\n"
                "  drivers:\n    type: kernel-modules\n",
                0o644,
            ),
            "bin/show": (
                '#!/bin/sh\ncat "$SNAP_COMPONENTS/extra/data.txt"\n'
                'snapctl install +more && cat "$SNAP_COMPONENTS/more/data.txt"\n',
                0o755,
            ),
        },
        out / "parts.snap",
    )
    make_snap(
        tmp_path / "prefs",
        {
            "meta/snap.yaml": (
                "name: prefs\nversion: '1'\nbase: core24\napps:\n  show:\n"
                "    command: bin/show\n    plugs: [gsettings]\n",
                0o644,
            ),
            "bin/show": ('#!/bin/sh\necho "BACKEND:$GSETTINGS_BACKEND"\n', 0o755),
        },
        out / "prefs.snap",
    )
    # Plugs as Firefox declares them: its own sandbox, system files and mount-control.
    browser = (
        "#!/bin/sh\n"
        "unshare --user --map-root-user id -u | sed 's/^/NESTED-UID:/'\n"
        "grep '^Seccomp:' /proc/self/status | tr -d ' \\t'\n"
        "ls /etc/ld.so.conf.d >/dev/null && echo SYSTEM-FILE-READ\n"
        "touch /etc/ld.so.conf.d/x 2>/dev/null || echo SYSTEM-FILE-READ-ONLY\n"
        "snapctl mount -o ro,bind /usr/share/doc $SNAP_COMMON/doc || echo MOUNT-REFUSED\n"
        "test -d $SNAP_COMMON/doc || echo NOTHING-MOUNTED\n"
    )
    make_snap(
        tmp_path / "browser",
        {
            "meta/snap.yaml": (
                "name: browser\nversion: '1'\nbase: core24\napps:\n  run:\n"
                "    command: bin/run\n    plugs: [browser-sandbox, etc-files, mounts, docs]\n"
                "plugs:\n  browser-sandbox:\n    interface: browser-support\n"
                "    allow-sandbox: true\n  etc-files:\n    interface: system-files\n"
                "    read: [/etc/ld.so.conf.d]\n    write: [/etc/ld.so.conf.d]\n"
                "  mounts:\n    interface: mount-control\n    mount:\n"
                "    - what: /usr/share/doc\n      where: $SNAP_COMMON/doc\n"
                "      options: [ro, bind]\n  docs:\n    interface: system-packages-doc\n",
                0o644,
            ),
            "bin/run": (browser, 0o755),
        },
        out / "browser.snap",
    )
    for component in ("extra", "more"):
        make_snap(
            tmp_path / component,
            {
                "meta/component.yaml": (
                    f"component: parts+{component}\ntype: standard\nversion: '1'\n",
                    0o644,
                ),
                "data.txt": (f"{component} data\n", 0o644),
                "meta/hooks/install": (
                    '#!/bin/sh\necho "$SNAP_COMPONENT_NAME" >> "$SNAP_DATA/component-hooks"\n',
                    0o755,
                ),
            },
            out / f"{component}.snap",
        )
    return out


@pytest.fixture
def local_store(monkeypatch, fixtures):
    class Release(SimpleNamespace):
        pass

    monkeypatch.setattr(
        snap_store, "release", lambda name, **_: Release(name=name, revision="7", version="1")
    )
    monkeypatch.setattr(
        snap_store,
        "download",
        lambda release, target, **_: shutil.copy(fixtures / f"{release.name}.snap", target),
    )
    return fixtures


@needs_squashfs
def test_install_shortcuts_and_removal_with_shared_components(store, local_store):
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    engine = Engine(store, threading.Event())
    package = inspect_package(local_store / "hello-multipak.snap", engine.runner)
    assert package.kind == "snap" and package.snap.name == "hello-multipak"
    assert snap.missing_runtime(store, package.snap) == ["core24", "fake-platform"]
    tx = engine.install_snap(package)
    assert tx.status == "installed"
    assert tx.snap_runtime == ["core24=7", "fake-platform=7"]
    shortcut = store.launcher(tx, tx.launchers[0]).read_text()
    assert f'"{tx.id}" "hello" %U' in shortcut
    assert "X-MultiPak-Method=snap" in shortcut
    assert str(store.snap_directory(tx.id) / "root/meta/gui/icon.png") in shortcut
    assert not (store.snap_directory(tx.id) / "package.snap").exists()
    engine.cleanup(tx)
    assert store.transactions() == []
    assert not store.launcher(tx, tx.launchers[0]).exists()
    assert not any(store.snap_runtime_directory().iterdir())


@needs_squashfs
def test_folders_nobody_may_open_do_not_block_retrying_or_removing(store, local_store):
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    # What an earlier, failed extraction of the base left behind.
    staging = store.snap_runtime_directory() / "core24/.7.tmp"
    (staging / "var/lib/snapd/void").mkdir(parents=True)
    (staging / "var/lib/snapd/void").chmod(0)
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "hello-multipak.snap", engine.runner))
    assert tx.status == "installed"
    assert not staging.exists()
    void = store.snap_runtime_directory() / "core24/7/var/lib/snapd/void"
    assert void.is_dir() and void.stat().st_mode & 0o777 == 0
    engine.cleanup(tx)
    assert not any(store.snap_runtime_directory().iterdir())


@needs_squashfs
def test_an_unfinished_shared_snap_folder_is_replaced(store, local_store):
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    # Seen on 15/11/2026: core24/2307 held only empty folders another tool had made,
    # no meta/snap.yaml, and the new copy could not take its place.
    left = store.snap_runtime_directory() / "core24/7"
    (left / "snap/service-test/1").mkdir(parents=True)
    assert snap.installed_runtime(store, "core24") == ""
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "hello-multipak.snap", engine.runner))
    assert tx.status == "installed"
    assert (left / "meta/snap.yaml").is_file() and not (left / "snap/service-test").exists()
    assert [path.name for path in left.parent.iterdir()] == ["7"]
    engine.cleanup(tx)
    assert not any(store.snap_runtime_directory().iterdir())


def test_a_folder_that_cannot_be_removed_is_left_aside(tmp_path, monkeypatch):
    target = tmp_path / "core24/7"
    (target / "snap").mkdir(parents=True)
    monkeypatch.setattr(snap, "remove_tree", lambda path, **_: None)
    snap._set_aside(target)
    assert not target.exists()
    (aside,) = target.parent.iterdir()
    assert aside.name.startswith(".7.stale-") and (aside / "snap").is_dir()
    runtime = SimpleNamespace(snap_runtime_directory=lambda: tmp_path)
    assert snap.installed_runtime(runtime, "core24") == ""
    snap._set_aside(target)  # Nothing there: nothing to do.


@needs_squashfs
def test_a_refused_snap_downloads_nothing(store, local_store, tmp_path):
    from multipak.engine import Engine

    path = make_snap(
        tmp_path / "server",
        {
            "meta/snap.yaml": (
                "name: server\napps:\n  s:\n    command: s\n    daemon: dbus\n",
                0o644,
            )
        },
        tmp_path / "server.snap",
    )
    engine = Engine(store, threading.Event())
    package = snap.inspect_snap(path, engine.runner)
    with pytest.raises(ValueError, match="cannot run yet"):
        engine.install_snap(package)
    assert store.transactions() == []
    assert not store.snap_runtime_directory().exists()


@needs_squashfs
@pytest.mark.skipif(not shutil.which("bwrap"), reason="bubblewrap is needed")
@pytest.mark.skipif(os.geteuid() == 0, reason="snaps run as a regular user")
def test_a_strict_snap_runs_in_its_bubble(store, local_store):
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "hello-multipak.snap", engine.runner))
    options, argv = snap.plan_launch(store, tx, "hello", ["one", "two words"])
    result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "ARGS:--flag x1 one two words",
        "CHAIN:yes",
        "from the platform",
        "layout file",
        "BASE",
        "DATA",
    ]
    assert (store.snap_directory(tx.id) / "data/x1/file").read_text() == "data\n"


@pytest.mark.parametrize(
    "argument,expected",
    [
        ("snap://mindustry", "mindustry"),
        ("https://snapcraft.io/mindustry", "mindustry"),
        ("file:///home/user/My%20Apps/tool.snap", Path("/home/user/My Apps/tool.snap")),
        ("/home/user/tool.rpm", Path("/home/user/tool.rpm")),
    ],
)
def test_the_installer_opens_files_and_snap_links(argument, expected):
    pytest.importorskip("PySide6")
    from multipak.kirigami.live import installer_target

    assert installer_target(argument) == expected


@pytest.mark.parametrize(
    "argument,channel",
    [
        ("snap://firefox", ""),
        ("snap://firefox?channel=latest/edge", "latest/edge"),
        ("snap://firefox?channel=beta", "latest/beta"),
        ("snap://firefox?channel=esr/stable", "esr/stable"),
        ("https://snapcraft.io/firefox?channel=latest/candidate", "latest/candidate"),
        # A branch, or anything else, is not followed: the stable release is used.
        ("snap://firefox?channel=latest/edge/fix-1", ""),
        ("snap://firefox?channel=nightly", ""),
    ],
)
def test_the_channel_of_a_store_link_is_followed(argument, channel):
    pytest.importorskip("PySide6")
    from multipak.kirigami.live import installer_target

    target = installer_target(argument)
    assert target == "firefox" and target.channel == channel


def test_a_closed_risk_follows_the_next_more_stable_one():
    import io
    import json as json_module
    from types import SimpleNamespace

    def entry(track, risk, revision):
        return {
            "channel": {"architecture": "amd64", "track": track, "risk": risk},
            "revision": revision,
            "version": str(revision),
            "download": {"url": "https://x/f.snap", "sha3-384": "a" * 96, "size": 1},
        }

    data = {
        "channel-map": [
            entry("latest", "stable", 10),
            entry("latest", "beta", 12),
            entry("esr", "stable", 5),
        ]
    }

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    fake = SimpleNamespace(open=lambda request, timeout=30: Reply(json_module.dumps(data).encode()))
    release = snap_store.release("firefox", opener=fake, arch="amd64")
    assert (release.revision, release.channel) == ("10", "latest/stable")
    # Every published channel, to choose from: latest first, the most stable first.
    assert release.channels == (
        ("latest/stable", "10"),
        ("latest/beta", "12"),
        ("esr/stable", "5"),
    )
    # edge is closed: it follows beta, as with snapd; the channel stays latest/edge.
    release = snap_store.release("firefox", opener=fake, arch="amd64", channel="latest/edge")
    assert (release.revision, release.channel) == ("12", "latest/edge")
    release = snap_store.release("firefox", opener=fake, arch="amd64", channel="esr/stable")
    assert release.revision == "5"
    with pytest.raises(ValueError, match="channel 2.0/stable"):
        snap_store.release("firefox", opener=fake, arch="amd64", channel="2.0/stable")


# snapctl and hooks


def test_snapctl_keeps_configuration_like_snapd(tmp_path):
    from multipak import snap_ctl

    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n    plugs: [home]\n")
    state = snap_ctl.State(tmp_path / "state")

    def call(*arguments):
        return snap_ctl.answer(list(arguments), info, state)

    assert call("set", "name=Ana", "port=8080", "theme.dark=true") == (0, "")
    assert call("get", "name") == (0, "Ana\n")
    assert call("get", "port") == (0, "8080\n")
    assert call("get", "theme") == (0, '{\n\t"dark": true\n}\n')
    assert call("get", "-t", "name") == (0, '"Ana"\n')
    assert call("get", "missing") == (0, "")
    assert call("set", "-s", "port=8081") == (0, "")
    assert call("get", "-d", "port") == (0, '{\n\t"port": "8081"\n}\n')
    assert call("set", "theme.dark!") == (0, "")
    assert call("unset", "name") == (0, "")
    assert call("get", "name", "port") == (0, '{\n\t"port": "8081"\n}\n')
    assert call("set", "Bad_Key=1")[0] == 1
    # It survives between runs.
    assert snap_ctl.answer(["get", "port"], info, snap_ctl.State(tmp_path / "state")) == (
        0,
        "8081\n",
    )


def test_snapctl_answers_what_has_no_snapd_here(tmp_path):
    from multipak import snap_ctl

    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n    plugs: [home]\n")
    state = snap_ctl.State(tmp_path / "state")

    def call(*arguments):
        return snap_ctl.answer(list(arguments), info, state)

    assert call("is-connected", "home") == (0, "")
    assert call("is-connected", "camera") == (1, "")
    assert call("is-connected", "--list") == (0, "home\n")
    code, text = call("restart", "a.service")
    assert code == 1 and "cannot be managed from here" in text  # Never reported as done.
    assert call("system-mode") == (0, "system-mode: run\n")
    assert call("refresh", "--pending") == (0, "pending: none\n")
    assert call("set-health", "okay") == (0, "")
    code, text = call("install", "+models")
    assert code == 1 and "components" in text
    code, text = call("reboot", "--halt")
    assert code == 1 and "no equivalent" in text
    log = (tmp_path / "state/unsupported.log").read_text().splitlines()
    # Only requests with no equivalent are noted; services and components are
    # other matters.
    assert log == ['["reboot", "--halt"]']


def test_the_snapctl_client_talks_to_multipak(tmp_path):
    from multipak import snap_ctl

    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n")
    state = snap_ctl.State(tmp_path / "state")
    channel = tmp_path / "channel"
    client = tmp_path / "snapctl"
    client.write_text(snap_ctl.CLIENT.replace("/snap/.multipak/channel", str(channel)))
    with snap_ctl.Server(channel, info, state):
        assert subprocess.run(["sh", str(client), "set", "a=x y"]).returncode == 0
        result = subprocess.run(["sh", str(client), "get", "a"], capture_output=True, text=True)
        assert (result.returncode, result.stdout) == (0, "x y\n")
        result = subprocess.run(
            ["sh", str(client), "stop", "--now"], capture_output=True, text=True
        )
        assert result.returncode == 1 and "cannot be managed" in result.stderr
        result = subprocess.run(["sh", str(client), "model"], capture_output=True, text=True)
        assert result.returncode == 1 and "no equivalent" in result.stderr
        assert [p.name for p in channel.iterdir()] == ["requests"]
    assert not channel.exists()
    result = subprocess.run(["sh", str(client), "get", "a"], capture_output=True, text=True)
    assert result.returncode == 1 and "not available" in result.stderr


def test_a_reply_is_never_written_through_a_link(tmp_path):
    from multipak import snap_ctl

    target = tmp_path / "victim"
    target.write_text("untouched")
    call = tmp_path / "call.abcdef"
    call.mkdir()
    (call / "args").write_bytes(b"get\0a\0")
    (call / "reply").symlink_to(target)
    descriptor = os.open(call, os.O_RDONLY | os.O_DIRECTORY)
    try:
        snap_ctl._reply(descriptor, 0, "written")
    finally:
        os.close(descriptor)
    assert target.read_text() == "untouched"


def test_hook_settings_come_from_snap_yaml():
    info = snap_meta.parse(
        "name: a\napps:\n  a:\n    command: a\nhooks:\n  install:\n"
        "    command-chain: [bin/prepare]\n    environment:\n      MODE: first\n"
    )
    assert info.hook("install").command_chain == ("bin/prepare",)
    assert info.hook("install").environment == (("MODE", "first"),)
    assert info.hook("configure") == snap_meta.Hook("configure")


needs_bubble = [
    needs_squashfs,
    pytest.mark.skipif(not shutil.which("bwrap"), reason="bubblewrap is needed"),
    pytest.mark.skipif(os.geteuid() == 0, reason="snaps run as a regular user"),
]


def _apply(marks):
    def decorate(function):
        for mark in reversed(marks):
            function = mark(function)
        return function

    return decorate


@_apply(needs_bubble)
def test_hooks_run_as_root_and_prepare_the_configuration(store, local_store):
    from multipak import snap_ctl
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "hooked.snap", engine.runner))
    assert tx.status == "installed"
    data = store.snap_directory(tx.id) / "data"
    assert (data / "x1/hook-uid").read_text() == "0\n"
    assert (data / "x1/hook-home").read_text() == "/root/snap/hooked/x1\n"
    assert (store.snap_directory(tx.id) / "root-home/snap/hooked/x1/made-by-hook").exists()
    assert not (store.snap_directory(tx.id) / "root/snap/hooked").exists()
    assert (data / "common/configured").read_text() == "hello\n"
    # The app reads what the hook set, through the same snapctl.
    info, state = snap._context(store, tx)
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    with snap_ctl.Server(channel, info, state):
        options, argv = snap.plan_launch(store, tx, "read", [], channel=channel)
        result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
    assert result.stdout.splitlines() == ["hello", "42", "CONNECTED", "NO-COMPONENTS"]
    assert "components" in result.stderr
    # /etc is the base's, but the users are the computer's.
    options, argv = snap.plan_launch(store, tx, "users", [])
    result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
    import pwd

    assert pwd.getpwuid(os.getuid()).pw_name + ":" in result.stdout
    # Uninstalling runs the remove script first, and a failing one does not block it.
    remove = store.snap_directory(tx.id) / "root/meta/hooks/remove"
    remove.write_text('#!/bin/sh\nsnapctl get greeting > "$SNAP_COMMON/removed"\nexit 1\n')
    remove.chmod(0o755)
    calls = []
    original = snap.run_hook

    def recording(engine, tx, name, **options):
        calls.append(name)
        original(engine, tx, name, **options)

    snap.run_hook = recording
    try:
        engine.cleanup(tx)
    finally:
        snap.run_hook = original
    assert calls == ["remove"] and store.transactions() == []


@_apply(needs_bubble)
def test_a_failing_hook_stops_the_installation(store, local_store):
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    engine = Engine(store, threading.Event())
    with pytest.raises(RuntimeError, match="install script failed") as raised:
        engine.install_snap(inspect_package(local_store / "broken.snap", engine.runner))
    assert "cannot prepare" in str(raised.value)
    (tx,) = store.transactions()
    assert tx.status == "failed" and not tx.launchers


def test_the_store_lists_the_components_of_a_revision():
    reply = {
        "channel-map": [
            {
                "channel": {"architecture": "amd64", "risk": "stable", "track": "latest"},
                "revision": 5,
                "version": "2",
                "download": {
                    "url": "https://api.snapcraft.io/x.snap",
                    "sha3-384": "a" * 96,
                    "size": 10,
                },
                "resources": [
                    {
                        "name": "extra",
                        "type": "component/standard",
                        "revision": 3,
                        "version": "1",
                        "download": {
                            "url": "https://api.snapcraft.io/c",
                            "sha3-384": "b" * 96,
                            "size": 4,
                        },
                    },
                    {"name": "bad", "type": "component/standard", "download": {}},
                    "not a resource",
                ],
            }
        ]
    }
    opener = Opener({snap_store.API + "parts": json.dumps(reply).encode()})
    release = snap_store.release("parts", opener=opener, arch="amd64")
    (resource,) = release.resources
    assert (resource.name, resource.kind, resource.revision) == ("extra", "standard", "3")


@_apply(needs_bubble)
def test_chosen_components_are_installed_and_others_on_request(store, local_store):
    import dataclasses

    from multipak import snap_components, snap_ctl
    from multipak.engine import Engine
    from multipak.metadata import inspect_package
    from multipak.process import Runner

    engine = Engine(store, threading.Event())
    package = inspect_package(local_store / "parts.snap", engine.runner)
    resources = tuple(
        snap_store.Resource(name, "standard", revision, "1", "https://x", "0" * 96, 1)
        for name, revision in (("extra", "3"), ("more", "4"))
    )
    package = dataclasses.replace(package, snap_resources=resources)
    tx = engine.install_snap(package, ["extra", "drivers"])
    assert tx.snap_components == ["extra=3"]  # Kernel modules are never installed.
    assert any("more, drivers" in warning or "drivers" in warning for warning in tx.warnings)
    data = store.snap_directory(tx.id) / "data"
    assert (data / "x1/component-hooks").read_text() == "parts+extra\n"
    info, state = snap._context(store, tx)
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    requests = snap.ComponentRequests(store, tx.id, Runner(threading.Event(), lambda _: None))
    with snap_ctl.Server(channel, info, state, requests):
        options, argv = snap.plan_launch(store, tx, "show", [], channel=channel)
        result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    # The component asked for while the app runs appears at once.
    assert result.stdout.splitlines() == ["extra data", "more data"]
    (saved,) = store.transactions()
    assert saved.snap_components == ["extra=3", "more=4"]
    assert (data / "x1/component-hooks").read_text() == "parts+extra\nparts+more\n"
    assert snap_components.installed(saved) == {"extra": "3", "more": "4"}


# Services of the session and the system


def test_services_the_computer_lacks_are_named():
    from multipak import snap_services
    from multipak.snap_services import HostState

    interfaces = {"desktop", "calendar-service", "bluez", "audio-playback", "gsettings"}
    state = HostState(
        frozenset({"org.freedesktop.portal.Desktop", "org.freedesktop.Notifications"}),
        frozenset({"org.freedesktop.NetworkManager"}),
        frozenset({"/run/user/1000/pulse/native"}),
    )
    missing = snap_services.missing_features(interfaces, state, "/run/user/1000")
    assert missing == ["calendars (GNOME's evolution-data-server)", "Bluetooth"]
    assert snap_services.needs_settings_file(interfaces, state)
    with_dconf = HostState(state.session | {"ca.desrt.dconf"}, state.system, state.paths)
    assert not snap_services.needs_settings_file(interfaces, with_dconf)
    # Versioned names count, and nothing is reported when a bus could not be read.
    versioned = HostState(
        frozenset({"org.gnome.evolution.dataserver.Calendar8", "org.freedesktop.portal.Desktop"}),
        None,
        frozenset({"/run/user/1000/pulse/native"}),
    )
    assert snap_services.missing_features(interfaces, versioned, "/run/user/1000") == []
    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n")
    (warning,) = snap_meta.limitations(info, services=missing)
    assert "Bluetooth" in warning and "does not have the service" in warning


def test_the_host_is_asked_once_for_buses_and_paths(monkeypatch):
    from multipak import host, snap_services

    output = (
        "SESSION\n:1.4 1 x\norg.freedesktop.portal.Desktop 10 xdg-desktop-portal\n"
        "ca.desrt.dconf - - (activatable)\nSYSTEM\nFAILED\nPATHS\n/run/cups/cups.sock\n"
    )
    calls = []

    def run(argv, **options):
        calls.append(argv)
        return SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(host, "run", run)
    state = snap_services.probe("/run/user/1000")
    assert len(calls) == 1
    assert state.session == {"org.freedesktop.portal.Desktop", "ca.desrt.dconf"}
    assert state.system is None and state.paths == {"/run/cups/cups.sock"}


@_apply(needs_bubble)
def test_settings_go_to_a_file_when_dconf_is_missing(store, local_store):
    from multipak.engine import Engine
    from multipak.metadata import inspect_package
    from multipak.snap_services import HostState

    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "prefs.snap", engine.runner))
    for session, expected in ((frozenset(), "keyfile"), (frozenset({"ca.desrt.dconf"}), "")):
        state = HostState(session, frozenset(), frozenset())
        options, argv = snap.plan_launch(store, tx, "show", [], services=lambda _d, s=state: s)
        result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
        assert result.stdout.strip() == f"BACKEND:{expected}", result.stderr


@needs_squashfs
def test_device_nodes_are_left_out_and_other_special_files_refused(tmp_path):
    from multipak.process import CommandError, Runner

    runner = Runner(threading.Event(), lambda _line: None)
    source = tmp_path / "source"
    (source / "dev").mkdir(parents=True)
    (source / "meta").mkdir()
    (source / "meta/snap.yaml").write_text("name: core26\ntype: base\n")
    (source / "dev/ordinary").write_text("kept\n")
    image = tmp_path / "base.snap"
    devices = ["-p", "dev/null c 666 root root 1 3", "-p", "dev/console c 600 root root 5 1"]
    subprocess.run(
        ["mksquashfs", str(source), str(image), "-noappend", "-quiet", *devices],
        check=True,
        capture_output=True,
    )
    assert sorted(snap.special_files(runner, image)) == ["dev/console", "dev/null"]
    snap.extract(runner, image, tmp_path / "out")
    assert not (tmp_path / "out/dev/null").exists()
    assert (tmp_path / "out/dev/ordinary").read_text() == "kept\n"
    # A special file anywhere else is not expected in a snap.
    odd = tmp_path / "odd.snap"
    subprocess.run(
        [
            "mksquashfs",
            str(source),
            str(odd),
            "-noappend",
            "-quiet",
            "-p",
            "queue i 644 root root f",
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError, match="outside /dev"):
        snap.extract(runner, odd, tmp_path / "odd")
    # Any error unsquashfs reports stops the extraction.
    broken = tmp_path / "broken.snap"
    broken.write_bytes(b"hsqs" + bytes(4092))
    with pytest.raises((CommandError, ValueError)):
        snap.extract(runner, broken, tmp_path / "broken")


@needs_squashfs
def test_a_wrong_or_incomplete_exclusion_list_never_extracts_wrongly(tmp_path, monkeypatch):
    from multipak.process import CommandError, Runner

    runner = Runner(threading.Event(), lambda _line: None)
    source = tmp_path / "source"
    (source / "dev").mkdir(parents=True)
    (source / "meta").mkdir()
    (source / "meta/snap.yaml").write_text("name: core26\ntype: base\n")
    image = tmp_path / "base.snap"
    devices = ["-p", "dev/null c 666 root root 1 3", "-p", "dev/zero c 666 root root 1 5"]
    subprocess.run(
        ["mksquashfs", str(source), str(image), "-noappend", "-quiet", *devices],
        check=True,
        capture_output=True,
    )
    (source / "meta/kept").write_text("kept\n")
    subprocess.run(
        ["mksquashfs", str(source), str(image), "-noappend", "-quiet", *devices],
        check=True,
        capture_output=True,
    )
    # A name that is not in the image never leaves out anything else: newer unsquashfs
    # stops (-match), 4.7.5 extracts everything apart from the devices.
    listed = ["dev/null", "dev/zero", "dev/missing"]
    monkeypatch.setattr(snap, "listing", lambda _runner, _path: (listed, 0))
    try:
        snap.extract(runner, image, tmp_path / "wrong")
    except CommandError:
        assert not (tmp_path / "wrong/meta/kept").exists()
    else:
        assert (tmp_path / "wrong/meta/kept").read_text() == "kept\n"
        assert not (tmp_path / "wrong/dev/null").exists()
    # A device left off the list cannot be created and fails the extraction as well.
    monkeypatch.setattr(snap, "listing", lambda _runner, _path: (["dev/null"], 0))
    with pytest.raises(CommandError):
        snap.extract(runner, image, tmp_path / "incomplete")


def test_the_base_is_not_listed_as_a_dependency():
    from multipak.kirigami.main import Welcome
    from multipak.models import Package

    def welcome(text, missing):
        package = Package(Path("app.snap"), "snap", "app", "1", "amd64", snap=snap_meta.parse(text))
        result = Welcome(package=package)
        result.set_snap_details("", missing)
        return result

    alone = welcome("name: hello\nbase: core\napps:\n  hello:\n    command: hello\n", ["core"])
    assert alone.snapRequirementsText == ""
    assert "16.04 (core)" in alone.snapEnvironmentExplanation
    assert "download the base" in alone.snapEnvironmentExplanation
    graphical = welcome(
        "name: game\nbase: core22\napps:\n  game:\n    command: game\n"
        "plugs:\n  gnome:\n    interface: content\n    target: $SNAP/gnome-platform\n"
        "    default-provider: gnome-42-2204\n",
        ["core22", "gnome-42-2204"],
    )
    assert graphical.snapRequirementsText == "• gnome-42-2204"
    assert "core22" not in graphical.snapRequirementsText


# zenity, shown by MultiPak


def test_zenity_command_lines_as_apps_write_them():
    from multipak import snap_dialog

    # How tinyfiledialogs (Mindustry, among others) asks for a file.
    request = snap_dialog.parse(
        [
            "--file-selection",
            "--multiple",
            "--title=Import map",
            "--filename=/home/u/maps/",
            "--file-filter=Maps | *.msav *.mmap",
            "--file-filter=All files | *",
        ]
    )
    assert request.mode == "file-selection" and "multiple" in request.flags
    assert request.value("title") == "Import map"
    assert request.value("filename") == "/home/u/maps/"
    assert [snap_dialog.name_filter(f) for f in request.lists["file-filter"]] == [
        "Maps (*.msav *.mmap)",
        "All files (*)",
    ]
    assert snap_dialog.name_filter("*.png  *.jpg") == "*.png *.jpg (*.png *.jpg)"
    # The other way of giving a value, and options it ignores.
    request = snap_dialog.parse(["--question", "--text", "Sure?", "--attach=12", "--no-wrap"])
    assert (request.mode, request.value("text")) == ("question", "Sure?")
    assert snap_dialog.run(["--version"]) == (0, "4.0.1\n", True)
    code, text, output = snap_dialog.run(["--list", "--column=A"])
    assert (code, output) == (255, False) and "not available" in text
    for wrong in (["--info", "--error"], ["--title=x"], ["--entry", "--text"]):
        assert snap_dialog.run(wrong)[0] == 255


def test_the_zenity_client_passes_dialogs_to_multipak(tmp_path):
    from multipak import snap_ctl

    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n")
    state = snap_ctl.State(tmp_path / "state")
    channel = tmp_path / "channel"
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in snap_ctl.CLIENT_NAMES:
        (tools / name).write_text(snap_ctl.CLIENT.replace("/snap/.multipak/channel", str(channel)))
        (tools / name).chmod(0o755)
    calls = []

    def dialogs(arguments):
        calls.append(arguments)
        if "--extra-button=Later" in arguments:
            return 1, "Later\n", True
        if "--title=Cancel me" in arguments:
            return 1, "", True
        return 0, "/home/u/map one.msav\n", True

    zenity = str(tools / "zenity")
    with snap_ctl.Server(channel, info, state, dialogs=dialogs):
        result = subprocess.run(
            [zenity, "--file-selection", "--title=Open"], capture_output=True, text=True
        )
        assert (result.returncode, result.stdout) == (0, "/home/u/map one.msav\n")
        result = subprocess.run(
            [zenity, "--file-selection", "--title=Cancel me"], capture_output=True, text=True
        )
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")
        result = subprocess.run(
            [zenity, "--question", "--extra-button=Later"], capture_output=True, text=True
        )
        assert (result.returncode, result.stdout) == (1, "Later\n")
        # snapctl, the same client under its other name, still reaches snapctl.
        result = subprocess.run([str(tools / "snapctl"), "get", "a"], capture_output=True)
        assert result.returncode == 0
    assert calls[0] == ["--file-selection", "--title=Open"] and len(calls) == 3
    # Where nothing can be shown, such as a hook, zenity says so instead of hanging.
    with snap_ctl.Server(channel, info, state):
        result = subprocess.run([zenity, "--info"], capture_output=True, text=True)
        assert result.returncode == 1 and "cannot be shown" in result.stderr


def test_zenity_file_selection_is_shown_with_qt(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog

    from multipak import snap_dialog

    application = QApplication.instance() or QApplication([])
    (tmp_path / "map.msav").write_text("map")
    (tmp_path / "notes.txt").write_text("notes")

    def choose(name):
        dialog = next(w for w in application.topLevelWidgets() if isinstance(w, QFileDialog))
        assert dialog.windowTitle() == "Import map"
        assert dialog.nameFilters() == ["Maps (*.msav)", "All files (*)"]
        if name:
            dialog.selectFile(str(tmp_path / name))
            dialog.accept()
        else:
            dialog.reject()

    arguments = [
        "--file-selection",
        "--title=Import map",
        f"--filename={tmp_path}/",
        "--file-filter=Maps | *.msav",
        "--file-filter=All files | *",
    ]
    QTimer.singleShot(0, lambda: choose("map.msav"))
    result = snap_dialog.show(snap_dialog.parse(arguments))
    assert result == (0, f"{tmp_path}/map.msav\n", True)
    QTimer.singleShot(0, lambda: choose(""))
    assert snap_dialog.show(snap_dialog.parse(arguments)) == (1, "", True)


# Permissions Firefox declares


FIREFOX_PLUGS = """name: browser
version: '1'
apps:
  run:
    command: bin/run
    plugs: [etc-firefox, hunspell, host-hunspell, system-packages-doc]
plugs:
  etc-firefox:
    interface: system-files
    read: [/etc/firefox]
  hunspell:
    interface: system-files
    read: [/var/lib/snapd/hostfs/usr/share/hunspell]
  host-hunspell:
    interface: mount-control
    mount:
    - what: /usr/share/hunspell
      where: $SNAP_COMMON/host-hunspell
      persistent: true
      options: [ro, bind, noatime, noexec]
"""


def test_declaring_system_files_mounts_or_docs_does_not_refuse_a_snap():
    info = snap_meta.parse(FIREFOX_PLUGS)
    assert snap_meta.refusal(info, "x86_64") == ""
    assert snap_meta.limitations(info) == []
    granted, invalid = snap_meta.system_files(info)
    assert granted == (
        ("/etc/firefox", False),
        ("/var/lib/snapd/hostfs/usr/share/hunspell", False),
    )
    assert invalid == ()


def test_system_files_asked_for_writing_stay_read_only_and_say_so():
    text = FIREFOX_PLUGS.replace("read: [/etc/firefox]", "write: [/etc/firefox, /run/thing]")
    info = snap_meta.parse(text)
    assert snap_meta.read_only_requests(info) == ("/etc/firefox",)  # /run is writable.
    (message,) = snap_meta.limitations(info)
    assert "/etc/firefox" in message and "read-only" in message


@pytest.mark.parametrize(
    "path",
    [
        "etc/relative",
        "/etc/../shadow",
        "/etc/./x",
        "/etc//x",
        "/etc/x/",
        "/etc/x@",
        "/etc/*",
        "/etc/fire{fox,bird}",
        "/etc/@{HOME}",
        "/etc/a^b",
        "$HOME/x",
        "/home/$HOME/x",
        "~/x",
        "/etc/~x",
        "/" + "x" * 600,
    ],
)
def test_system_files_snapd_would_refuse_refuse_the_snap(path):
    text = FIREFOX_PLUGS.replace("read: [/etc/firefox]", f"read: ['{path}']")
    assert "would not accept" in snap_meta.refusal(snap_meta.parse(text), "x86_64")


def test_system_files_are_shown_read_only_without_following_the_computers_links(tmp_path):
    import dataclasses

    request = request_for(tmp_path)
    (tmp_path / "base/etc/alt").symlink_to("/usr/alt")
    request = dataclasses.replace(
        request,
        system_files=(
            "/etc/firefox",
            "/etc/linked",
            "/etc/through/policies.json",
            "/etc/deep/conf/x.conf",
            "/etc/alt/conf",
            "/etc/missing",
        ),
    )
    listings = {
        **HOST,
        "/etc": [
            Entry("firefox", "d"),
            Entry("linked", "l", "/var/home/user/secret"),
            Entry("through", "l", "../usr/lib/ff"),
            Entry("deep", "d"),
            Entry("alt", "d"),
        ],
        "/etc/deep": [Entry("conf", "d")],
        "/etc/deep/conf": [Entry("x.conf", "f")],
        "/etc/alt": [Entry("conf", "d")],
    }
    arguments = build(request, listings.get, EXISTS.__contains__)[0]
    order = pairs(arguments)
    assert ("--ro-bind", "/etc/firefox") in order
    assert ("--ro-bind", "/etc/deep/conf/x.conf") in order
    # Links stay links, resolved inside the snap's view: never the computer's target.
    assert ("--symlink", "/etc/linked") in order
    assert ("--symlink", "/etc/through") in order
    assert "/var/home/user/secret" not in [
        arguments[i + 1] for i, a in enumerate(arguments) if a == "--ro-bind"
    ]
    destinations = [d for _o, d in order if d]
    assert "/etc/through/policies.json" not in destinations
    # A link of the base in the way does not move the path elsewhere.
    assert not [d for d in destinations if d.startswith(("/usr/alt", "/etc/alt/"))]
    assert "/etc/missing" not in destinations
    assert not [o for o, d in order if o == "--bind" and d and d.startswith("/etc")]


def test_snapctl_mount_never_reports_a_mount_it_did_not_make(tmp_path):
    from multipak import snap_ctl

    info = snap_meta.parse(FIREFOX_PLUGS)
    state = snap_ctl.State(tmp_path / "state")
    for request in (
        ["mount", "-o", "ro,bind", "/usr/share/hunspell", "/var/snap/browser/common/h"],
        ["umount", "/var/snap/browser/common/h"],
    ):
        code, text = snap_ctl.answer(request, info, state)
        assert code != 0 and "nothing was changed" in text
    log = (tmp_path / "state/unsupported.log").read_text().splitlines()
    assert len(log) == 2


@_apply(needs_bubble)
def test_the_bubble_keeps_what_a_browser_sandbox_needs(store, local_store):
    from multipak import snap_ctl
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    if subprocess.run(["unshare", "--user", "true"], capture_output=True).returncode:
        pytest.skip("this computer does not allow user namespaces")
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "browser.snap", engine.runner))
    assert tx.status == "installed" and tx.limitations
    info, state = snap._context(store, tx)
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    with snap_ctl.Server(channel, info, state):
        options, argv = snap.plan_launch(store, tx, "run", [], channel=channel)
        result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
    lines = result.stdout.splitlines()
    # Firefox's own sandbox makes user namespaces and installs a seccomp filter: the
    # bubble must allow the first and must not have taken the second already.
    assert "NESTED-UID:0" in lines, result.stderr
    assert "Seccomp:0" in lines
    assert "SYSTEM-FILE-READ" in lines and "SYSTEM-FILE-READ-ONLY" in lines
    assert "MOUNT-REFUSED" in lines and "NOTHING-MOUNTED" in lines
    assert "nothing was changed" in result.stderr


def test_system_packages_doc_shows_the_computers_documentation_read_only(tmp_path):
    import dataclasses

    plain_request = request_for(tmp_path)
    request = dataclasses.replace(plain_request, interfaces=frozenset({"system-packages-doc"}))
    listings = {**HOST, "/usr/share": [Entry("doc", "d"), Entry("man", "d")]}
    exists = EXISTS | {"/usr/share/doc", "/usr/share/man"}
    order = pairs(build(request, listings.get, exists.__contains__)[0])
    assert ("--ro-bind", "/usr/share/doc") in order and ("--ro-bind", "/usr/share/man") in order
    plain = pairs(build(plain_request, listings.get, exists.__contains__)[0])
    assert ("--ro-bind", "/usr/share/doc") not in plain


@needs_squashfs
def test_downloading_dependencies_reports_how_far_it_got(store, local_store, monkeypatch):
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    copy = snap_store.download

    def download(release, target, progress=lambda *_: None, **kwargs):
        progress(0, 200)
        progress(100, 200)
        copy(release, target, progress=progress, **kwargs)
        progress(200, 200)

    monkeypatch.setattr(snap_store, "download", download)
    engine = Engine(store, threading.Event())
    reported = []
    engine.progress = lambda value, text: reported.append((value, text))
    tx = engine.install_snap(inspect_package(local_store / "hello-multipak.snap", engine.runner))
    assert tx.status == "installed"
    values = [v for v, text in reported if "Downloading" in text or "Preparing" in text]
    # Two dependencies, each with its share between 30 and 80, never indeterminate.
    assert values and -1 not in values
    assert values == sorted(values) and 30 <= values[0] and values[-1] <= 80
    assert len(set(values)) > 3


# Snaps with services, duplicates and data left behind

WITH_SERVICE = (
    "name: browser\nversion: '1'\napps:\n  browser:\n    command: bin/browser\n"
    "  daemon:\n    command: bin/daemon\n    daemon: simple\n"
)


class FakeSystemd:
    """systemctl and systemd-run as seen from MultiPak: the units that run."""

    def __init__(self, fail=()):
        self.calls, self.active, self.fail = [], set(), set(fail)

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[0] == "systemd-run":
            name = next(item[7:] for item in argv if item.startswith("--unit="))
            timer = any(item.startswith("--on-calendar=") for item in argv)
            name += ".timer" if timer else ".service"
            if name in self.fail:
                return 1, "Failed to start transient service unit\n"
            self.active.add(name)
            return 0, ""
        action, name = argv[2], argv[3]
        if action == "list-units":
            wanted = argv[argv.index("--") + 1 :]
            return 0, "".join(
                f"{item} loaded active running x\n" for item in wanted if item in self.active
            )
        if action == "is-active":
            return (0, "active\n") if name in self.active else (3, "inactive\n")
        if action == "stop":
            # As systemctl: a unit it does not have refuses the whole request;
            # otherwise every unit is stopped that can be and the first error told.
            missing = [item for item in argv[3:] if item not in self.active]
            if len(argv) > 4 and missing:
                return 5, f"Failed to enqueue jobs: Unit {missing[0]} not loaded.\n"
            code, output = 0, ""
            for item in argv[3:]:
                if item in self.fail:
                    code, output = code or 1, output or "Access denied\n"
                    continue
                self.active.discard(item)
            return code, output
        raise AssertionError(argv)


def _served():
    from multipak.models import Transaction

    info = snap_meta.parse(
        "name: served\napps:\n  open:\n    command: bin/open\n"
        "  web:\n    command: bin/web\n    daemon: simple\n    before: [db-sync]\n"
        "  db-sync:\n    command: bin/sync\n    daemon: oneshot\n"
        "  db:\n    command: bin/db\n    daemon: simple\n    before: [web]\n"
        "  cron:\n    command: bin/cron\n    daemon: simple\n    install-mode: disable\n"
        "  tick:\n    command: bin/tick\n    daemon: dbus\n"
    )
    tx = Transaction("a" * 32, "", "", "served", "1", "snap", "", method="snap")
    return tx, info


def test_services_are_read_with_their_order_and_what_they_lack():
    from multipak import snap_daemons

    _tx, info = _served()
    assert snap_meta.refusal(info, "x86_64") == ""
    assert [app.name for app in snap_meta.applications(info)] == ["open"]
    assert snap_meta.runnable_services(info) == ("web", "db-sync", "db", "cron")
    assert snap_meta.idle_services(info) == ("tick (dbus)",)
    running, idle = snap_meta.limitations(info)
    assert "user account" in running and "1024" in running and "tick (dbus)" in idle
    assert snap_daemons.ordered(info) == ("db", "cron", "web", "db-sync")
    assert snap_daemons.ordered(info, ["db-sync", "db"]) == ("db-sync", "db")
    loop = snap_meta.parse(
        "name: l\napps:\n  a:\n    command: a\n    daemon: simple\n    after: [b]\n"
        "  b:\n    command: b\n    daemon: simple\n    after: [a]\n"
    )
    with pytest.raises(ValueError, match="loop"):
        snap_daemons.ordered(loop)
    with pytest.raises(ValueError, match="after"):
        snap_meta.parse("name: x\napps:\n  a:\n    command: a\n    after: [nothing]\n")
    with pytest.raises(ValueError, match="daemon"):
        snap_meta.parse("name: x\napps:\n  a:\n    command: a\n    daemon: always\n")


def test_services_start_as_the_users_systemd_units_in_order(monkeypatch):
    from multipak import snap_daemons

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    tx, info = _served()
    systemd = FakeSystemd()
    services = snap_daemons.Services(tx, info, systemd)
    services.start_enabled()
    started = [argv for argv in systemd.calls if argv[0] == "systemd-run"]
    units = [next(i[7:] for i in argv if i.startswith("--unit=")) for argv in started]
    prefix = f"multipak-snap-{tx.id}-"
    # cron is install-mode: disable, tick's timer is not understood: neither starts.
    assert units == [prefix + name for name in ("db", "web", "db-sync")]
    web, sync = started[1], started[2]
    assert web[-5:] == ["multipak-snap", "--", "--service", tx.id, "web"]
    assert {"--user", "--collect", "--property=Restart=on-failure"} <= set(web)
    assert f"--property=Before={prefix}db-sync.service" in web
    assert f"--property=After={prefix}web.service" not in web
    assert {"--property=Type=oneshot", "--property=RemainAfterExit=yes"} <= set(sync)
    # Already running: not started twice.
    systemd.calls.clear()
    services.act("start", ["served.web"])
    assert not any(argv[0] == "systemd-run" for argv in systemd.calls)
    table = services.table([])
    assert "served.web      enabled   active    -" in table
    assert "served.cron     disabled  inactive  -" in table
    assert "served.tick     enabled   inactive  dbus" in table
    # Stopped with one request, so that systemd stops them at the same time, in the
    # reverse order where they have one.
    systemd.calls.clear()
    services.act("stop", ["served"])
    (stopped,) = [argv[3:] for argv in systemd.calls if argv[2] == "stop"]
    # cron never started (install-mode: disable): not asked.
    assert stopped == [prefix + n + ".service" for n in ("db-sync", "web", "db")]
    with pytest.raises(ValueError, match="unknown service"):
        services.act("start", ["other.web"])
    with pytest.raises(ValueError, match="cannot run these services yet"):
        services.act("start", ["served.tick"])


def test_service_requests_from_scripts_wait_and_failures_are_reported(monkeypatch):
    from multipak import snap_ctl, snap_daemons

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    tx, info = _served()
    systemd = FakeSystemd()
    deferred = snap_daemons.Services(tx, info, systemd, deferred=True)
    state = snap_ctl.State("/nonexistent")
    assert snap_ctl.answer(["restart", "served.web"], info, state, daemons=deferred) == (0, "")
    assert not any(argv[0] == "systemd-run" for argv in systemd.calls)
    unit = f"multipak-snap-{tx.id}-web.service"
    refusing = FakeSystemd(fail={unit})
    failing = snap_daemons.Services(tx, info, refusing)
    code, text = snap_ctl.answer(["start", "served.web"], info, state, daemons=failing)
    assert code == 1 and "Failed to start" in text
    refusing.active.add(unit)  # Running, but it will not stop.
    code, text = snap_ctl.answer(["stop", "served.web"], info, state, daemons=failing)
    assert code == 1 and "Access denied" in text


def test_a_service_that_ended_does_not_keep_the_others_from_stopping(monkeypatch):
    # systemctl refuses a whole stop request if one unit is not loaded, such as
    # Nextcloud's nextcloud-fixer once it has ended: only loaded units are asked.
    from multipak import snap_daemons

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    tx, info = _served()
    prefix = f"multipak-snap-{tx.id}-"
    systemd = FakeSystemd()
    services = snap_daemons.Services(tx, info, systemd)
    services.start_enabled()
    systemd.active.discard(prefix + "db-sync.service")  # Ended and collected.
    systemd.calls.clear()
    services.stop_all()
    assert systemd.active == set()
    (stopped,) = [argv[3:] for argv in systemd.calls if argv[2] == "stop"]
    assert stopped == [prefix + n + ".service" for n in ("web", "db")]
    # One that will not stop: the rest still stop, one at a time, and it is told.
    services.start_enabled()
    systemd.fail.add(prefix + "db.service")
    with pytest.raises(RuntimeError, match="Could not stop") as raised:
        services.stop_all()
    assert str(raised.value).splitlines()[1:] == ["db: Access denied"]
    assert systemd.active == {prefix + "db.service"}
    # Nothing loaded: nothing to ask.
    systemd.active.clear()
    systemd.calls.clear()
    services.stop_all()
    assert not [argv for argv in systemd.calls if argv[2] == "stop"]


def _service_script_kills():
    return [line.strip() for line in snap.SERVICE.splitlines() if line.strip().startswith("kill")]


def test_the_service_script_signals_every_process_in_a_way_dash_understands():
    # dash, the shell of the bases, takes "kill -s TERM -1" for the option -1 and
    # signals nothing: the service then waited for its whole stop timeout.
    negative = [line for line in _service_script_kills() if re.search(r"\s-\d", line)]
    assert negative and all(" -- -" in line for line in negative)


@pytest.mark.skipif(
    not (shutil.which("dash") and shutil.which("bwrap")), reason="dash and bubblewrap are needed"
)
@pytest.mark.skipif(os.geteuid() == 0, reason="snaps run as a regular user")
def test_a_service_without_stop_mode_ends_at_once_under_dash(tmp_path):
    control = tmp_path / "control"
    os.mkfifo(control)
    script = tmp_path / "service.sh"
    script.write_text(snap.SERVICE.replace(snap.CONTROL, str(control)), encoding="utf-8")
    dash = shutil.which("dash")
    # A loop that waits in a foreground command, as the Nextcloud snap's services do.
    child = [dash, "-c", "while :; do sleep 300; done"]
    process = subprocess.Popen(
        ["bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--die-with-parent", "--"]
        + [dash, str(script), *child],
        env={**os.environ, "MULTIPAK_STOP_ALL": "1"},
    )
    try:
        time.sleep(0.5)
        started = time.monotonic()
        assert snap.send_control(control, "stop TERM") or _retry_control(control)
        process.wait(timeout=10)
        assert time.monotonic() - started < 5
    finally:
        process.kill()
        process.wait()


def _retry_control(control):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if snap.send_control(control, "stop TERM"):
            return True
        time.sleep(0.1)
    return False


@_apply(needs_bubble)
def test_a_service_runs_in_the_apps_bubble_and_uninstalling_stops_it(
    store, local_store, monkeypatch
):
    from multipak import snap_ctl, snap_daemons
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    systemd = FakeSystemd()
    monkeypatch.setattr(snap, "runner_call", lambda runner: systemd)
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "served.snap", engine.runner))
    assert tx.status == "installed"
    # The configure script's restart waited; then what snapd starts was started.
    started = [argv for argv in systemd.calls if argv[0] == "systemd-run"]
    assert [argv[-1] for argv in started] == ["web"]
    assert any("user account" in warning for warning in tx.warnings)
    assert any("tick (dbus)" in warning for warning in tx.warnings)
    info, state = snap._context(store, tx)
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    services = snap_daemons.Services(tx, info, systemd)
    with snap_ctl.Server(channel, info, state, daemons=services):
        options, argv = snap.plan_launch(store, tx, "web", [], channel=channel, service=True)
        result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = store.snap_directory(tx.id) / "data" / tx.snap_revision
    # As root, as snapd runs services, but only in the bubble.
    assert (data / "ran").read_text() == f"0 web /root/snap/served/{tx.snap_revision}\n"
    assert "served.web" in result.stdout and "active" in result.stdout
    with pytest.raises(RuntimeError, match="cannot run this service yet"):
        snap.plan_launch(store, tx, "tick", [], service=True)
    with pytest.raises(RuntimeError, match="background services"):
        snap.plan_launch(store, tx, "web", [])
    # Stopping fails and the service still runs: nothing is removed, the snap stays
    # installed, and uninstalling can be tried again.
    web = f"multipak-snap-{tx.id}-web.service"
    systemd.fail.add(web)
    with pytest.raises(snap.ServicesStillRunning, match="still running: web"):
        engine.cleanup(tx)
    (kept,) = store.transactions()
    assert kept.status == "installed" and (data / "ran").exists()
    systemd.fail.discard(web)
    engine.cleanup(kept)
    assert store.transactions() == []
    # Stopping fails but nothing runs any more: the snap is removed.
    tx = engine.install_snap(inspect_package(local_store / "served.snap", engine.runner))
    systemd.active.clear()
    systemd.fail.add(f"multipak-snap-{tx.id}-web.service")
    engine.cleanup(tx)
    assert store.transactions() == []


@_apply(needs_bubble)
def test_apps_services_and_hooks_of_a_snap_share_its_tmp(store, local_store, monkeypatch):
    # Nextcloud's PHP finds the sockets MySQL and Redis leave in /tmp only if all
    # of the snap's services see the same /tmp, as with snapd.
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    monkeypatch.setattr(snap, "runner_call", lambda runner: FakeSystemd())
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "served.snap", engine.runner))
    tmp = snap.private_tmp(tx)
    plans = {
        "service": snap.plan_launch(store, tx, "web", [], service=True)[0],
        "app": snap.plan_launch(store, tx, "open", [])[0],
        "hook": snap.plan_launch(store, tx, None, [], hook="configure")[0],
    }
    for kind, options in plans.items():
        index = options.index(str(tmp))
        assert options[index - 1 : index + 2] == ["--bind", str(tmp), "/tmp"], kind
    service = ["bwrap", *plans["service"], "--", "/bin/sh", "-c", "echo up > /tmp/server.sock"]
    assert subprocess.run(service, capture_output=True, text=True).returncode == 0
    app = ["bwrap", *plans["app"], "--", "/bin/sh", "-c", "cat /tmp/server.sock"]
    result = subprocess.run(app, capture_output=True, text=True)
    assert result.stdout == "up\n", result.stderr
    # Uninstalling removes it with the snap.
    engine.cleanup(tx)
    assert not tmp.parent.exists()


@_apply(needs_bubble)
def test_an_app_runs_as_administrator_only_in_its_bubble(store, local_store, monkeypatch):
    # Nextcloud's occ and manual-install refuse to run unless they are root, as with
    # sudo and snapd: asked for, the app runs as root of its own user namespace.
    from multipak import snap_ctl
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    monkeypatch.setattr(snap, "runner_call", lambda runner: FakeSystemd())
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "served.snap", engine.runner))
    info, state = snap._context(store, tx)
    assert snap.admin_apps(info) == ["open"]
    assert snap.admin_label(info, "open") == "served.open"
    assert snap.admin_label(info, "served") == "served"
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    data = store.snap_directory(tx.id) / "data" / tx.snap_revision
    with snap_ctl.Server(channel, info, state):
        options, argv = snap.plan_launch(store, tx, "open", ["a b"], channel=channel, admin=True)
        # The app's own command, with its arguments; not a hook's.
        assert argv[-2:] == [f"/snap/served/{tx.snap_revision}/bin/web", "a b"]
        # (Its snapctl services call fails here: no services server in this test.)
        subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
        assert (data / "ran").read_text() == f"0  /root/snap/served/{tx.snap_revision}\n"
        probe = ["/bin/sh", "-c", 'echo "$(id -u) $USER"; cat /proc/self/uid_map']
        result = subprocess.run(["bwrap", *options, "--", *probe], capture_output=True, text=True)
        lines = result.stdout.split("\n")
        assert lines[0] == "0 root", result.stderr
        # Root in the bubble is the user on the computer: nothing more.
        assert lines[1].split() == ["0", str(os.getuid()), "1"]
        # Without asking, the same app runs as the user.
        options, argv = snap.plan_launch(store, tx, "open", [], channel=channel)
        subprocess.run(["bwrap", *options, "--", *argv], capture_output=True)
        assert (data / "ran").read_text().startswith(f"{os.getuid()} ")
    with pytest.raises(RuntimeError, match="background services"):
        snap.plan_launch(store, tx, "web", [], admin=True)
    with pytest.raises(ValueError):
        snap.plan_launch(store, tx, None, [], hook="configure", admin=True)
    engine.cleanup(tx)


def test_classic_snaps_are_never_run_as_administrator(monkeypatch):
    info = snap_meta.parse(
        "name: wide\nconfinement: classic\napps:\n  wide:\n    command: w\n"
        "  daemon:\n    command: d\n    daemon: simple\n"
    )
    assert snap.admin_apps(info) == []
    confined = dataclasses.replace(info, confinement="strict")
    assert snap.admin_apps(confined) == ["wide"]


def test_apps_with_a_window_are_never_run_as_administrator(tmp_path):
    # Firefox as root would leave files only root can change in the snap's data.
    from multipak.models import Transaction
    from multipak.store import Store

    info = snap_meta.parse(
        "name: cloud\napps:\n  cloud:\n    command: c\n  occ:\n    command: o\n"
        "  viewer:\n    command: v\n    desktop: usr/share/applications/viewer.desktop\n"
    )
    store = Store(tmp_path / "data", tmp_path / "applications")
    tx = Transaction(
        "a" * 32,
        "",
        "",
        "cloud",
        "1",
        "snap",
        "a" * 64,
        method="snap",
        status="installed",
        launchers=["multipak-" + "a" * 32 + "-snap-cloud.desktop"],
    )
    path = store.launcher(tx, tx.launchers[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[Desktop Entry]\nType=Application\nName=Cloud\nExec=multipak-snap -- {tx.id} cloud %U\n"
    )
    graphical = snap.graphical_apps(store, tx)
    assert graphical == {"cloud"}
    # The menu shortcut and the desktop file of snap.yaml both mean a window.
    assert snap.admin_apps(info, graphical) == ["occ"]


@pytest.mark.parametrize(
    "text,values,delay",
    [
        ("10:00", ["*-*-* 10:00:00"], 0),
        ("mon-fri,10:00", ["Mon..Fri *-*-* 10:00:00"], 0),
        ("9:00~11:00", ["*-*-* 09:00:00"], 7199),
        ("23:00-01:00", ["*-*-* 23:00:00"], 7199),
        ("00:00-24:00/4", [f"*-*-* {h:02d}:00:00" for h in (0, 6, 12, 18)], 0),
        ("mon1,23:00,,sun", ["Mon *-*-01..07 23:00:00", "Sun *-*-* 00:00:00"], 86399),
    ],
)
def test_snap_timers_become_calendar_events(text, values, delay):
    from multipak.snap_timer import calendar

    assert calendar(text) == (values, delay)


@pytest.mark.parametrize("text", ["", "someday", "25:00", "mon5,10:00", "10:00/2", "mon,,"])
def test_timers_that_cannot_be_translated_are_refused(text):
    from multipak.snap_timer import calendar

    with pytest.raises(ValueError):
        calendar(text)


def test_timers_and_kinds_of_services_become_systemd_settings(monkeypatch):
    from multipak import snap_daemons
    from multipak.models import Transaction

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    info = snap_meta.parse(
        "name: k\napps:\n"
        "  rotate:\n    command: r\n    daemon: oneshot\n    timer: 9:00~11:00\n"
        "  notifier:\n    command: n\n    daemon: notify\n"
        "  old:\n    command: o\n    daemon: forking\n"
    )
    tx = Transaction("b" * 32, "", "", "k", "1", "snap", "", method="snap")
    systemd = FakeSystemd()
    services = snap_daemons.Services(tx, info, systemd)
    services.start_enabled()
    rotate, notifier, old = (argv for argv in systemd.calls if argv[0] == "systemd-run")
    assert "--on-calendar=*-*-* 09:00:00" in rotate
    assert "--timer-property=RandomizedDelaySec=7199" in rotate
    # Run by the timer, again and again: no RemainAfterExit, no restarting.
    assert "--property=Type=oneshot" in rotate
    assert not any("RemainAfterExit" in item or "Restart=" in item for item in rotate)
    for argv in (notifier, old):
        assert not any(item.startswith("--property=Type=") for item in argv)
        assert "--property=Restart=on-failure" in argv
    timer = f"multipak-snap-{tx.id}-rotate.timer"
    assert systemd.active >= {timer} and services.state("rotate") == "active"
    # A timer's service is loaded with it.
    systemd.active.add(f"multipak-snap-{tx.id}-rotate.service")
    systemd.calls.clear()
    services.stop(["rotate"])
    # The timer first, so that it does not start the service again.
    assert [argv[3:] for argv in systemd.calls if argv[2] == "stop"] == [
        [timer, f"multipak-snap-{tx.id}-rotate.service"]
    ]


def test_socket_addresses_follow_what_snapd_allows():
    assert snap_meta.socket_address("$SNAP_DATA/a.sock", "system") == ("unix", "$SNAP_DATA/a.sock")
    assert snap_meta.socket_address("8080", "system") == ("tcp", "", 8080)
    assert snap_meta.socket_address("127.0.0.1:8080", "system") == ("tcp", "127.0.0.1", 8080)
    assert snap_meta.socket_address("[::1]:9000", "user") == ("tcp", "::1", 9000)
    for listen, scope in (
        ("80", "system"),
        ("$SNAP_USER_DATA/a", "system"),
        ("$SNAP_DATA/../x", "system"),
        ("@abstract", "system"),
        ("$XDG_RUNTIME_DIR/a", "system"),
    ):
        with pytest.raises(ValueError):
            snap_meta.socket_address(listen, scope)


@_apply(needs_bubble)
def test_forking_socket_and_user_services_run_as_snapd_runs_them(
    store, local_store, monkeypatch, tmp_path
):
    from multipak import snap_ctl
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    monkeypatch.setattr(snap, "_home", lambda: Path.home())
    systemd = FakeSystemd()
    monkeypatch.setattr(snap, "runner_call", lambda runner: systemd)
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "kinds.snap", engine.runner))
    info, state = snap._context(store, tx)
    data = store.snap_directory(tx.id) / "data"
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    # User services start with the session, through the desktop's autostart.
    autostart = snap._autostart_path(tx).read_text()
    assert f'"--services" "login" "{tx.id}"' in autostart
    # System services too: the user's systemd forgets them when the computer restarts.
    assert "Name=MultiPak snap kinds: forked, listener, mine" in autostart
    with snap_ctl.Server(channel, info, state):
        # forking: the bubble waits for what the service started.
        options, argv = snap.plan_launch(store, tx, "forked", [], channel=channel, service=True)
        assert "--unshare-pid" in options
        result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert (data / tx.snap_revision / "forked").read_text() == "done\n"
        # A socket: listening outside, handed to the service as 3 by its first name.
        app = info.app("listener")
        sockets = snap._listen(store, tx, info, app)
        try:
            path = data / "common/control.sock"
            assert path.is_socket() and path.stat().st_mode & 0o777 == 0o600
            options, argv = snap.plan_launch(
                store, tx, "listener", [], channel=channel, service=True, activation=["control"]
            )
            status, stopped = snap._supervise(["bwrap", *options, "--", *argv], sockets)
        finally:
            for item in sockets:
                item.close()
        assert (status, stopped) == (0, False)
        assert (data / "common/activated").read_text() == "1 control\n"
        # A user service runs as the user, with the user's snap folders.
        options, argv = snap.plan_launch(store, tx, "mine", [], channel=channel, service=True)
        subprocess.run(["bwrap", *options, "--", *argv], check=True)
    mine = Path.home() / "snap/kinds/common/mine"
    assert mine.read_text() == f"{os.getuid()} {Path.home()}/snap/kinds/{tx.snap_revision}\n"
    engine.cleanup(tx)
    assert not snap._autostart_path(tx).exists()


def test_what_follows_a_notify_service_waits_until_it_is_ready(store, monkeypatch):
    import socket

    from multipak import snap_daemons
    from multipak.models import Transaction

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    info = snap_meta.parse(
        "name: n\napps:\n  db:\n    command: d\n    daemon: notify\n    before: [web]\n"
        "  web:\n    command: w\n    daemon: simple\n"
    )
    tx = Transaction("c" * 32, "", "", "n", "1", "snap", "", method="snap")
    channel = store.snap_directory(tx.id) / "channel"
    channel.mkdir(parents=True)
    listener = snap._NotifyListener(store, tx, "db", channel)
    systemd = FakeSystemd()
    checked = []

    def call(argv):
        result = systemd(argv)
        if argv[0] == "systemctl" and argv[3].endswith("-db.service"):
            checked.append(argv)
            if len(checked) == 3:
                # The service says it is ready, through the socket in the bubble.
                # (Inside, the path is short; here, through the folder's descriptor.)
                folder = os.open(channel, os.O_RDONLY)
                sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                sender.sendto(b"STATUS=loading\nREADY=1\n", f"/proc/self/fd/{folder}/notify")
                sender.close()
                os.close(folder)
                deadline = time.monotonic() + 5
                while not snap_daemons.ready_path(store, tx, "db").exists():
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
        return result

    services = snap_daemons.Services(tx, info, call, store=store)
    monkeypatch.setattr(snap_daemons.time, "sleep", lambda _s: None)
    services.start(["db", "web"])
    started = [argv[-1] for argv in systemd.calls if argv[0] == "systemd-run"]
    assert started == ["db", "web"]
    # web was started only after db said it was ready.
    first_web = next(i for i, argv in enumerate(systemd.calls) if argv[-1] == "web")
    assert first_web > systemd.calls.index(checked[-1])
    assert services.state("db") == "active"
    listener.close()
    assert services.state("db") == "activating"


def test_a_notify_service_that_never_says_ready_does_not_block_forever(store, monkeypatch):
    from multipak import snap_daemons
    from multipak.models import Transaction

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    info = snap_meta.parse(
        "name: n\napps:\n  db:\n    command: d\n    daemon: notify\n"
        "  web:\n    command: w\n    daemon: simple\n    after: [db]\n"
    )
    tx = Transaction("d" * 32, "", "", "n", "1", "snap", "", method="snap")
    now = [0.0]
    monkeypatch.setattr(snap_daemons.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    systemd = FakeSystemd()
    services = snap_daemons.Services(tx, info, systemd, store=store, clock=lambda: now[0])
    services.start_enabled()
    assert [argv[-1] for argv in systemd.calls if argv[0] == "systemd-run"] == ["db", "web"]
    assert now[0] >= snap_daemons.READY_SECONDS


@_apply(needs_bubble)
def test_the_snaps_system_users_are_in_its_bubble(store, local_store, monkeypatch):
    import pwd

    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    # The ID mapping itself needs newuidmap: apps run as the user without it.
    monkeypatch.setattr(snap, "user_mapping", lambda info: [])
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "users.snap", engine.runner))
    options, argv = snap.plan_launch(store, tx, "list", [])
    result = subprocess.run(["bwrap", *options, "--", *argv], capture_output=True, text=True)
    assert "snap_daemon:x:584788:584788::/nonexistent:/usr/bin/false" in result.stdout
    assert "snap_daemon:x:584788:" in result.stdout
    # And the computer's users are still there.
    assert pwd.getpwuid(os.getuid()).pw_name + ":" in result.stdout


def test_snapds_service_settings_reach_systemd_and_the_bubble(monkeypatch):
    from multipak import snap_daemons
    from multipak.models import Transaction

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    info = snap_meta.parse(
        "name: s\napps:\n"
        "  db:\n    command: bin/db --x\n    daemon: simple\n    stop-timeout: 1m30s\n"
        "    restart-condition: on-abort\n    restart-delay: 2.5s\n    stop-mode: sighup\n"
        "    stop-command: bin/stop now\n    post-stop-command: bin/after\n"
        "    reload-command: bin/reload\n    command-chain: [bin/chain]\n"
        "  job:\n    command: bin/job\n    daemon: oneshot\n    start-timeout: 10s\n"
        "    restart-condition: never\n"
    )
    db = info.app("db")
    assert (db.stop_timeout, db.restart_delay, db.stop_mode) == (90, 2.5, "sighup")
    tx = Transaction("f" * 32, "", "", "s", "1", "snap", "", method="snap")
    systemd = FakeSystemd()
    snap_daemons.Services(tx, info, systemd).start_enabled()
    started = {argv[-1]: argv for argv in systemd.calls if argv[0] == "systemd-run"}
    assert "--property=TimeoutStopSec=105" in started["db"]
    assert "--property=Restart=on-failure" in started["db"]
    assert "--property=RestartSec=2.5" in started["db"]
    assert "--property=TimeoutStartSec=10" in started["job"]
    assert "--property=Restart=no" in started["job"]
    env = {"SNAP": "/snap/s/1"}
    extra = snap._service_environment(info, db, env)
    assert extra["MULTIPAK_STOP"] == "/snap/s/1/bin/chain /snap/s/1/bin/stop now"
    assert extra["MULTIPAK_POST_STOP"] == "/snap/s/1/bin/chain /snap/s/1/bin/after"
    assert extra["MULTIPAK_RELOAD"] == "/snap/s/1/bin/chain /snap/s/1/bin/reload"
    # sighup: only the main process; without stop-mode, all of them, as systemd does.
    assert extra["MULTIPAK_STOP_ALL"] == ""
    assert snap._service_environment(info, info.app("job"), env)["MULTIPAK_STOP_ALL"] == "1"
    for field, text in (
        ("stop-timeout", "soon"),
        ("restart-condition", "sometimes"),
        ("stop-mode", "sigkill"),
        ("stop-command", "../out"),
    ):
        with pytest.raises(ValueError, match=field):
            snap_meta.parse(f"name: s\napps:\n  a:\n    command: a\n    {field}: {text}\n")


def test_session_bus_names_start_user_services_only():
    session = (
        "slots:\n  name:\n    interface: dbus\n    bus: session\n    name: org.example.Svc\n"
        "  system:\n    interface: dbus\n    bus: system\n    name: org.example.Sys\n"
    )
    info = snap_meta.parse(
        "name: s\napps:\n"
        "  mine:\n    command: a\n    daemon: simple\n    daemon-scope: user\n"
        "    activates-on: [name]\n"
        "  bus:\n    command: b\n    daemon: dbus\n    daemon-scope: user\n"
        "    bus-name: org.example.Bus\n"
        "  other:\n    command: c\n    daemon: simple\n    daemon-scope: user\n"
        "    activates-on: [system]\n"
        "  root:\n    command: d\n    daemon: dbus\n    bus-name: org.example.Root\n" + session
    )
    assert info.app("mine").bus_names == ("org.example.Svc",)
    assert info.app("bus").bus_names == ("org.example.Bus",)
    # The system bus takes services only from the administrator.
    assert info.app("other").unsupported == ("activates-on",)
    assert info.app("root").unsupported == ("dbus",)


@_apply(needs_bubble)
def test_a_service_stops_with_its_commands_and_signal(store, local_store, monkeypatch):
    from multipak import snap_ctl
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    monkeypatch.setattr(snap, "runner_call", lambda runner: FakeSystemd())
    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "stopping.snap", engine.runner))
    info, state = snap._context(store, tx)
    app = info.app("daemon")
    data = store.snap_directory(tx.id) / "data" / tx.snap_revision
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    with snap_ctl.Server(channel, info, state):
        os.mkfifo(channel / "control", 0o600)
        options, argv = snap.plan_launch(store, tx, "daemon", [], channel=channel, service=True)
        run = snap._ServiceRun(["bwrap", *options, "--", *argv], [], channel, app, None)
        deadline = time.monotonic() + 10
        while not (data / "started").exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert snap.send_control(channel / "control", "reload")
        while not (data / "reloaded").exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        run.request_stop()
        status, _stopped = run.run()
    # stop-command first, then the signal (HUP, from stop-mode), then post-stop.
    assert (data / "log").read_text().splitlines() == ["stop-command", "got HUP", "post-stop"]
    assert status == 0


@pytest.mark.parametrize(
    "settings,ready",
    [
        # Never says it is ready within its start timeout.
        ("    daemon: notify\n    start-timeout: 300ms\n", False),
        # Ready, but misses its watchdog.
        ("    daemon: simple\n    watchdog-timeout: 300ms\n", True),
    ],
)
def test_a_service_that_fails_its_timeouts_is_stopped_as_failed(tmp_path, settings, ready):
    from types import SimpleNamespace

    info = snap_meta.parse(f"name: s\napps:\n  d:\n    command: d\n{settings}")
    notify = SimpleNamespace(is_ready=ready, last_ping=None)
    # No control channel here: it is ended for good at once.
    run = snap._ServiceRun(["sleep", "30"], [], tmp_path, info.app("d"), notify)
    started = time.monotonic()
    status, stopped = run.run()
    # A failure, so that systemd restarts it as its restart-condition says.
    assert (status, stopped) == (1, False)
    assert time.monotonic() - started < 10


def test_session_bus_names_are_written_and_removed_with_the_snap(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from multipak.models import Transaction

    monkeypatch.setattr(snap, "_home", lambda: tmp_path)
    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    info = snap_meta.parse(
        "name: s\napps:\n  mine:\n    command: a\n    daemon: dbus\n"
        "    daemon-scope: user\n    bus-name: org.example.Mine\n"
        "  taken:\n    command: b\n    daemon: dbus\n    daemon-scope: user\n"
        "    bus-name: org.example.Taken\n"
    )
    tx = Transaction("a" * 32, "", "", "s", "1", "snap", "", method="snap")
    calls, logs = [], []
    engine = SimpleNamespace(runner=SimpleNamespace(run=lambda argv, **_k: calls.append(argv)))
    engine._log = logs.append
    services = tmp_path / ".local/share/dbus-1/services"
    services.mkdir(parents=True)
    (services / "org.example.Taken.service").write_text("[D-BUS Service]\nName=other\n")
    snap._write_bus_services(engine, tx, info)
    text = (services / "org.example.Mine.service").read_text()
    assert "Name=org.example.Mine" in text
    assert f'"--services" "start" "{tx.id}" "mine"' in text
    # Another program's name is left alone.
    assert "Name=other" in (services / "org.example.Taken.service").read_text()
    assert any("org.example.Taken" in line for line in logs)
    assert calls and calls[-1][-1] == "ReloadConfig"
    snap._remove_bus_services(engine, tx)
    assert sorted(p.name for p in services.iterdir()) == ["org.example.Taken.service"]


@needs_squashfs
def test_a_snap_is_installed_only_once(store, local_store, monkeypatch):
    from multipak.engine import Engine
    from multipak.kirigami import live
    from multipak.metadata import inspect_package

    engine = Engine(store, threading.Event())
    package = inspect_package(local_store / "hello-multipak.snap", engine.runner)
    tx = engine.install_snap(package)
    assert snap.installed_snap(store, "hello-multipak").id == tx.id
    with pytest.raises(ValueError, match="already installed"):
        engine.install_snap(inspect_package(local_store / "hello-multipak.snap", engine.runner))
    assert [t.id for t in store.transactions()] == [tx.id]
    # By name, nothing is downloaded again.
    monkeypatch.setattr(snap_store, "release", lambda *_a, **_k: pytest.fail("downloaded"))
    with pytest.raises(ValueError, match="already installed"):
        live.download_snap(engine, "hello-multipak")
    engine.cleanup(tx)
    assert snap.installed_snap(store, "hello-multipak") is None


@needs_squashfs
def test_uninstalling_moves_the_apps_data_to_the_trash(store, local_store, monkeypatch, tmp_path):
    from urllib.parse import unquote

    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    engine = Engine(store, threading.Event())
    for attempt in (1, 2):
        tx = engine.install_snap(
            inspect_package(local_store / "hello-multipak.snap", engine.runner)
        )
        data = home / "snap/hello-multipak/x1"
        data.mkdir(parents=True)
        (data / "settings").write_text(f"attempt {attempt}\n")
        engine.cleanup(tx)
        assert not (home / "snap/hello-multipak").exists()
    trash = home / ".local/share/Trash"
    first, second = trash / "files/hello-multipak", trash / "files/hello-multipak.2"
    assert (first / "x1/settings").read_text() == "attempt 1\n"
    assert (second / "x1/settings").read_text() == "attempt 2\n"
    info = (trash / "info/hello-multipak.trashinfo").read_text()
    assert unquote(info.split("Path=")[1].splitlines()[0]) == str(home / "snap/hello-multipak")
    # A link in place of the folder is left alone, never followed.
    tx = engine.install_snap(inspect_package(local_store / "hello-multipak.snap", engine.runner))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / "snap/hello-multipak").symlink_to(elsewhere)
    engine.cleanup(tx)
    assert elsewhere.is_dir() and (home / "snap/hello-multipak").is_symlink()


def test_system_files_snapd_accepts_are_accepted():
    for path in ("/opt/My App (2)", "/etc/fire-fox.d/policies.json", "/var/lib/a,b=c"):
        text = FIREFOX_PLUGS.replace("read: [/etc/firefox]", f"read: ['{path}']")
        assert snap_meta.refusal(snap_meta.parse(text), "x86_64") == "", path


@_apply(needs_bubble)
def test_a_system_file_the_base_lacks_can_be_mounted(tmp_path):
    # The base has /etc/ld.so.conf.d but not the computer's file in it: the folder is
    # read-only, so its mount point must be prepared, or bubblewrap refuses to start.
    from multipak.snap_sandbox import local_entries

    host = Path("/etc/ld.so.conf.d")
    files = sorted(p.name for p in host.iterdir() if p.is_file()) if host.is_dir() else []
    if not files:
        pytest.skip("this computer has no /etc/ld.so.conf.d files")
    wanted = f"/etc/ld.so.conf.d/{files[0]}"
    base = tmp_path / "base"
    minimal_base(base)
    (base / "etc/ld.so.conf.d").mkdir(parents=True, exist_ok=True)
    (base / "etc/ld.so.conf.d/base.conf").write_text("base\n")
    app = tmp_path / "app"
    (app / "meta").mkdir(parents=True)
    for name in ("data", "common", "home"):
        (tmp_path / name).mkdir()
    request = Request(
        snap=Mount("hello", "x1", str(app)),
        base=Mount("core24", "7", str(base)),
        classic=False,
        providers=(),
        content=(),
        layouts=(),
        data=str(tmp_path / "data"),
        common=str(tmp_path / "common"),
        uid=os.getuid(),
        home=str(tmp_path / "home"),
        environment=(("PATH", "/usr/bin"),),
        argv=(),
        workdir="/",
        system_files=(wanted,),
    )
    arguments, _ = build(request, local_entries, os.path.lexists)
    script = f"cat {wanted} > /dev/null && cat /etc/ld.so.conf.d/base.conf"
    result = subprocess.run(
        ["bwrap", *arguments, "--", "/usr/bin/sh", "-c", script], capture_output=True, text=True
    )
    assert (result.returncode, result.stdout) == (0, "base\n"), result.stderr


def test_package_text_is_never_read_as_markup():
    from multipak.kirigami.main import Welcome, _message_html
    from multipak.models import Package

    for field in ("architectures: ['<b>x</b>']", "type: <i>x</i>"):
        with pytest.raises(ValueError, match="invalid"):
            snap_meta.parse(f"name: a\n{field}\napps:\n  a:\n    command: a\n")
    # Whatever a message holds, the page shows it as it is.
    assert _message_html('<img src="http://e.invalid/x"> & a\nb') == (
        "<p>&lt;img src=&quot;http://e.invalid/x&quot;&gt; &amp; a<br>b</p>"
    )
    text = FIREFOX_PLUGS.replace("read: [/etc/firefox]", "read: ['/etc/<a href=x>*</a>']")
    package = Package(Path("a.snap"), "snap", "browser", "1", "amd64", snap=snap_meta.parse(text))
    welcome = Welcome(package=package)
    welcome.set_snap_details(snap_meta.refusal(package.snap, "x86_64"), [])
    assert "<a href" not in welcome.snapRefusal and "&lt;a href=x&gt;" in welcome.snapRefusal


def test_user_open_passes_on_only_what_snapd_allows(tmp_path):
    from multipak import snap_ctl, snap_open

    home = tmp_path / "home"
    (home / "docs").mkdir(parents=True)
    (home / "docs/a b.pdf").write_text("pdf")
    (home / "out").symlink_to("/etc")
    assert snap_open.target("https://example.org/x?y=1", home) == "https://example.org/x?y=1"
    assert snap_open.target("mailto:a@example.org", home) == "mailto:a@example.org"
    assert snap_open.target(f"{home}/docs/a b.pdf", home) == str(home / "docs/a b.pdf")
    assert snap_open.target(f"file://{home}/docs/a%20b.pdf", home) == str(home / "docs/a b.pdf")
    for refused in (
        "javascript:alert(1)",
        "smb://server/share",
        "file:///etc/passwd",
        "/etc/passwd",
        f"{home}/out/passwd",  # A link out of the home folder.
        f"{home}/missing.pdf",
        "docs/a b.pdf",
        "https://example.org/\nx",
    ):
        with pytest.raises(ValueError):
            snap_open.target(refused, home)
    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n")
    state = snap_ctl.State(tmp_path / "state")
    opened = []
    opener = lambda text: (opened.append(text) or 0, "")  # noqa: E731
    assert snap_ctl.answer(["user-open", "https://e.org"], info, state, opener=opener) == (0, "")
    assert opened == ["https://e.org"]
    assert snap_ctl.answer(["user-open", "a", "b"], info, state, opener=opener)[0] == 1
    code, text = snap_ctl.answer(["user-open", "https://e.org"], info, state)
    assert code == 1 and "nothing can be opened" in text


def test_the_channel_answers_a_limited_number_of_requests_at_once(tmp_path, monkeypatch):
    from multipak import snap_ctl

    monkeypatch.setattr(snap_ctl, "MAX_REQUESTS", 1)
    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n")
    state = snap_ctl.State(tmp_path / "state")
    channel = tmp_path / "channel"
    zenity = tmp_path / "zenity"
    zenity.write_text(snap_ctl.CLIENT.replace("/snap/.multipak/channel", str(channel)))
    zenity.chmod(0o755)
    waiting, release = threading.Event(), threading.Event()

    def dialogs(_arguments):
        waiting.set()
        release.wait(10)
        return 0, "/home/u/x\n", True

    with snap_ctl.Server(channel, info, state, dialogs=dialogs):
        first = subprocess.Popen([str(zenity), "--file-selection"], stdout=subprocess.PIPE)
        assert waiting.wait(10)
        second = subprocess.run([str(zenity), "--info"], capture_output=True, text=True)
        assert second.returncode == 1 and "too many requests" in second.stderr
        release.set()
        assert first.communicate(timeout=10)[0] == b"/home/u/x\n"
        # The app's leftovers, even unreadable, do not outlive the run.
        (channel / "junk/deep").mkdir(parents=True)
        (channel / "junk").chmod(0)
    assert not channel.exists()


@needs_squashfs
def test_a_marker_that_is_a_link_is_not_taken_for_the_file(tmp_path):
    from multipak.process import Runner

    runner = Runner(threading.Event(), lambda _line: None)
    source = tmp_path / "source"
    (source / "meta").mkdir(parents=True)
    (source / "meta/snap.yaml").symlink_to("/etc/hostname")
    image = tmp_path / "linked.snap"
    subprocess.run(
        ["mksquashfs", str(source), str(image), "-noappend", "-quiet"],
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError, match="does not contain"):
        snap.extract(runner, image, tmp_path / "out")


def test_system_files_need_lists_of_paths_as_snapd_requires():
    for plug in (
        "  f:\n    interface: system-files\n    read: /etc/firefox\n",
        "  f:\n    interface: system-files\n",
        "  f:\n    interface: system-files\n    write: [[/etc/x]]\n",
    ):
        text = f"name: a\napps:\n  a:\n    command: a\n    plugs: [f]\nplugs:\n{plug}"
        assert "would not accept" in snap_meta.refusal(snap_meta.parse(text), "x86_64"), plug
    bare = "name: a\napps:\n  a:\n    command: a\n    plugs: [system-files]\n"
    assert "would not accept" in snap_meta.refusal(snap_meta.parse(bare), "x86_64")


def test_the_log_never_holds_environment_values():
    from multipak.process import shown_command

    argv = ["bwrap", "--setenv", "GITHUB_TOKEN", "secret", "--ro-bind", "/a", "/b", "--setenv"]
    assert shown_command(argv) == [
        "bwrap",
        "--setenv",
        "GITHUB_TOKEN",
        "…",
        "--ro-bind",
        "/a",
        "/b",
        "--setenv",
    ]


def test_snapctl_set_cannot_grow_the_configuration_without_end(tmp_path):
    from multipak import snap_ctl

    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n")
    state = snap_ctl.State(tmp_path / "state")
    assert snap_ctl.answer(["set", "a=" + "x" * 1000], info, state)[0] == 0
    code, text = snap_ctl.answer(["set", "b=" + "y" * (snap_ctl.MAX_CONFIG + 1)], info, state)
    assert code == 1 and "too large" in text
    assert snap_ctl.answer(["get", "b"], info, state) == (0, "")  # Nothing was kept.


def test_a_pipe_in_place_of_the_request_does_not_hold_the_channel(tmp_path):
    from multipak import snap_ctl

    info = snap_meta.parse("name: a\napps:\n  a:\n    command: a\n")
    state = snap_ctl.State(tmp_path / "state")
    channel = tmp_path / "channel"
    with snap_ctl.Server(channel, info, state) as server:
        call = channel / "call.abcdefgh"
        call.mkdir()
        os.mkfifo(call / "args")
        os.mkfifo(call / "reply")
        reader = os.open(call / "reply", os.O_RDONLY | os.O_NONBLOCK)
        try:
            thread = threading.Thread(target=server._handle, args=(call.name,))
            server._slots.acquire()
            thread.start()
            thread.join(5)
            assert not thread.is_alive()
            assert b"unreadable request" in os.read(reader, 4096)
        finally:
            os.close(reader)


def test_a_client_that_stops_reading_does_not_hold_the_reply(tmp_path, monkeypatch):
    from multipak import snap_ctl

    monkeypatch.setattr(snap_ctl, "REPLY_SECONDS", 1)
    call = tmp_path / "call"
    call.mkdir()
    os.mkfifo(call / "reply")
    reader = os.open(call / "reply", os.O_RDONLY | os.O_NONBLOCK)
    directory_fd = os.open(call, os.O_RDONLY | os.O_DIRECTORY)
    try:
        started = time.monotonic()
        snap_ctl._reply(directory_fd, 0, "x" * (1024 * 1024))  # Far more than a pipe holds.
        assert time.monotonic() - started < 5
    finally:
        os.close(reader)
        os.close(directory_fd)


@needs_squashfs
def test_metadata_behind_a_linked_folder_is_not_read(tmp_path):
    root = tmp_path / "root"
    (root / "real/gui").mkdir(parents=True)
    (root / "real/gui/app.desktop").write_text("[Desktop Entry]\nExec=app\n")
    (root / "real/snap.yaml").write_text("name: a\n")
    (root / "meta").symlink_to("real")
    assert not snap.regular_file(root, "meta/snap.yaml")
    with pytest.raises(ValueError):
        snap.read_metadata(root, "meta/snap.yaml")
    (root / "meta").unlink()
    (root / "meta").mkdir()
    (root / "meta/snap.yaml").write_text("name: a\n")
    (root / "meta/gui").symlink_to("../real/gui")
    assert snap.read_metadata(root, "meta/snap.yaml") == "name: a\n"
    info = snap_meta.parse("name: a\napps:\n  app:\n    command: app\n")
    tx = Transaction("t1", "", "", "a", "1", "snap", "", method="snap", snap_revision="x1")

    class Store:
        def launcher(self, _tx, name):
            return tmp_path / name

        def save(self, _tx):
            pass

    snap._desktops(Store(), tx, info, root)
    assert tx.launchers == []  # A linked meta/gui is never read.


def test_layout_sources_are_never_created_through_a_link(tmp_path):
    data = tmp_path / "data"
    (data / "inner").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (data / "out").symlink_to(outside)
    assert snap._without_links(data, data / "inner/new/deeper")
    assert not snap._without_links(data, data / "out/new")
    assert not snap._without_links(data, tmp_path / "elsewhere")


def test_content_paths_at_the_same_target_are_renamed_as_snapd_does(tmp_path):
    # Without "source:", every path goes at the target; snapd then renames the later
    # ones target-2, target-3 (interfaces/mount/spec.go, unclashMountEntries).
    info, _tx = installed(
        "name: a\napps:\n  a:\n    command: a\nplugs:\n  lib:\n    interface: content\n"
        "    target: $SNAP/lib\n    default-provider: libs\n"
    )
    provider = tmp_path / "libs"
    for name in ("one", "two", "three"):
        (provider / name).mkdir(parents=True)
    (provider / "meta").mkdir()
    (provider / "meta/snap.yaml").write_text(
        "name: libs\nslots:\n  lib:\n    interface: content\n"
        "    read: [$SNAP/one, $SNAP/two, $SNAP/one, $SNAP/three]\n"
    )
    variables = {"SNAP": "/snap/a/x1", "SNAP_DATA": "/var/snap/a/x1", "SNAP_COMMON": "/c"}
    mounts = snap.content_mounts(info, {"libs": Mount("libs", "5", str(provider))}, variables)
    assert mounts == (
        ("/snap/a/x1/lib", str(provider / "one")),
        ("/snap/a/x1/lib-2", str(provider / "two")),
        ("/snap/a/x1/lib-3", str(provider / "three")),
    )


@needs_squashfs
def test_named_icons_are_shown_to_the_desktop_and_removed_with_the_snap(store, local_store):
    from multipak.engine import Engine
    from multipak.metadata import inspect_package

    engine = Engine(store, threading.Event())
    tx = engine.install_snap(inspect_package(local_store / "hello-multipak.snap", engine.runner))
    icons = store.applications.parent / "icons"
    exported = sorted(str(p.relative_to(icons)) for p in icons.rglob("*") if p.is_file())
    assert exported == [
        "hicolor/48x48/apps/snap.hello-multipak.hello.png",
        "hicolor/scalable/apps/snap.hello-multipak.hello.svg",
    ]
    engine.cleanup(tx)
    assert not [p for p in icons.rglob("*") if p.is_file()]


def test_downloads_and_extractions_check_the_free_space_first(tmp_path, monkeypatch):
    from collections import namedtuple

    from multipak import storage
    from multipak.storage import InsufficientSpace

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _p: usage(10, 10, 1024**2))
    release = snap_store.Release(
        "big", "1", "1", "https://e.invalid/big", "0" * 96, 5 * 1024**3, "", ""
    )
    with pytest.raises(InsufficientSpace, match="5120 MB are needed"):
        snap_store.download(release, tmp_path / "big.snap", opener=object())
    assert not list(tmp_path.iterdir())


@needs_squashfs
def test_extraction_needs_room_for_the_whole_snap(tmp_path, monkeypatch):
    from collections import namedtuple

    from multipak import storage
    from multipak.process import Runner
    from multipak.storage import InsufficientSpace

    runner = Runner(threading.Event(), lambda _line: None)
    source = tmp_path / "source"
    (source / "meta").mkdir(parents=True)
    (source / "meta/snap.yaml").write_text("name: a\n")
    (source / "data").write_bytes(b"x" * 3 * 1024**2)
    image = tmp_path / "a.snap"
    subprocess.run(
        ["mksquashfs", str(source), str(image), "-noappend", "-quiet"],
        check=True,
        capture_output=True,
    )
    special, size = snap.listing(runner, image)
    assert special == [] and size >= 3 * 1024**2
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _p: usage(10, 10, 1024**2))
    with pytest.raises(InsufficientSpace):
        snap.extract(runner, image, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_requests_from_a_running_snap_wait_for_another_installation(store):
    held = threading.Event()
    release = threading.Event()

    def other_installation():
        with store.lock():
            held.set()
            release.wait(5)

    thread = threading.Thread(target=other_installation)
    thread.start()
    held.wait(5)
    # An installation started now still fails at once, as before.
    with pytest.raises(RuntimeError, match="already in progress"):
        with store.lock():
            pass
    threading.Timer(0.6, release.set).start()
    started = time.monotonic()
    with store.lock(wait=10):
        assert time.monotonic() - started >= 0.5
    thread.join()


def test_the_review_offers_the_store_channels(store, monkeypatch):
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from multipak.kirigami.live import LiveInstaller

    QCoreApplication.instance() or QApplication([])
    installer = LiveInstaller("snap://firefox", store=store)
    installer.package = SimpleNamespace(
        snap_channels=(("latest/stable", "160.0-1"), ("latest/edge", "162.0a1")),
        snap_channel="latest/stable",
    )
    assert [item["label"] for item in installer.snapChannels] == [
        "latest/stable · 160.0-1",
        "latest/edge · 162.0a1",
    ]
    assert installer.snapChannelIndex == 0
    asked = []
    monkeypatch.setattr(
        installer, "_run", lambda operation, handler, mode: asked.append((installer.path, mode))
    )
    installer.chooseSnapChannel("latest/stable")  # The one shown: nothing to do.
    installer.chooseSnapChannel("esr/stable")  # Not published: ignored.
    assert asked == []
    # Another channel: downloaded on the review page itself, not from the start.
    installer.chooseSnapChannel("latest/edge")
    ((path, mode),) = asked
    assert (path, path.channel, mode) == ("firefox", "latest/edge", "channel")
    assert installer._channel_previous == "firefox"


def test_a_snap_downloaded_before_is_not_downloaded_again(tmp_path):
    payload = b"hsqs" + b"x" * 100
    opener = Opener(
        {
            snap_store.API + "hello": store_reply(payload),
            "https://api.snapcraft.io/download/hello_42.snap": payload,
        }
    )
    release = snap_store.release("hello", opener=opener, arch="amd64")
    target = snap_store.download(release, tmp_path / "hello_42.snap", opener=opener)
    asked = len(opener.requests)
    assert snap_store.download(release, target, opener=opener) == target
    assert len(opener.requests) == asked  # Kept: the same revision of another channel.
    target.write_bytes(b"hsqs" + b"y" * 100)  # Changed since: fetched again.
    snap_store.download(release, target, opener=opener)
    assert target.read_bytes() == payload and len(opener.requests) == asked + 1


def test_stopping_goes_through_the_launcher_and_restarts_are_limited(store, monkeypatch):
    from multipak import snap_daemons
    from multipak.models import Transaction

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    info = snap_meta.parse("name: s\napps:\n  web:\n    command: w\n    daemon: simple\n")
    tx = Transaction("a" * 32, "", "", "s", "1", "snap", "", method="snap")
    systemd = FakeSystemd()
    snap_daemons.Services(tx, info, systemd).start(["web"])
    (argv,) = [item for item in systemd.calls if item[0] == "systemd-run"]
    assert f'--property=ExecStop="multipak-snap" "--" "--services" "halt" "{tx.id}" "web"' in argv
    assert {"--property=StartLimitBurst=5", "--property=RestartSec=2"} <= set(argv)
    # halt: the launcher is asked to end, and waited for.
    running = snap.running_path(store, tx, "web")
    running.parent.mkdir(parents=True)
    running.write_text("channel")
    halt = snap.halt_path(store, tx, "web")
    threading.Timer(0.3, running.unlink).start()
    snap._halt(store, tx, info.app("web"))
    assert halt.exists() and not running.exists()
    assert snap._ending(halt, None)


def test_the_launcher_runs_an_app_as_administrator_only_with_its_option(monkeypatch, capsys):
    import sys

    from multipak import snap_launcher

    calls = []
    monkeypatch.setattr(
        snap_launcher,
        "launch",
        lambda tx, app, args, **options: calls.append((tx, app, args, options)) or 7,
    )
    monkeypatch.setattr(
        sys, "argv", ["multipak-snap", "--", "--admin", "t" * 32, "occ", "status", "-v"]
    )
    assert snap_launcher.main() == 7
    assert calls == [("t" * 32, "occ", ["status", "-v"], {"admin": True})]
    assert "root inside the snap's sandbox, never on your computer" in capsys.readouterr().err
    for wrong in (["--admin"], ["--admin", "t" * 32], ["--admin", "--x", "occ"]):
        monkeypatch.setattr(sys, "argv", ["multipak-snap", *wrong])
        assert snap_launcher.main() == 2
    monkeypatch.setattr(sys, "argv", ["multipak-snap", "t" * 32, "occ", "status"])
    assert snap_launcher.main() == 7
    assert calls[-1] == ("t" * 32, "occ", ["status"], {})
    assert len(calls) == 2


def test_folders_that_only_hold_mounts_are_open_to_system_users(tmp_path):
    from multipak.snap_sandbox import Plan

    plan = Plan(lambda path: None)
    plan.set("/snap/users/1", "local", str(tmp_path))
    plan.set("/snap/users/current", "symlink", "1")
    arguments = plan.arguments()
    # Made by MultiPak, 0755, before what is mounted in it; not by bubblewrap, 0700.
    assert arguments.index("/snap/users") < arguments.index("/snap/users/1")
    index = arguments.index("/snap/users")
    assert arguments[index - 1] == "--dir" and arguments[index - 3 : index - 1] == [
        "--dir",
        "/snap",
    ]
    if shutil.which("bwrap") and Path("/usr/bin/stat").exists():
        host = ["--ro-bind", "/usr", "/usr"]
        for name in ("bin", "lib", "lib64"):
            if os.path.islink(f"/{name}"):
                host += ["--symlink", os.readlink(f"/{name}"), f"/{name}"]
            elif os.path.isdir(f"/{name}"):
                host += ["--ro-bind", f"/{name}", f"/{name}"]
        result = subprocess.run(
            ["bwrap", *arguments, *host, "--", "/usr/bin/stat", "-c", "%a", "/snap/users"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            assert result.stdout == "755\n"


@pytest.mark.skipif(not shutil.which("bwrap"), reason="bubblewrap is needed")
@pytest.mark.parametrize("shell", ["sh", "dash", "bash"])
def test_a_system_service_does_not_get_the_same_process_numbers_each_start(shell, tmp_path):
    # A stale pid file of a daemon killed outright named the number the next start
    # got in its new process namespace: MiniDLNA refused to start ("already running").
    if not shutil.which(shell):
        pytest.skip(f"{shell} is needed")
    control = tmp_path / "control"
    control.touch()
    script = snap.SERVICE.replace("/snap/.multipak/channel/control", str(control))
    bubble = ["bwrap", "--unshare-user", "--uid", "0", "--gid", "0", "--unshare-pid"]
    bubble += ["--dev-bind", "/", "/", "--proc", "/proc", "--"]
    numbers = set()
    for _ in range(4):
        result = subprocess.run(
            [*bubble, shell, "-c", script, "multipak-service", "sh", "-c", "echo $$"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        numbers.add(int(result.stdout))
    # Without the capability to set it (bubblewrap's own namespace drops them all),
    # a random number of them was used up first.
    assert len(numbers) > 1 and max(numbers) > 3
    with_capability = [*bubble[:-1], "--cap-add", "CAP_SYS_ADMIN", "--"]
    result = subprocess.run(
        [*with_capability, shell, "-c", script, "multipak-service", "sh", "-c", "echo $$"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert int(result.stdout) > 1000, result.stderr


def test_the_session_starts_system_services_the_computer_restart_forgot(
    store, monkeypatch, tmp_path
):
    from multipak import store as store_module

    monkeypatch.setattr(snap, "launcher_invocation", lambda: ["multipak-snap", "--"])
    monkeypatch.setattr(snap, "_home", lambda: tmp_path)
    info = snap_meta.parse(
        "name: cloud\napps:\n  db:\n    command: d\n    daemon: simple\n"
        "  off:\n    command: o\n    daemon: simple\n    install-mode: disable\n"
        "  mine:\n    command: m\n    daemon: simple\n    daemon-scope: user\n"
    )
    tx = Transaction("d" * 32, "", "", "cloud", "1", "snap", "", method="snap", status="installed")
    store.save(tx)
    monkeypatch.setattr(store_module, "Store", lambda: store)
    monkeypatch.setattr(snap, "_context", lambda store, tx: (info, None))
    started = []

    class FakeServices:
        def __init__(self, tx, info, *args, **kwargs):
            pass

        def start_enabled(self, scope=None):
            started.append(scope)

        def act(self, action, names):
            return ""

    monkeypatch.setattr(snap, "Services", FakeServices)
    path = snap._autostart_path(tx)
    # Installed before this change: starting its services writes the entry.
    assert not path.exists()
    snap.control_services(tx.id, "start", [])
    text = path.read_text()
    assert "Name=MultiPak snap cloud: db, mine" in text
    assert f'"--services" "login" "{tx.id}"' in text
    snap.control_services(tx.id, "login", [])
    assert started == [None]  # Every enabled service, of both scopes.


def test_a_failed_installation_does_not_block_the_next_one(tmp_path):
    # A download that failed (or a closed process) left the snap's record behind:
    # opening the snap again must not say it is already installed.
    from multipak.models import Transaction
    from multipak.store import Store

    store = Store(tmp_path / "data", tmp_path / "applications")

    def record(identifier, status):
        tx = Transaction(
            identifier * 32,
            "",
            "",
            "hello-world",
            "6.4",
            "snap",
            "",
            method="snap",
            status=status,
            snap_name="hello-world",
        )
        store.save(tx)
        return tx

    failed = record("a", "failed")
    assert snap.installed_snap(store, "hello-world") is None
    cleaned = []

    class FakeEngine:
        def __init__(self):
            self.store = store

        def cleanup(self, tx):
            cleaned.append(tx.id)

    snap._clean_leftovers(FakeEngine(), "hello-world")
    assert cleaned == [failed.id]
    # An installed snap, or one whose removal must be finished, still blocks.
    for status in ("installed", "cleanup_failed"):
        held = record("b", status)
        assert snap.installed_snap(store, "hello-world").id == held.id
