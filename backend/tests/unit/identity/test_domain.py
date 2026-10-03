from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.identity.domain.entities import User
from syncplaylists.modules.identity.domain.errors import InvalidEmailError
from syncplaylists.modules.identity.domain.events import UserRegistered
from syncplaylists.modules.identity.domain.value_objects import Email


def test_email_is_normalized() -> None:
    assert Email("  Alice@Example.COM ") == Email("alice@example.com")
    assert Email("  Alice@Example.COM ").value == "alice@example.com"


@pytest.mark.parametrize(
    "raw", ["", "alice", "alice@", "@example.com", "a b@example.com", "alice@example"]
)
def test_invalid_email_is_rejected(raw: str) -> None:
    with pytest.raises(InvalidEmailError):
        Email(raw)


def test_too_long_email_is_rejected() -> None:
    with pytest.raises(InvalidEmailError):
        Email("a" * 320 + "@example.com")


def test_register_records_event() -> None:
    user_id = uuid4()
    user = User.register(user_id, Email("a@example.com"), "hash", datetime.now(UTC))

    events = user.pull_domain_events()
    assert len(events) == 1
    assert isinstance(events[0], UserRegistered)
    assert events[0].user_id == user_id
