from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.modules.identity.domain.entities import User
from syncplaylists.modules.identity.domain.errors import EmailAlreadyRegisteredError
from syncplaylists.modules.identity.domain.value_objects import Email
from syncplaylists.modules.identity.infrastructure.repository import SqlUserRepository


def _user(email: str) -> User:
    return User.register(uuid4(), Email(email), "hash", datetime.now(UTC))


async def test_round_trips_user(session: AsyncSession) -> None:
    repo = SqlUserRepository(session)
    user = _user(f"{uuid4().hex}@example.com")

    await repo.add(user)

    by_id = await repo.get(user.id)
    by_email = await repo.get_by_email(user.email)
    assert by_id is not None
    assert by_email is not None
    assert by_id.id == by_email.id == user.id
    assert by_id.email == user.email
    assert by_id.password_hash == "hash"


async def test_duplicate_email_is_domain_error_and_keeps_transaction_usable(
    session: AsyncSession,
) -> None:
    repo = SqlUserRepository(session)
    email = f"{uuid4().hex}@example.com"
    first = _user(email)
    await repo.add(first)

    with pytest.raises(EmailAlreadyRegisteredError):
        await repo.add(_user(email))

    # SAVEPOINT откатил только неудачную вставку — внешняя транзакция жива.
    assert await repo.get(first.id) is not None
