"""HTTP- и WebSocket-схемы расширения. Все входящие сообщения — extra="forbid": лишнее
поле (например, cookie или токен площадки, попавшие туда по ошибке расширения) сервер
отвергает, а не молча принимает (ARCHITECTURE.md, 11h)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from syncplaylists.modules.extension.application.dto import DeviceDto
from syncplaylists.modules.extension.domain.value_objects import SessionState
from syncplaylists.shared_kernel.domain.value_objects import Platform

_Short = Annotated[str, Field(min_length=1, max_length=100)]
_ExternalId = Annotated[str, Field(min_length=1, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- HTTP


class StartPairingRequest(_Strict):
    device_name: _Short
    browser: Annotated[str, Field(min_length=1, max_length=40)]
    version: Annotated[str, Field(min_length=1, max_length=40)]


class StartPairingResponse(BaseModel):
    pairing_id: str
    user_code: str
    expires_in: int
    interval: int


class ConfirmPairingRequest(_Strict):
    user_code: Annotated[str, Field(min_length=4, max_length=20)]


class ClaimPairingRequest(_Strict):
    pairing_id: Annotated[str, Field(min_length=10, max_length=100)]


class ClaimPairingResponse(BaseModel):
    status: Literal["pending", "paired"]
    device_id: UUID | None = None
    device_token: str | None = None


class DeviceSchema(BaseModel):
    id: UUID
    name: str
    browser: str
    version: str
    created_at: datetime
    last_seen_at: datetime | None
    revoked: bool

    @classmethod
    def from_dto(cls, dto: DeviceDto) -> "DeviceSchema":
        return cls(
            id=dto.id,
            name=dto.name,
            browser=dto.browser,
            version=dto.version,
            created_at=dto.created_at,
            last_seen_at=dto.last_seen_at,
            revoked=dto.revoked,
        )


class DeviceMeResponse(BaseModel):
    """Расширение показывает, к какому аккаунту SyncPlaylists оно привязано."""

    device_id: UUID
    user_email: str


# ---------------------------------------------------------------- WebSocket: расширение → сервер


class HelloMessage(_Strict):
    type: Literal["hello"]
    token: Annotated[str, Field(min_length=10, max_length=200)]
    version: Annotated[str, Field(min_length=1, max_length=40)]


class PingMessage(_Strict):
    type: Literal["ping"]


class PlatformStateMessage(_Strict):
    type: Literal["platform_state"]
    platform: Platform
    session: SessionState
    external_user_id: _ExternalId | None = None


class ConnectPlatformMessage(_Strict):
    """Пользователь в расширении явно нажал «Подключить <площадку>»."""

    type: Literal["connect_platform"]
    request_id: _Short
    platform: Platform
    external_user_id: _ExternalId
    display_name: Annotated[str, Field(max_length=200)] | None = None


class TaskError(_Strict):
    code: Annotated[str, Field(min_length=1, max_length=40)]
    message: Annotated[str, Field(max_length=500)] = ""
    retry_after: float | None = None


class ResultMessage(_Strict):
    type: Literal["result"]
    task_id: Annotated[str, Field(min_length=1, max_length=64)]
    ok: bool
    # Тело зависит от операции; его проверяет схема операции в шлюзе (тоже extra="forbid").
    data: dict[str, Any] | None = None
    error: TaskError | None = None


class ProgressMessage(_Strict):
    type: Literal["progress"]
    task_id: Annotated[str, Field(min_length=1, max_length=64)]


ClientMessage = Annotated[
    PingMessage | PlatformStateMessage | ConnectPlatformMessage | ResultMessage | ProgressMessage,
    Field(discriminator="type"),
]
