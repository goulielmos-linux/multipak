import threading
from types import SimpleNamespace

import pytest

from multipak.process import CommandError, Runner
from multipak.updates import update_container


@pytest.mark.parametrize("exit_code", [0, 7])
def test_container_update_streams_output_and_checks_process_result(
    tmp_path, monkeypatch, exit_code
):
    command = tmp_path / "distrobox"
    command.write_text(
        "#!/bin/sh\n"
        '[ "$#" = 2 ] && [ "$1" = upgrade ] && [ "$2" = MultiPak-Fedora-45 ] || exit 90\n'
        '[ "$DBX_CONTAINER_MANAGER" = podman ] && [ "$DBX_NON_INTERACTIVE" = 1 ] || exit 91\n'
        "if read -r input; then exit 92; fi\n"
        'printf "Downloading packages\\n"\n'
        f"exit {exit_code}\n"
    )
    command.chmod(0o755)
    podman = tmp_path / "podman"
    podman.write_text(
        '#!/bin/sh\n[ "$1" = container ] && [ "$2" = exists ] && [ "$3" = MultiPak-Fedora-45 ]\n'
    )
    podman.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    logs, progress = [], []
    engine = SimpleNamespace(
        runner=Runner(threading.Event(), logs.append),
        progress=lambda percent, text: progress.append((percent, text)),
    )
    if exit_code:
        with pytest.raises(CommandError) as failure:
            update_container(engine, "MultiPak-Fedora-45")
        assert failure.value.code == exit_code
    else:
        update_container(engine, "MultiPak-Fedora-45")
    assert "Downloading packages" in "".join(logs)
    assert progress == [(-1, "Updating MultiPak-Fedora-45… Please wait.")]


def test_invalid_update_target_never_starts_a_process():
    update_container_engine = SimpleNamespace()
    with pytest.raises(ValueError):
        update_container(update_container_engine, "--all")


def test_removed_container_does_not_start_distrobox():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        raise CommandError(argv, 1, "")

    engine = SimpleNamespace(runner=SimpleNamespace(run=run), progress=lambda *_: None)
    with pytest.raises(RuntimeError, match="Reload"):
        update_container(engine, "MultiPak-Fedora-45")
    assert calls == [["podman", "container", "exists", "MultiPak-Fedora-45"]]
