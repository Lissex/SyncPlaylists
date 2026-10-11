"""VK (4c-5): id, проекция трека → кандидат, ключи «тот же трек» для отсева копий."""

import json
from pathlib import Path

import pytest

from syncplaylists.integrations.platforms.vk.extension_wire import VkSearch, VkTrack
from syncplaylists.integrations.platforms.vk.ids import VkPlaylistId, VkTrackId
from syncplaylists.integrations.platforms.vk.mapping import identity, is_official, to_candidate
from syncplaylists.shared_kernel.domain.value_objects import Duration, Platform

_FIXTURES = Path(__file__).parents[3] / "fixtures" / "vk"


def _search() -> list[VkTrack]:
    raw = json.loads((_FIXTURES / "search_call_back.json").read_text(encoding="utf-8"))
    raw.pop("_comment")
    return VkSearch.model_validate(raw).tracks


def test_track_id_roundtrip() -> None:
    track = VkTrackId.parse("-2001400099_99400099_0a1b2c")
    assert (track.owner_id, track.audio_id, track.access_key) == (-2001400099, 99400099, "0a1b2c")
    assert track.plain == "-2001400099_99400099"
    assert str(track) == "-2001400099_99400099_0a1b2c"
    assert str(VkTrackId.parse("1_2")) == "1_2"
    with pytest.raises(ValueError, match="не трек VK"):
        VkTrackId.parse("1_2_bad-key")


def test_playlist_id_accepts_generated_negative_id() -> None:
    playlist = VkPlaylistId.parse("415573253_-24_3f450d61e179c9ced6")
    assert (playlist.owner_id, playlist.playlist_id) == (415573253, -24)
    assert str(playlist) == "415573253_-24_3f450d61e179c9ced6"


def test_only_release_with_main_artists_is_official() -> None:
    official, unreleased, v3, caps, _own_copy = _search()
    assert is_official(official)
    assert not is_official(unreleased)
    assert not is_official(v3)  # привязка к релизу есть, а main_artists нет
    assert not is_official(caps)


def test_candidate_from_projection() -> None:
    candidate = to_candidate(_search()[0])
    assert candidate is not None
    assert candidate.ref.platform is Platform.VK
    assert candidate.ref.external_id == "474499302_456949008_0a1b2c3d4e5f6a7b8c"
    assert (candidate.title, candidate.artist) == ("Call Back", "Don Toliver")
    assert candidate.duration == Duration(124_000)
    assert candidate.rights_holder
    assert candidate.cover_url == "https://sun9-1.userapi.com/impg/fixture-600.jpg"
    assert candidate.isrc is None


def test_subtitle_goes_into_title_and_feat_into_artists() -> None:
    track = VkTrack(
        owner_id=1,
        id=2,
        artist="Kanye West feat. Pusha T",
        title="Runaway",
        subtitle="Live",
        duration=500,
        main_artists=["Kanye West"],
        featured_artists=["Pusha T"],
    )
    candidate = to_candidate(track)
    assert candidate is not None
    assert candidate.title == "Runaway (Live)"
    assert candidate.artist == "Kanye West, Pusha T"
    assert candidate.artists == ("Kanye West", "Pusha T")


def test_copy_is_recognised_by_release_or_text() -> None:
    official, _, _, caps, own_copy = _search()
    assert identity(official) & identity(own_copy)  # тот же релиз
    # Перезалив без релиза: тот же текст и длительность (регистр не важен).
    assert identity(caps) & identity(
        VkTrack(owner_id=7, id=8, artist="Don Toliver", title="Call back", duration=124)
    )
    assert not identity(caps) & identity(
        VkTrack(owner_id=7, id=8, artist="Don Toliver", title="Call Back", duration=200)
    )
