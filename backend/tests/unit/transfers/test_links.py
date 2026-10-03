from uuid import uuid4

import pytest

from syncplaylists.modules.transfers.application.links import (
    LinkKind,
    ResolvePlaylistLinkUseCase,
)
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    LibraryDestination,
    LibrarySource,
    PlaylistSource,
)
from syncplaylists.shared_kernel.application.ports import AccountNotAvailableError
from syncplaylists.shared_kernel.domain.errors import (
    PlatformNotSupportedError,
    UnsupportedLinkError,
)
from syncplaylists.shared_kernel.domain.links import LinkResolver
from syncplaylists.shared_kernel.domain.value_objects import Platform, PlaylistRef
from tests.fakes import FakeGatewayFactory, FakeMusicPlatformGateway
from tests.fakes.accounts import FakeAccountAccessProvider

_LIKES = "https://music.yandex.ru/users/alice/playlists/3"
_PLAYLIST = "https://music.yandex.ru/users/alice/playlists/1001"


class _NoExpander:
    def is_short_link(self, host: str) -> bool:
        return False

    async def expand(self, url: str) -> str:
        raise AssertionError("не должен вызываться")


class _Env:
    def __init__(self) -> None:
        self.user_id = uuid4()
        self.accounts = FakeAccountAccessProvider()
        self.gateways = FakeGatewayFactory()
        self.use_case = ResolvePlaylistLinkUseCase(
            LinkResolver(_NoExpander()), self.gateways, self.accounts
        )


async def test_own_likes_link_resolves_to_library_of_connected_account() -> None:
    env = _Env()
    access = env.accounts.connect(env.user_id, Platform.YANDEX)
    env.gateways.register(
        FakeMusicPlatformGateway(
            platform=Platform.YANDEX,
            own_library_refs={PlaylistRef(Platform.YANDEX, "alice:3")},
        )
    )

    dto = await env.use_case.execute(env.user_id, _LIKES)

    assert dto.kind is LinkKind.LIBRARY
    assert dto.account_id == access.account_id
    assert dto.as_source() == LibrarySource(Platform.YANDEX, access.account_id)
    assert dto.as_destination() == LibraryDestination(Platform.YANDEX, access.account_id)


async def test_someone_elses_likes_link_stays_a_playlist() -> None:
    env = _Env()
    env.accounts.connect(env.user_id, Platform.YANDEX)
    env.gateways.register(FakeMusicPlatformGateway(platform=Platform.YANDEX))  # не своя

    dto = await env.use_case.execute(env.user_id, _LIKES)

    assert dto.kind is LinkKind.PLAYLIST
    assert dto.external_id == "alice:3"
    assert dto.as_source() == PlaylistSource(PlaylistRef(Platform.YANDEX, "alice:3"))


async def test_likes_link_without_connected_account_stays_a_playlist() -> None:
    env = _Env()

    dto = await env.use_case.execute(env.user_id, _LIKES)

    assert dto.kind is LinkKind.PLAYLIST
    assert dto.external_id == "alice:3"
    assert dto.title is None


async def test_playlist_link_with_account_includes_preview() -> None:
    env = _Env()
    env.accounts.connect(env.user_id, Platform.YANDEX)

    dto = await env.use_case.execute(env.user_id, _PLAYLIST)

    assert dto.kind is LinkKind.PLAYLIST
    assert dto.title == "Fake playlist"
    assert dto.as_destination() == ExistingPlaylist(PlaylistRef(Platform.YANDEX, "alice:1001"))


async def test_unambiguous_library_link_requires_connected_account() -> None:
    env = _Env()

    with pytest.raises(AccountNotAvailableError):
        await env.use_case.execute(env.user_id, "https://open.spotify.com/collection/tracks")


async def test_link_to_platform_without_adapter_is_not_supported() -> None:
    env = _Env()
    env.gateways.unsupported.add(Platform.VK)

    with pytest.raises(PlatformNotSupportedError):
        await env.use_case.execute(env.user_id, "https://vk.com/music/playlist/1_2")


async def test_bad_link_is_rejected_with_reason() -> None:
    env = _Env()

    with pytest.raises(UnsupportedLinkError) as caught:
        await env.use_case.execute(env.user_id, "https://music.yandex.ru/album/1")
    assert caught.value.reason == "not_a_playlist"
