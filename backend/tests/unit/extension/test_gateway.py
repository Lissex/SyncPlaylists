"""Общий шлюз EXTENSION: операции из реестра, постраничные чтения, проверка ответов
расширения схемами, ключ идемпотентности создания плейлиста."""

from uuid import uuid4

import pytest

from syncplaylists.integrations.platforms.extension.gateway import ExtensionGateway
from syncplaylists.integrations.platforms.registry import PlatformGatewayFactory
from syncplaylists.modules.extension.application.operations import (
    OPERATIONS,
    error_from_wire,
    operation_spec,
)
from syncplaylists.modules.extension.presentation.config import ExtensionEndpointConfig
from syncplaylists.shared_kernel.application.ports import AccountAccess, PlatformCredentials
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
    PlatformNotSupportedError,
    PlatformRateLimitedError,
    PlatformUnavailableError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
    Transport,
)
from tests.fakes.extension import ScriptedChannel

_TRACK = {"id": "1", "title": "Starboy", "artist": "The Weeknd", "duration_ms": 230000}


def _access(transport: Transport = Transport.EXTENSION) -> AccountAccess:
    return AccountAccess(
        account_id=uuid4(),
        user_id=uuid4(),
        platform=Platform.VK,
        transport=transport,
        external_user_id="vk-1",
        credentials=None if transport is Transport.EXTENSION else PlatformCredentials("t"),
    )


def _gateway() -> tuple[ExtensionGateway, ScriptedChannel]:
    channel = ScriptedChannel()
    return ExtensionGateway(channel, _access()), channel


async def test_calls_carry_account_identity_and_operation() -> None:
    gateway, channel = _gateway()
    channel.reply("search_by_isrc", {"tracks": [{**_TRACK, "isrc": "usum71703861"}]})

    found = await gateway.search_by_isrc(ISRC("USUM71703861"))

    assert [t.ref for t in found] == [ExternalTrackRef(Platform.VK, "1")]
    assert found[0].isrc == ISRC("USUM71703861")
    call = channel.calls[0]
    assert (call.platform, call.external_user_id, call.operation) == (
        Platform.VK,
        "vk-1",
        "search_by_isrc",
    )


async def test_playlist_is_read_page_by_page() -> None:
    gateway, channel = _gateway()
    channel.reply(
        "playlist_page",
        {"tracks": [_TRACK], "next_cursor": "c1", "title": "Мой плейлист"},
        {"tracks": [{**_TRACK, "id": "2"}], "next_cursor": None},
    )

    snapshot = await gateway.get_playlist(PlaylistRef(Platform.VK, "p1"))

    assert snapshot.title == "Мой плейлист"
    assert [t.ref.external_id for t in snapshot.tracks] == ["1", "2"]
    assert [c.args["cursor"] for c in channel.calls] == [None, "c1"]


async def test_library_is_read_page_by_page() -> None:
    gateway, channel = _gateway()
    channel.reply(
        "library_page",
        {"tracks": [_TRACK], "next_cursor": "2"},
        {"tracks": [], "next_cursor": None},
    )

    tracks = [t async for t in gateway.get_library()]

    assert len(tracks) == 1
    assert len(channel.calls) == 2


async def test_create_playlist_uses_idempotency_key_from_request_id() -> None:
    gateway, channel = _gateway()
    channel.reply("create_playlist", {"playlist_id": "pl-1"})

    ref = await gateway.create_playlist("Копия", None, request_id="t1:1")

    assert ref == PlaylistRef(Platform.VK, "pl-1")
    assert channel.calls[0].idempotency_key == "create_playlist:t1:1"


async def test_write_timeout_grows_with_track_count() -> None:
    gateway, channel = _gateway()
    tracks = [ExternalTrackRef(Platform.VK, str(i)) for i in range(100)]
    channel.reply("add_tracks", {"added": [t.external_id for t in tracks], "failed": ["5", "x"]})

    result = await gateway.add_tracks(PlaylistRef(Platform.VK, "pl"), tracks)

    assert channel.calls[0].items == 100
    # Чужой id из ответа ("x") не принимается.
    assert result.failed == (ExternalTrackRef(Platform.VK, "5"),)
    assert len(result.added) == 99
    spec = operation_spec("add_tracks")
    assert spec.timeout_for(100) > spec.timeout_for(1)


async def test_response_with_extra_fields_is_rejected() -> None:
    """Расширение не может «протащить» лишнее (например, cookie) — ответ не по схеме."""
    gateway, channel = _gateway()
    channel.reply("search_by_isrc", {"tracks": [{**_TRACK, "cookie": "secret"}]})

    with pytest.raises(PlatformUnavailableError):
        await gateway.search_by_isrc(ISRC("USUM71703861"))


async def test_extension_unavailable_propagates() -> None:
    gateway, channel = _gateway()
    channel.reply(
        "search_by_isrc",
        ExtensionUnavailableError(Platform.VK, ExtensionUnavailableReason.OFFLINE),
    )

    with pytest.raises(ExtensionUnavailableError):
        await gateway.search_by_isrc(ISRC("USUM71703861"))


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("logged_out", ExtensionUnavailableError),
        ("captcha", ExtensionUnavailableError),
        ("session_mismatch", ExtensionUnavailableError),
        ("no_permission", ExtensionUnavailableError),
        ("not_found", PlaylistNotFoundError),
        ("not_writable", PlaylistNotWritableError),
        ("rate_limited", PlatformRateLimitedError),
        ("something_else", PlatformUnavailableError),
    ],
)
def test_wire_errors_map_to_platform_errors(code: str, expected: type[Exception]) -> None:
    assert isinstance(error_from_wire(Platform.VK, {"code": code}), expected)


def test_rate_limited_keeps_retry_after() -> None:
    error = error_from_wire(Platform.VK, {"code": "rate_limited", "retry_after": 600})
    assert isinstance(error, PlatformRateLimitedError)
    assert error.retry_after_seconds == 600


def test_every_operation_has_timeout() -> None:
    assert all(spec.timeout_seconds > 0 for spec in OPERATIONS.values())
    with pytest.raises(ValueError, match="Неизвестная"):
        operation_spec("eval")


def test_factory_routes_extension_accounts_to_extension_builders() -> None:
    server, browser = object(), object()
    factory = PlatformGatewayFactory(
        {Platform.YANDEX: lambda _: server},  # type: ignore[arg-type,return-value]
        {Platform.VK: lambda _: browser},  # type: ignore[arg-type,return-value]
    )

    assert factory.supports(Platform.VK)
    assert factory.for_account(_access()) is browser
    with pytest.raises(PlatformNotSupportedError):
        factory.for_account(_access(Transport.UNOFFICIAL))  # у VK нет серверного адаптера


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        (None, True),
        ("chrome-extension://abc", True),
        ("chrome-extension://other", False),
        ("moz-extension://8f1c-random-uuid", True),
        ("https://evil.example", False),
        ("http://localhost:5173", False),
    ],
)
def test_ws_origin_allowlist(origin: str | None, allowed: bool) -> None:
    config = ExtensionEndpointConfig(allowed_extension_ids=("abc",))
    assert config.origin_allowed(origin) is allowed
