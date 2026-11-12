import sys
import threading
import time
from pathlib import Path

import pytest

from multipak.container_progress import ImageProgress, PackageProgress, byte_size
from multipak.process import Cancelled, CommandError, Runner


@pytest.mark.parametrize("chunk_size", [1, 7, 10000])
def test_image_download_handles_ansi_frames_split_output_and_each_layer(chunk_size):
    changes = []
    parser = ImageProgress(lambda *args: changes.append(args))
    output = (
        "Trying to pull example/image:1...\r\n"
        "Getting image source signatures\r\n"
        "Copying blob aabbccddeeff [----] 1.0MiB / 4.0MiB | 1.0 MiB/s\r\n"
        "\x1b[2A\x1b[JCopying blob aabbccddeeff [----] 2.0MiB / 4.0MiB\r\n"
        "Copying blob 123456789abc [----] 1.0MB / 10.0MB\r\n"
        "Copying blob aabbccddeeff done |\r\n"
        "Copying blob 123456789abc skipped: already exists\r\n"
        "Writing manifest to image destination"
    )
    for start in range(0, len(output), chunk_size):
        parser.feed(output[start : start + chunk_size])
    parser.finish()
    assert [percent for percent, _ in changes if percent >= 0] == [25, 50, 10, 100]
    assert "part 1: 25%" in changes[2][1]
    assert "part 2: 10%" in changes[4][1]
    assert changes[-1] == (-1, "Saving the container image…")
    assert all("\x1b" not in text for _, text in changes)


def test_cached_unknown_or_invalid_sizes_never_invent_download_percentage():
    changes = []
    parser = ImageProgress(lambda *args: changes.append(args))
    parser.feed(
        "Copying blob abcdef skipped: already exists\n"
        "Copying blob 123456 done |\n"
        "Copying blob abcdef [----] 0.0b / 0.0b\n"
        + "Copying blob abcdef [----] "
        + "9" * 1000
        + "MiB / 1.0MiB\n"
    )
    assert changes == [(-1, "Reusing downloaded image layers…")]
    assert byte_size("2KiB") == 2048
    assert byte_size("2kB") == 2000


@pytest.mark.parametrize(
    "line, percent, title",
    [
        ("[1/3] example-1.x86_64  25% | 1.0 MiB/s | 500 KiB", 25, "example-1.x86_64"),
        ("[3/3] Total  75% | 1.0 MiB/s | 5.0 MiB", 75, "container packages"),
        ("(1/4): example.rpm  50% [###] 1.0 MB/s | 10 MB", 50, "example.rpm"),
        (" example-1  5.0 MiB  1.0 MiB/s 00:05 [###] 40%", 40, "example-1"),
        (" Total (2/4)  5.0 MiB  1.0 MiB/s 00:05 [###] 60%", 60, "container packages"),
    ],
)
def test_package_download_names_the_file_or_reported_aggregate(line, percent, title):
    changes = []
    parser = PackageProgress(lambda *args: changes.append(args), updating=True)
    parser.feed(line + "\r")
    assert changes[-1][0] == percent
    assert title in changes[-1][1]
    assert changes[-1][1].startswith("Downloading")


def test_apt_download_and_install_are_separate_and_do_not_require_a_terminal():
    changes = []
    parser = PackageProgress(lambda *args: changes.append(args), updating=True)
    parser.feed("Hit:1 https://repo stable InRelease\n20% [Working]\r")
    assert changes[-1] == (20, "Checking package repositories — 20%")
    parser.feed("Need to get 30 MB of archives.\n45% [1 example 10 kB/20 kB 50%]\r")
    assert changes[-1] == (45, "Downloading container packages — 45%")
    parser.feed("Preparing to unpack example.deb ...\n")
    assert changes[-1] == (-1, "Installing package updates…")
    parser.feed("Progress: [ 50%]\r")
    assert changes[-1] == (50, "Installing packages — 50%")


def test_preparation_and_updates_without_numbers_show_stages_without_fake_percentages():
    changes = []
    parser = PackageProgress(lambda *args: changes.append(args))
    parser.feed(
        "distrobox: Installing basic packages...\n"
        "Reading package lists...\n"
        "Need to get 30 MB of archives.\n"
        "Get:1 https://repo stable/main example amd64 1.0 [30 MB]\n"
        "Setting up example (1.0) ...\n"
        "distrobox: Setting up groups...\n"
    )
    assert changes == [
        (-1, "Preparing the container's basic components…"),
        (-1, "Checking package repositories…"),
        (-1, "Downloading container packages…"),
        (-1, "Installing the container's basic components…"),
        (-1, "Configuring the container…"),
    ]


def test_install_percentages_and_invalid_percentages_do_not_become_download_progress():
    changes = []
    parser = PackageProgress(lambda *args: changes.append(args), updating=True)
    parser.feed("error example 150% 1 MiB/s\nexample -5% 1 MiB/s\n")
    assert changes == []
    parser.feed("Running transaction\n(1/3) upgrading example [###] 100% 2 MiB\n")
    assert changes == [(-1, "Installing package updates…")]


@pytest.mark.parametrize("exit_code", [0, 7])
def test_output_terminal_preserves_stdin_eof_streaming_and_exit_code(exit_code):
    events = []
    runner = Runner(threading.Event(), lambda _: None)
    script = (
        "import os,sys; "
        "assert not os.isatty(0) and os.isatty(1) and os.isatty(2); "
        'assert sys.stdin.read() == ""; '
        'print("Copying blob abcdef [----] 1MiB / 4MiB", flush=True); '
        f"sys.exit({exit_code})"
    )
    parser = ImageProgress(lambda *args: events.append(args))
    if exit_code:
        with pytest.raises(CommandError) as failure:
            parser.run(runner, [sys.executable, "-u", "-c", script], terminal_output=True)
        assert failure.value.code == exit_code
    else:
        result = parser.run(runner, [sys.executable, "-u", "-c", script], terminal_output=True)
        assert "Copying blob" in result
    assert events[-1][0] == 25


@pytest.mark.parametrize("cancel", [True, False])
def test_terminal_output_cleanup_on_cancel_or_timeout(tmp_path, cancel):
    marker = tmp_path / "child.pid"
    event = threading.Event()
    runner = Runner(event, lambda _: None)
    child = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"Path({str(marker)!r}).write_text(str(os.getpid())); "
        'print("ready",flush=True); time.sleep(30)'
    )
    script = (
        "import subprocess,sys,time; "
        f'subprocess.Popen([sys.executable,"-u","-c",{child!r}]); time.sleep(30)'
    )
    with pytest.raises(Cancelled if cancel else TimeoutError):
        runner.run(
            [sys.executable, "-u", "-c", script],
            terminal_output=True,
            on_output=lambda text: event.set() if cancel and "ready" in text else None,
            timeout=3,
        )
    assert marker.exists()
    path = Path("/proc") / marker.read_text() / "stat"
    deadline = time.monotonic() + 1
    while path.exists() and path.read_text().split()[2] != "Z" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not path.exists() or path.read_text().split()[2] == "Z"
