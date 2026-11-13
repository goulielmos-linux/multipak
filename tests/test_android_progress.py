import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from multipak import android_initializer
from multipak.android_progress import AndroidProgress, AndroidStartupOutput
from multipak.process import Cancelled, Runner


@pytest.mark.parametrize("chunk_size", [1, 17, 4096])
def test_startup_retries_are_explained_without_changing_command_output(chunk_size):
    logs, updates = [], []
    runner = SimpleNamespace(log=logs.append)
    original = runner.log
    output = (
        "[18:08:04] Waiting for binder Service Manager...\n"
        "[18:08:05] Failed to get service waydroidplatform, trying again...\n"
        "Name: Failed Adventure\npackageName: org.example.demo\n"
    )
    with AndroidStartupOutput(runner, lambda *args: updates.append(args)):
        runner.log("$ ['waydroid', 'app', 'list']")
        for start in range(0, len(output), chunk_size):
            runner.log(output[start : start + chunk_size])
        assert not any("Failed to get service" in line for line in logs)
    assert runner.log is original
    assert updates == [(-1, "Waiting for Android to finish starting…")]
    assert "Name: Failed Adventure" in logs and "packageName: org.example.demo" in logs
    assert logs.count("Waiting for Android to finish starting…") == 1


def test_startup_failure_keeps_original_retries_and_real_errors():
    logs = []
    runner = SimpleNamespace(log=logs.append)
    original = runner.log
    with pytest.raises(TimeoutError, match="startup timed out"):
        with AndroidStartupOutput(runner, lambda *_: None):
            runner.log("[18:08:05] Failed to get service waydroidplatform, trying again...\n")
            runner.log("[ERROR] Failed to access IPlatform service\n")
            assert "[ERROR] Failed to access IPlatform service" in logs
            raise TimeoutError("startup timed out")
    assert runner.log is original
    assert "Android startup diagnostics:" in logs
    assert any("Failed to get service" in line for line in logs)


def test_cancelled_startup_does_not_report_expected_retries_as_failures():
    logs = []
    runner = SimpleNamespace(log=logs.append)
    with pytest.raises(Cancelled):
        with AndroidStartupOutput(runner, lambda *_: None):
            runner.log("Failed to get service waydroidplatform, trying again...")
            raise Cancelled
    assert not any("Failed to get service" in line for line in logs)


def test_split_stream_updates_each_download_without_waiting_for_newlines():
    updates = []
    stream = AndroidProgress(lambda *args: updates.append(args))
    stream.feed("[11:22:46] Downloading https://example.org/system/system.zip\n")
    assert updates[-1] == (-1, "Connecting to the Android system download…")
    for chunk in ("\r[Down", "loading] 297.5 MB/850 MB    1.25 ", "MB/s(approx.) "):
        stream.feed(chunk)
    assert updates[-1] == (35, "Downloading Android system — 35%\n297.5 MB of 850 MB · 1.25 MB/s")
    count = len(updates)
    stream.feed("\r[Downloading] 297.5 MB/850 MB    1.25 MB/s(approx.) ")
    assert len(updates) == count
    stream.feed("\n[11:23:46] Validating system image\n[11:23:48] Extracting to /images\n")
    assert updates[-2:] == [
        (-1, "Validating the Android system…"),
        (-1, "Extracting the Android system…"),
    ]
    stream.feed("[11:24:46] Downloading https://example.org/vendor/vendor.zip\n")
    stream.feed("\r[Downloading] 10 MB/100 MB    25 kB/s(approx.) ")
    assert updates[-1] == (10, "Downloading Android components — 10%\n10 MB of 100 MB · 25 kB/s")
    stream.feed("\nValidating vendor image\nExtracting to /images\n")
    assert updates[-1] == (-1, "Extracting the Android components…")


def test_zero_speed_is_visible_and_unknown_or_invalid_sizes_stay_indeterminate():
    updates = []
    stream = AndroidProgress(lambda *args: updates.append(args))
    stream.feed("Downloading https://example.org/system/image.zip\n")
    stream.feed("\r[Downloading] 10 MB/0 MB    0 MB/s(approx.) ")
    assert updates[-1][0] == -1
    stream.feed("\r[Downloading] 10 MB/100 MB    0 kB/s(approx.) ")
    assert updates[-1][0] == 10 and "0 kB/s" in updates[-1][1]
    stream.feed("\r[Downloading] 110 MB/100 MB    1 MB/s(approx.) ")
    assert updates[-1][0] == 100


def test_output_callback_receives_updates_before_process_exits(tmp_path):
    acknowledgement = tmp_path / "acknowledged"
    updates = []

    def receive(text):
        updates.append(text)
        acknowledgement.touch()

    code = (
        "import sys,time; from pathlib import Path; "
        "print('[Downloading] 1 MB/10 MB    1 MB/s(approx.)', end=' '); "
        "path=Path(sys.argv[1]); deadline=time.monotonic()+2\n"
        "while not path.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "assert path.exists(), 'output was buffered until exit'"
    )
    Runner(threading.Event(), lambda _: None).run(
        [sys.executable, "-u", "-c", code, str(acknowledgement)], on_output=receive, timeout=4
    )
    assert "Downloading" in "".join(updates)


def test_supervisor_stops_separate_child_process_group_when_download_is_cancelled(tmp_path):
    watch = tmp_path / "running"
    watch.touch()
    marker = tmp_path / "child.pid"
    child_code = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid())); print('ready',flush=True); time.sleep(30)"
    )
    script = (
        "import importlib.util,sys,os; from pathlib import Path; "
        "sys.path.insert(0,str(Path(sys.argv[1]).parent)); "
        "os.environ['PKEXEC_UID']=str(os.getuid()); "
        "spec=importlib.util.spec_from_file_location('initializer',sys.argv[1]); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "sys.exit(module.supervise([sys.executable,'-c',sys.argv[3],sys.argv[4]],sys.argv[2]))"
    )
    cancelled = threading.Event()
    with pytest.raises(Cancelled):
        Runner(cancelled, lambda _: None).run(
            [
                sys.executable,
                "-I",
                "-u",
                "-c",
                script,
                android_initializer.__file__,
                str(watch),
                child_code,
                str(marker),
            ],
            on_output=lambda text: cancelled.set() if "ready" in text else None,
            cancel_file=watch,
            timeout=5,
        )
    assert not watch.exists()
    process = Path("/proc") / marker.read_text() / "stat"
    assert not process.exists() or process.read_text().split()[2] == "Z"


def test_initializer_only_launches_waydroid_with_unbuffered_system_python(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(android_initializer.os, "geteuid", lambda: 0)
    monkeypatch.setenv("PKEXEC_UID", "1000")
    watch = tmp_path / "running"
    monkeypatch.setattr(sys, "argv", ["initializer.py", str(watch), "init", "-s", "VANILLA"])
    monkeypatch.setattr(android_initializer, "supervise", lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(android_initializer.shutil, "which", lambda *a, **kw: None)
    resolve = android_initializer.initialization_arguments
    monkeypatch.setattr(
        android_initializer,
        "initialization_arguments",
        lambda arguments: resolve(arguments, tmp_path / "channels.cfg"),
    )
    assert android_initializer.main() == 0
    assert calls == [
        (
            [
                "/usr/bin/python3",
                "-E",
                "-s",
                "-u",
                "/usr/bin/waydroid",
                "init",
                "-s",
                "VANILLA",
                "-c",
                android_initializer.SYSTEM_CHANNEL,
                "-v",
                android_initializer.VENDOR_CHANNEL,
                "-r",
                "lineage",
            ],
            str(watch),
        )
    ]
    monkeypatch.setattr(sys, "argv", ["initializer.py", str(watch), "shell", "--", "sh"])
    with pytest.raises(ValueError):
        android_initializer.main()
    assert len(calls) == 1


@pytest.mark.parametrize("content", [None, "[channels]\nsystem_channel=\nvendor_channel=\n"])
def test_unconfigured_channels_use_official_sources_before_first_attempt(tmp_path, content):
    path = tmp_path / "channels.cfg"
    if content is not None:
        path.write_text(content)
    arguments = android_initializer.initialization_arguments(["init", "-s", "VANILLA"], path)
    assert arguments == [
        "init",
        "-s",
        "VANILLA",
        "-c",
        android_initializer.SYSTEM_CHANNEL,
        "-v",
        android_initializer.VENDOR_CHANNEL,
        "-r",
        "lineage",
    ]


def test_custom_ota_channels_rom_and_explicit_options_are_preserved(tmp_path):
    path = tmp_path / "channels.cfg"
    path.write_text(
        "[channels]\nsystem_channel=https://example.org/system\n"
        "vendor_channel=https://example.org/vendor\nrom_type=bliss\n"
    )
    arguments = android_initializer.initialization_arguments(["init", "-s", "VANILLA"], path)
    assert arguments[-6:] == [
        "-c",
        "https://example.org/system",
        "-v",
        "https://example.org/vendor",
        "-r",
        "bliss",
    ]
    explicit = ["init", "-s", "VANILLA", "-c", "https://explicit.example/system", "-r", "lineage"]
    result = android_initializer.initialization_arguments(explicit, path)
    assert result == [*explicit, "-v", "https://example.org/vendor"]
