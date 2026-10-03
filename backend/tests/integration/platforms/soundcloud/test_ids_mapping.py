"""Форматы id SoundCloud и маппинг трека — без сети."""

from datetime import UTC, datetime

import pytest

from syncplaylists.integrations.platforms.soundcloud.client_id import (
    extract_client_id,
    extract_script_urls,
)
from syncplaylists.integrations.platforms.soundcloud.ids import (
    SoundCloudPlaylistId,
    parse_track_id,
)
from syncplaylists.integrations.platforms.soundcloud.mapping import to_candidate
from syncplaylists.integrations.platforms.soundcloud.tokens import (
    token_client_id,
    token_expires_at,
)
from syncplaylists.shared_kernel.domain.search import TrackRestriction
from syncplaylists.shared_kernel.domain.value_objects import ISRC, Duration
from tests.integration.platforms.soundcloud.conftest import (
    BUNDLE_1,
    BUNDLE_2,
    CLIENT_ID,
    bundle_with,
    fixture,
    jwt,
    site_html,
)


def test_playlist_id_formats() -> None:
    assert SoundCloudPlaylistId.parse("dj-x/sets/road-trip") == SoundCloudPlaylistId(
        path="dj-x/sets/road-trip"
    )
    secret = SoundCloudPlaylistId.parse("dj-x/sets/road-trip/s-AbC123")
    assert (secret.path, secret.secret_token) == ("dj-x/sets/road-trip/s-AbC123", "s-AbC123")
    assert secret.url == "https://soundcloud.com/dj-x/sets/road-trip/s-AbC123"
    assert SoundCloudPlaylistId.parse("5100:s-NeW999") == SoundCloudPlaylistId(
        playlist_id=5100, secret_token="s-NeW999"
    )
    assert SoundCloudPlaylistId.parse("5100") == SoundCloudPlaylistId(playlist_id=5100)
    assert SoundCloudPlaylistId.parse("dj-x/likes") == SoundCloudPlaylistId(likes_of="dj-x")
    assert SoundCloudPlaylistId.for_created(5100, "s-NeW999") == "5100:s-NeW999"
    assert SoundCloudPlaylistId.for_created(5100, None) == "5100"


@pytest.mark.parametrize("raw", ["", "dj-x", "dj-x/tracks", "5100:secret", "a/sets/b/c"])
def test_playlist_id_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError, match="плейлиста SoundCloud"):
        SoundCloudPlaylistId.parse(raw)


def test_track_id() -> None:
    assert parse_track_id("328259811") == 328259811
    with pytest.raises(ValueError, match="трека SoundCloud"):
        parse_track_id("abc")


def test_client_id_extraction() -> None:
    assert extract_script_urls(site_html()) == [BUNDLE_1, BUNDLE_2]
    assert extract_client_id(bundle_with(CLIENT_ID)) == CLIENT_ID
    assert extract_client_id('e.client_id="' + CLIENT_ID + '"') == CLIENT_ID
    assert extract_client_id("var nothing=1;") is None


def test_script_urls_only_from_allowed_hosts() -> None:
    html = '<script src="https://evil.example/a.js"></script><script src="http://a-v2.sndcdn.com/x.js">'
    assert extract_script_urls(html) == []


def test_original_upload_mapping() -> None:
    candidate = to_candidate(fixture("search_tracks")["collection"][1])
    assert candidate is not None
    assert candidate.ref.external_id == "1001"
    assert (candidate.title, candidate.artist) == ("Lucid Dreams", "Juice WRLD")
    assert candidate.isrc == ISRC("USUG11800685")
    assert candidate.duration == Duration(239882)
    assert candidate.uploader == "Juice WRLD"
    assert candidate.rights_holder is True
    assert candidate.cover_url == "https://i1.sndcdn.com/artworks-1001-t500x500.jpg"
    assert candidate.restriction is None


def test_reupload_mapping_uses_uploader_as_artist() -> None:
    candidate = to_candidate(fixture("search_tracks")["collection"][0])
    assert candidate is not None
    # «Juice WRLD - Lucid Dreams [prod. ...]» разберёт нормализатор.
    assert candidate.title == "Juice WRLD - Lucid Dreams [prod. Nick Mira]"
    assert candidate.artist == "trap.tunes.daily"
    assert candidate.rights_holder is False
    assert candidate.isrc is None


def test_snip_uses_full_duration_and_marks_preview_only() -> None:
    candidate = to_candidate(fixture("search_tracks")["collection"][3])
    assert candidate is not None
    assert candidate.duration == Duration(240483)  # не 30-секундное превью
    assert candidate.restriction is TrackRestriction.PREVIEW_ONLY
    assert candidate.artist == "Juice WRLD, Lil Uzi Vert"


def test_stub_and_bad_isrc() -> None:
    assert to_candidate({"id": 1, "kind": "track", "policy": "ALLOW"}) is None
    track = dict(fixture("search_tracks")["collection"][1])
    track["publisher_metadata"] = {"artist": "X", "isrc": "not-an-isrc"}
    candidate = to_candidate(track)
    assert candidate is not None
    assert candidate.isrc is None


def test_jwt_claims() -> None:
    token = jwt({"exp": 1_790_000_000, "client_id": "web-client"})
    assert token_expires_at(token) == datetime.fromtimestamp(1_790_000_000, UTC)
    assert token_client_id(token) == "web-client"
    assert token_expires_at("2-000001-900001-legacy") is None
    assert token_client_id("not.a-jwt.!!!") is None
