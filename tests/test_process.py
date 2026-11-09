import sys
import threading

import pytest

from multipak.process import Cancelled, CommandError, Runner, looks_like_network_failure


def test_metacharacters_are_passed_as_literal_arguments(tmp_path):
    target = tmp_path / "should-not-exist"
    text = f"hello; touch {target}; $(whoami)"
    runner = Runner(threading.Event(), lambda _: None)
    result = runner.run([sys.executable, "-c", "import sys; print(sys.argv[1])", text])
    assert result.strip() == text
    assert not target.exists()


def test_nonzero_exit_has_diagnostics():
    runner = Runner(threading.Event(), lambda _: None)
    with pytest.raises(CommandError) as info:
        runner.run([sys.executable, "-c", "print('specific failure'); raise SystemExit(7)"])
    assert info.value.code == 7
    assert "specific failure" in info.value.output


def test_cancellation_terminates_running_process():
    event = threading.Event()
    timer = threading.Timer(0.2, event.set)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            Runner(event, lambda _: None).run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
            )
    finally:
        timer.cancel()


def test_timeout():
    with pytest.raises(TimeoutError):
        Runner(threading.Event(), lambda _: None).run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.1,
        )


def test_shared_environment_cancellation_finishes_current_command_only():
    event = threading.Event()
    runner = Runner(event, lambda _: None)
    runner.defer_cancellation = True
    timer = threading.Timer(0.1, event.set)
    timer.start()
    try:
        result = runner.run(
            [sys.executable, "-c", "import time; time.sleep(0.25); print('finished')"],
        )
        assert result.strip() == "finished"
        with pytest.raises(Cancelled):
            runner.run([sys.executable, "-c", "print('must not start')"])
    finally:
        timer.cancel()


def test_network_failure_is_a_hint_not_every_error():
    assert looks_like_network_failure("Temporary failure resolving example.org")
    assert not looks_like_network_failure("Package not found")
