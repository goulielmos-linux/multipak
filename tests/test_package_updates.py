import json

import pytest

from multipak import package_check, package_updates
from multipak.package_updates import Check


def statuses(result):
    return {name: (value["status"], value["version"]) for name, value in result.items()}


def test_apt_tells_updates_repositories_and_files():
    text = """gimp:
  Installed: 2.10.38-1
  Candidate: 3.0.0-1
  Version table:
     3.0.0-1 500
        500 http://deb.debian.org/debian trixie/main amd64 Packages
 *** 2.10.38-1 100
        100 /var/lib/dpkg/status
google-chrome-stable:
  Installed: 142.0
  Candidate: 142.0
  Version table:
 *** 142.0 500
        500 https://dl.google.com/linux/chrome/deb stable/main amd64 Packages
        100 /var/lib/dpkg/status
localapp:amd64:
  Installed: 1.0
  Candidate: 1.0
  Version table:
 *** 1.0 100
        100 /var/lib/dpkg/status
"""
    result = package_check.parse_apt_policy(text, ["gimp", "google-chrome-stable", "localapp"])
    assert statuses(result) == {
        "gimp": ("available", "3.0.0-1"),
        "google-chrome-stable": ("current", ""),
        "localapp": ("no-source", ""),
    }
    assert result["gimp"]["installed"] == "2.10.38-1"


def test_dnf_check_update_lists_only_the_asked_packages():
    text = """
gimp.x86_64                     3:3.0.0-1.fc43                 updates
gimp-libs.x86_64                3:3.0.0-1.fc43                 updates
Obsoleting Packages
other.x86_64                    1-1                            updates
"""
    assert package_check.parse_dnf_check_update(text, ["gimp", "other"]) == {
        "gimp": "3:3.0.0-1.fc43"
    }


def test_zypper_tables():
    updates = """S | Repository | Name | Current Version | Available Version | Arch
--+------------+------+-----------------+-------------------+-------
v | repo-oss   | gimp | 2.10-1          | 3.0-1             | x86_64
"""
    search = """S  | Name | Type    | Version | Arch   | Repository
---+------+---------+---------+--------+------------------
i+ | gimp | package | 2.10-1  | x86_64 | (System Packages)
v  | gimp | package | 3.0-1   | x86_64 | repo-oss
i  | mine | package | 1.0-1   | x86_64 | (System Packages)
"""
    assert package_check.parse_zypper_updates(updates, ["gimp"]) == {"gimp": "3.0-1"}
    assert package_check.parse_zypper_search(search, ["gimp", "mine"]) == {"gimp"}


def test_pacman_apk_and_xbps():
    assert package_check.parse_pacman_upgrades(
        "gimp 2.10.38-1 -> 3.0.0-1\nother 1 -> 2\n", ["gimp"]
    ) == {"gimp": "3.0.0-1"}
    apk = package_check.parse_apk_version(
        "Installed:                                Available:\n"
        "gimp-2.10.38-r0                         < 3.0.0-r0\n"
        "gimp-data-1-r0                          = 1-r0\n"
        "mine-1.0-r0                             ?\n",
        ["gimp", "gimp-data", "mine"],
    )
    assert statuses(apk) == {
        "gimp": ("available", "3.0.0-r0"),
        "gimp-data": ("current", ""),
        "mine": ("no-source", ""),
    }
    assert apk["gimp-data"]["installed"] == "1-r0"
    assert package_check.parse_xbps_updates(
        "gimp-3.0.0_1 update x86_64 https://repo-default.voidlinux.org/current 1 2\n", ["gimp"]
    ) == {"gimp": "3.0.0_1"}


def test_the_output_line_becomes_checks():
    line = (
        package_check.MARK
        + " "
        + json.dumps({"gimp": package_check.entry("available", "2.10", "3.0")})
    )
    result = package_updates.parse_output("noise\n" + line + "\n", ["gimp", "vlc"], 5.0)
    assert result == {
        "gimp": Check("available", "2.10", "3.0", "", 5.0),
        "vlc": Check("failed", error="no answer", checked=5.0),
    }
    with pytest.raises(RuntimeError):
        package_updates.parse_output("nothing", ["gimp"], 5.0)


def test_the_script_runs_in_the_container_without_downloading_unless_asked(tmp_path):
    calls = []

    class Runner:
        def run(self, command, timeout, check=True):
            calls.append(command)
            if "python3" in command:
                return (
                    package_check.MARK
                    + " "
                    + json.dumps({"gimp": package_check.entry("current", "3.0")})
                )
            return ""

    result = package_updates.check_container(Runner(), "MultiPak-Fedora-43", "rpm", ["gimp"])
    assert result["gimp"].status == "current"
    # A new private folder with a random name, never a fixed path in the shared /tmp.
    mkdir = calls[0]
    assert mkdir[-5:-1] == ["mkdir", "-m", "700", "--"]
    folder = mkdir[-1]
    assert folder.startswith(package_updates.FOLDER) and len(folder) > len(package_updates.FOLDER)
    target = f"{folder}/{package_updates.SCRIPT}"
    assert calls[1][:2] == ["podman", "cp"] and calls[1][-1].endswith(":" + target)
    assert calls[2][-3:] == [target, "rpm", "gimp"]
    assert calls[3][-2:] == ["--", folder] and "rm" in calls[3]
    package_updates.check_container(Runner(), "MultiPak-Fedora-43", "rpm", ["gimp"], refresh=True)
    assert calls[-2][-3:] == ["rpm", "--refresh", "gimp"]
    assert calls[-2][-4] != target  # another random folder each time
    with pytest.raises(ValueError):
        package_updates.check_container(Runner(), "bad name;", "rpm", ["gimp"])


def test_saved_results_expire(tmp_path):
    class Store:
        root = tmp_path

    package_updates.remember(
        Store, {"a": Check("available", checked=100.0), "b": Check("current", checked=0.0)}
    )
    found = package_updates.cached(Store, now=lambda: 100.0 + 60)
    assert set(found) == {"a", "b"}
    found = package_updates.cached(Store, now=lambda: package_updates.FRESH + 50.0)
    assert set(found) == {"a"}


def test_unknown_kinds_are_reported_as_unsupported(capsys):
    package_check.main(["package_check.py", "appimage", "app"])
    line = capsys.readouterr().out.strip()
    assert line.startswith(package_check.MARK)
    assert json.loads(line.split(" ", 1)[1])["app"]["status"] == "unsupported"


def test_apt_rpm_on_alt_linux_treats_the_rpm_database_as_the_installed_copy():
    text = """gimp:
  Installed: 2.10.38-alt1
  Candidate: 3.0.0-alt1
  Version table:
     3.0.0-alt1 0
        500 http://ftp.altlinux.org p11/branch/x86_64 classic
 *** 2.10.38-alt1 0
        100 RPM Database
mine:
  Installed: 1.0-alt1
  Candidate: 1.0-alt1
  Version table:
 *** 1.0-alt1 0
        100 RPM Database
"""
    assert statuses(package_check.parse_apt_policy(text, ["gimp", "mine"])) == {
        "gimp": ("available", "3.0.0-alt1"),
        "mine": ("no-source", ""),
    }


def test_pisi_and_eopkg_lists_without_colors():
    text = "\x1b[32mgimp\x1b[0m           - GNU Image Manipulation Program\nother - x\n"
    assert package_check.parse_eopkg_upgrades(package_check.plain(text), ["gimp"]) == {"gimp": ""}


def test_slackware_compares_with_slackpkg_and_prefers_patches():
    pkglist = (
        "slackware64 mozilla-firefox 115.3.0esr x86_64 1 mozilla-firefox-115.3.0esr-x86_64-1"
        " ./slackware64/xap txz\n"
        "patches mozilla-firefox 128.4.0esr x86_64 1_slack15.0"
        " mozilla-firefox-128.4.0esr-x86_64-1_slack15.0 ./patches/packages txz\n"
        "slackware64 vim 9.0.2127 x86_64 1 vim-9.0.2127-x86_64-1 ./slackware64/ap txz\n"
    )
    offered = package_check.parse_slackpkg_list(pkglist)
    records = [
        "mozilla-firefox-115.3.0esr-x86_64-1",
        "vim-9.0.2127-x86_64-1",
        "mine-1.0-x86_64-1_SBo",
    ]
    names = ["mozilla-firefox", "vim", "mine"]
    result = package_check.decide_slackware(
        names, package_check.slackware_installed(records, names), offered
    )
    assert statuses(result) == {
        "mozilla-firefox": ("available", "128.4.0esr-1_slack15.0"),
        "vim": ("current", ""),
        "mine": ("no-source", ""),
    }
    assert result["vim"]["installed"] == "9.0.2127-1"


def test_slitaz_lists():
    assert package_check.parse_tazpkg_info(
        "gimp\t2.10.38\tgraphics\tImage editor\thttps://gimp.org\t\t20M\tgtk+\n"
    ) == {"gimp": "2.10.38"}


def test_the_new_kinds_have_a_checker():
    for kind in ("pisi", "slackware", "slitaz", "gentoo"):
        assert kind in package_updates.SUPPORTED
        assert package_check.checker(kind) is not None
