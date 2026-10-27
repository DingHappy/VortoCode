import pytest

from src.gateway.account_access import (
    AccountAssistant,
    AccountIdentity,
    account_origin,
    identity_from_account_service,
    own_assistants,
)


def test_two_devices_of_same_account_match_even_after_email_changes():
    first = identity_from_account_service(
        {"user_id": 12, "email": "old@example.test", "device": {"id": 1}}, "https://account.test"
    )
    second = identity_from_account_service(
        {"user_id": 12, "email": "new@example.test", "device": {"id": 2}},
        "https://account.test:443/",
    )
    assert first == second
    assistant = AccountAssistant("a" * 32, first, "常驻助手", "online")
    assert own_assistants(second, [assistant])[0]["instance_id"] == "a" * 32


def test_same_email_or_subject_on_another_issuer_does_not_match():
    owner = identity_from_account_service(
        {"user_id": 12, "email": "same@example.test"}, "https://account.test"
    )
    stranger = identity_from_account_service(
        {"user_id": 13, "email": "same@example.test"}, "https://account.test"
    )
    different_issuer = AccountIdentity("https://other.test", "12")
    assistant = AccountAssistant("a" * 32, owner, "常驻助手")
    assert own_assistants(stranger, [assistant]) == []
    assert own_assistants(different_issuer, [assistant]) == []


@pytest.mark.parametrize(
    "data",
    [{"email": "a@example.test"}, {"user_id": True}, {"user_id": "12"}, {"user_id": 0}, None],
)
def test_legacy_or_unverified_identity_is_not_guessed(data):
    with pytest.raises(ValueError):
        identity_from_account_service(data, "https://account.test")


def test_offline_assistants_remain_visible_and_unknown_never_becomes_online():
    owner = AccountIdentity("https://account.test", "12")
    result = own_assistants(
        owner,
        [
            AccountAssistant("a" * 32, owner, "服务器", "offline"),
            AccountAssistant("b" * 32, owner, "电脑", "future-state"),
        ],
    )
    assert [item["availability"] for item in result] == ["offline", "unknown"]
    assert all("approve" not in item and "control" not in item for item in result)
    with pytest.raises(ValueError):
        own_assistants(
            owner, [AccountAssistant("a" * 32, owner, "A"), AccountAssistant("a" * 32, owner, "B")]
        )


@pytest.mark.parametrize(
    "origin",
    [
        "http://account.test",
        "https://user:secret@account.test",
        "https://account.test/?token=secret",
        "https://account.test/#secret",
        "https://account.test/path",
    ],
)
def test_account_origin_does_not_accept_credentials_or_untrusted_plain_http(origin):
    with pytest.raises(ValueError):
        account_origin(origin)
