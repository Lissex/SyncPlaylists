"""ExtensionVkApi (4c-5) на ScriptedChannel: какие операции и аргументы уходят в
браузер, строгая схема ответа (ссылка на аудио в ответе — нарушение контракта),
постраничное чтение и коды ошибок."""

from typing import Any
from uuid import uuid4

import pytest

from syncplaylists.integrations.platforms.vk.api import ExtensionVkApi, VkBadRequestError
from syncplaylists.integrations.platforms.vk.ids import VkPlaylistId, VkTrackId
from syncplaylists.modules.extension.application.operations import ExtensionOperationError
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import PlatformUnavailableError
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport
from tests.fakes.extension import ScriptedChannel

_ACCESS = AccountAccess(
    account_id=uuid4(),
    user_id=uuid4(),
    platform=Platform.VK,
    transport=Transport.EXTENSION,
    external_user_id="100500",
    credentials=None,
)
_PLAYLIST = {"owner_id": 100500, "id": 158, "access_key": "abc", "title": "P", "count": 3}


def _track(audio_id: int) -> dict[str, Any]:
    return {"owner_id": 1, "id": audio_id, "artist": "A", "title": f"T{audio_id}", "duration": 100}


def _page(ids: list[int], total: int, next_offset: int | None) -> dict[str, Any]:
    page: dict[str, Any] = {
        "playlist": _PLAYLIST,
        "total": total,
        "tracks": [_track(i) for i in ids],
    }
    if next_offset is not None:
        page["next_offset"] = next_offset
    return page


def _api(channel: ScriptedChannel, page_size: int = 2) -> ExtensionVkApi:
    return ExtensionVkApi(channel, _ACCESS, page_size=page_size)


async def test_playlist_is_read_page_by_page() -> None:
    channel = ScriptedChannel()
    channel.reply("playlist_page", _page([1, 2], 3, 2), _page([3], 3, None))

    header, tracks = await _api(channel).playlist(VkPlaylistId(100500, 158, "abc"))

    assert header.id == 158
    assert [t.id for t in tracks] == [1, 2, 3]
    assert [c.args for c in channel.calls] == [
        {"owner_id": 100500, "playlist_id": 158, "access_key": "abc", "offset": 0, "limit": 2},
        {"owner_id": 100500, "playlist_id": 158, "access_key": "abc", "offset": 2, "limit": 2},
    ]
    assert all(c.platform is Platform.VK and c.external_user_id == "100500" for c in channel.calls)


async def test_playlist_changed_between_pages_is_read_again() -> None:
    channel = ScriptedChannel()
    channel.reply(
        "playlist_page",
        _page([1, 2], 3, 2),
        _page([3, 4], 4, None),  # добавили трек посреди чтения
        _page([1, 2], 4, 2),
        _page([3, 4], 4, None),
    )

    _, tracks = await _api(channel).playlist(VkPlaylistId(100500, 158))

    assert [t.id for t in tracks] == [1, 2, 3, 4]


async def test_audio_url_in_response_breaks_contract() -> None:
    channel = ScriptedChannel()
    channel.reply("search", {"tracks": [{**_track(1), "url": "https://cs1.vkuseraudio.net/x.mp3"}]})

    with pytest.raises(PlatformUnavailableError):
        await _api(channel).search("x", 10)


async def test_writes_carry_keys_and_items() -> None:
    channel = ScriptedChannel()
    channel.reply("create_playlist", {"owner_id": 100500, "id": 158, "access_key": "abc"})
    channel.reply("set_playlist_tracks", {})
    channel.reply("add_to_library", {"owner_id": 100500, "id": 901})
    api = _api(channel)

    await api.create_playlist("Перенос", "", idempotency_key="create_playlist:t:1")
    await api.set_playlist_tracks(158, ["1_1", "2_2"], idempotency_key="k")
    added = await api.add_to_library(VkTrackId(-2001, 5, "ff"), idempotency_key="lib")

    create, replace, library = channel.calls
    assert (create.args, create.idempotency_key) == (
        {"title": "Перенос", "description": ""},
        "create_playlist:t:1",
    )
    assert (replace.args, replace.items) == ({"playlist_id": 158, "audio_ids": ["1_1", "2_2"]}, 2)
    assert library.args == {"owner_id": -2001, "audio_id": 5, "access_key": "ff"}
    assert (added.owner_id, added.id) == (100500, 901)


async def test_bad_request_becomes_vk_error_other_codes_pass() -> None:
    channel = ScriptedChannel()
    channel.reply(
        "set_playlist_tracks",
        ExtensionOperationError(Platform.VK, "bad_request"),
        ExtensionOperationError(Platform.VK, "something"),
    )
    api = _api(channel)

    with pytest.raises(VkBadRequestError):
        await api.set_playlist_tracks(1, ["1_1"], idempotency_key=None)
    with pytest.raises(ExtensionOperationError):
        await api.set_playlist_tracks(1, ["1_1"], idempotency_key=None)


async def test_tracks_are_loaded_by_hundreds() -> None:
    channel = ScriptedChannel()
    channel.reply("tracks", {"tracks": [_track(1)]}, {"tracks": [_track(2)]})

    loaded = await _api(channel).tracks([VkTrackId(1, i) for i in range(150)])

    assert [len(c.args["ids"]) for c in channel.calls] == [100, 50]
    assert [t.id for t in loaded] == [1, 2]
