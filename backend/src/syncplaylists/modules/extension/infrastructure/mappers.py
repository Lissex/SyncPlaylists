from syncplaylists.modules.extension.domain.entities import ExtensionDevice
from syncplaylists.modules.extension.infrastructure.orm import ExtensionDeviceOrm


def device_to_orm(device: ExtensionDevice) -> ExtensionDeviceOrm:
    return ExtensionDeviceOrm(
        id=device.id,
        user_id=device.user_id,
        name=device.name,
        browser=device.browser,
        version=device.version,
        token_hash=device.token_hash,
        created_at=device.created_at,
        last_seen_at=device.last_seen_at,
        revoked_at=device.revoked_at,
    )


def device_to_domain(orm: ExtensionDeviceOrm) -> ExtensionDevice:
    return ExtensionDevice(
        id=orm.id,
        user_id=orm.user_id,
        name=orm.name,
        browser=orm.browser,
        version=orm.version,
        token_hash=orm.token_hash,
        created_at=orm.created_at,
        last_seen_at=orm.last_seen_at,
        revoked_at=orm.revoked_at,
    )
