import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.infrastructure.db.engine import create_engine
from syncplaylists.infrastructure.db.session import create_session_factory
from syncplaylists.infrastructure.security.aes_gcm import AesGcmTokenCipher
from syncplaylists.modules.accounts.application.access import AccountAccessService
from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.errors import AccountAlreadyConnectedError
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus, EncryptedToken
from syncplaylists.modules.accounts.infrastructure.orm import ConnectedAccountOrm
from syncplaylists.modules.accounts.infrastructure.repository import (
    SqlAccountCredentialsWriter,
    SqlConnectedAccountRepository,
)
from syncplaylists.modules.transfers.infrastructure.orm import TransferOrm
from syncplaylists.shared_kernel.application.ports import PlatformCredentials
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport


def _account(user_id: UUID, external_user_id: str = "vk-1") -> ConnectedAccount:
    return ConnectedAccount.connect(
        account_id=uuid4(),
        user_id=user_id,
        platform=Platform.VK,
        transport=Transport.UNOFFICIAL,
        external_user_id=external_user_id,
        display_name="Alice",
        access_token=EncryptedToken(b"\x01cipher-access"),
        refresh_token=None,
        expires_at=None,
        now=datetime.now(UTC),
    )


async def test_round_trips_account(session: AsyncSession, user_id: UUID) -> None:
    repo = SqlConnectedAccountRepository(session)
    account = _account(user_id)

    await repo.add(account)

    loaded = await repo.get(account.id)
    assert loaded is not None
    assert loaded.platform is Platform.VK
    assert loaded.access_token == EncryptedToken(b"\x01cipher-access")
    assert loaded.refresh_token is None
    assert await repo.find_active(user_id, Platform.VK) is not None
    assert await repo.find_by_external(user_id, Platform.VK, "vk-1") is not None
    assert [a.id for a in await repo.list_for_user(user_id)] == [account.id]


async def test_db_stores_ciphertext_not_plaintext(session: AsyncSession, user_id: UUID) -> None:
    cipher = AesGcmTokenCipher(os.urandom(32))
    repo = SqlConnectedAccountRepository(session)
    account = _account(user_id)
    account.access_token = EncryptedToken(cipher.encrypt("plain-vk-token", aad=b"a"))

    await repo.add(account)

    raw = await session.scalar(
        select(ConnectedAccountOrm.access_token_enc).where(ConnectedAccountOrm.id == account.id)
    )
    assert raw is not None
    assert b"plain-vk-token" not in raw


async def test_only_one_active_account_per_platform(session: AsyncSession, user_id: UUID) -> None:
    repo = SqlConnectedAccountRepository(session)
    await repo.add(_account(user_id, "vk-1"))

    with pytest.raises(AccountAlreadyConnectedError):
        await repo.add(_account(user_id, "vk-2"))


async def test_disconnected_account_does_not_block_new_one(
    session: AsyncSession, user_id: UUID
) -> None:
    repo = SqlConnectedAccountRepository(session)
    first = _account(user_id, "vk-1")
    await repo.add(first)
    first.disconnect(datetime.now(UTC))
    await repo.save(first)

    await repo.add(_account(user_id, "vk-2"))

    loaded = await repo.get(first.id)
    assert loaded is not None
    assert loaded.status is AccountStatus.DISCONNECTED
    assert loaded.access_token is None


async def test_status_check_constraint(session: AsyncSession, user_id: UUID) -> None:
    session.add(
        ConnectedAccountOrm(
            id=uuid4(),
            user_id=user_id,
            platform="vk",
            transport="unofficial",
            external_user_id="x",
            status="not_a_status",
            connected_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_transfer_user_fk(session: AsyncSession) -> None:
    session.add(
        TransferOrm(
            id=uuid4(),
            user_id=uuid4(),  # такого пользователя нет
            status="queued",
            source_kind="playlist",
            source_platform="vk",
            source_playlist_id="p",
            destination_kind="existing",
            target_platform="spotify",
            target_playlist_id="p",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_transfer_account_fk(session: AsyncSession, user_id: UUID) -> None:
    session.add(
        TransferOrm(
            id=uuid4(),
            user_id=user_id,
            status="queued",
            source_kind="library",
            source_platform="vk",
            source_account_id=uuid4(),  # такого аккаунта нет
            destination_kind="existing",
            target_platform="spotify",
            target_playlist_id="p",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_update_credentials_survives_rollback_of_outer_transaction(
    settings: Settings, session: AsyncSession, user_id: UUID
) -> None:
    cipher = AesGcmTokenCipher(settings.security.token_encryption_key_bytes())
    engine = create_engine(settings.db)
    factory = create_session_factory(engine)
    try:
        repo = SqlConnectedAccountRepository(session)
        account = _account(user_id)
        await repo.add(account)
        await session.commit()  # аккаунт и пользователь видны другим сессиям

        service = AccountAccessService(repo, cipher, SqlAccountCredentialsWriter(factory))
        # Внешняя «транзакция переноса»: что-то пишет, адаптер обновляет токены, потом
        # перенос падает и откатывается. (Строку connected_accounts внешняя транзакция
        # не трогает — иначе FOR UPDATE во writer ждал бы её лока.)
        transfer_id = uuid4()
        session.add(
            TransferOrm(
                id=transfer_id,
                user_id=user_id,
                status="queued",
                source_kind="playlist",
                source_platform="vk",
                source_playlist_id="p",
                destination_kind="existing",
                target_platform="spotify",
                target_playlist_id="p",
            )
        )
        await session.flush()
        await service.update_credentials(
            account.id, PlatformCredentials(access_token="rotated", refresh_token="new-refresh")
        )
        await session.rollback()

        async with factory() as fresh:
            access = await AccountAccessService(
                SqlConnectedAccountRepository(fresh), cipher, SqlAccountCredentialsWriter(factory)
            ).get(user_id, account.id)
            rolled_back_transfer = await fresh.get(TransferOrm, transfer_id)
        assert access.credentials.access_token == "rotated"
        assert access.credentials.refresh_token == "new-refresh"
        assert rolled_back_transfer is None  # внешняя транзакция действительно откатилась
    finally:
        await engine.dispose()
