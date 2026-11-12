import base64
import json
import subprocess

import pytest

from multipak.update_notice import DAY, NoticeState, Policy, parse_policy


def test_both_entry_points_share_exactly_one_24_hour_deadline(tmp_path):
    manager = NoticeState(tmp_path / "notice.json")
    installer = NoticeState(tmp_path / "notice.json")
    update = Policy("1.0.2", "1.0.1")
    manager.remember_later(now=100)
    assert not installer.should_show(update, "1.0.1", now=100 + DAY - 1)
    assert installer.should_show(update, "1.0.1", now=100 + DAY)
    installer.remember_later(now=100 + DAY)
    assert not manager.should_show(update, "1.0.1", now=100 + DAY + 1)


def test_security_requirement_bypasses_snooze_but_safe_versions_remain_optional(tmp_path):
    state = NoticeState(tmp_path / "notice.json")
    state.remember_later(now=100)
    policy = Policy("1.2.0", "1.0.2")
    assert state.should_show(policy, "1.0.1", now=101)
    assert policy.required("1.0.1")
    assert not policy.required("1.0.2")
    assert not state.should_show(policy, "1.0.2", now=101)
    assert not policy.available("1.2.0")
    assert not policy.available("1.3.0")


@pytest.mark.parametrize(
    "changes",
    [
        {"app_id": "other.app"},
        {"schema": 2},
        {"latest_version": "1.0.2\n"},
        {"latest_version": "1.0.2a1"},
        {"minimum_supported_version": "9.0.0"},
    ],
)
def test_malformed_or_wrong_application_policies_are_rejected(changes):
    value = {
        "schema": 1,
        "app_id": "io.github.goulielmos_linux.multipak",
        "latest_version": "1.0.2",
        "minimum_supported_version": "1.0.1",
    } | changes
    with pytest.raises(ValueError):
        parse_policy(json.dumps(value).encode())


def test_offline_or_invalid_policy_does_not_create_a_security_block(tmp_path, monkeypatch):
    state = NoticeState(tmp_path / "notice.json")
    state.save(payload=123, signature=[], checked_at=100)
    monkeypatch.setattr(
        "multipak.update_notice.fetch_policy", lambda key: (_ for _ in ()).throw(OSError("offline"))
    )
    assert state.checked_policy(tmp_path / "missing-key", now=101) is None


def test_unverified_cached_policy_is_never_trusted(tmp_path, monkeypatch):
    state = NoticeState(tmp_path / "notice.json")
    state.save(
        payload=base64.b64encode(b'{"minimum_supported_version":"9.0.0"}').decode(),
        signature=base64.b64encode(b"invalid signature").decode(),
        checked_at=100,
    )
    monkeypatch.setattr(
        "multipak.update_notice.verify_policy",
        lambda *args: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "gpgv")),
    )
    monkeypatch.setattr(
        "multipak.update_notice.fetch_policy", lambda key: (_ for _ in ()).throw(OSError("offline"))
    )
    assert state.checked_policy(tmp_path / "public-key", now=101) is None
