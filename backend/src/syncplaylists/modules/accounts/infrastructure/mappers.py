from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus, EncryptedToken
from syncplaylists.modules.accounts.infrastructure.orm import ConnectedAccountOrm
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport


def account_to_domain(orm: ConnectedAccountOrm) -> ConnectedAccount:
    return ConnectedAccount(
        id=orm.id,
        user_id=orm.user_id,
        platform=Platform(orm.platform),
        transport=Transport(orm.transport),
        external_user_id=orm.external_user_id,
        display_name=orm.display_name,
        access_token=(
            EncryptedToken(orm.access_token_enc) if orm.access_token_enc is not None else None
        ),
        refresh_token=(
            EncryptedToken(orm.refresh_token_enc) if orm.refresh_token_enc is not None else None
        ),
        expires_at=orm.expires_at,
        status=AccountStatus(orm.status),
        connected_at=orm.connected_at,
    )


def apply_to_orm(account: ConnectedAccount, orm: ConnectedAccountOrm) -> ConnectedAccountOrm:
    orm.id = account.id
    orm.user_id = account.user_id
    orm.platform = account.platform.value
    orm.transport = account.transport.value
    orm.external_user_id = account.external_user_id
    orm.display_name = account.display_name
    orm.access_token_enc = account.access_token.ciphertext if account.access_token else None
    orm.refresh_token_enc = account.refresh_token.ciphertext if account.refresh_token else None
    orm.expires_at = account.expires_at
    orm.status = account.status.value
    orm.connected_at = account.connected_at
    return orm
