"""LinkResolver на реальных форматах ссылок площадок (то, что отдаёт кнопка
«Поделиться» в веб-версии и в мобильных приложениях)."""

import pytest

from syncplaylists.shared_kernel.domain.errors import UnsupportedLinkError
from syncplaylists.shared_kernel.domain.links import (
    LibraryLink,
    LinkResolver,
    PlaylistLink,
    extract_url,
    parse_link,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform, PlaylistRef


def _playlist(platform: Platform, external_id: str) -> PlaylistLink:
    return PlaylistLink(PlaylistRef(platform, external_id))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # --- Яндекс Музыка ---
        (
            "https://music.yandex.ru/users/music-blog/playlists/2753",
            _playlist(Platform.YANDEX, "music-blog:2753"),
        ),
        (
            "https://music.yandex.ru/users/ya.playlist/playlists/1076?utm_source=desktop&utm_medium=copy_link",
            _playlist(Platform.YANDEX, "ya.playlist:1076"),
        ),
        (
            "https://music.yandex.com/users/some.user/playlists/1000/",
            _playlist(Platform.YANDEX, "some.user:1000"),
        ),
        (
            "https://music.yandex.by/users/user-1/playlists/7",
            _playlist(Platform.YANDEX, "user-1:7"),
        ),
        (
            "https://music.yandex.kz/users/user_1/playlists/7",
            _playlist(Platform.YANDEX, "user_1:7"),
        ),
        (
            # «Мне нравится» тоже плейлист kind=3 — чья это медиатека, решает application.
            "https://music.yandex.ru/users/alice/playlists/3",
            _playlist(Platform.YANDEX, "alice:3"),
        ),
        (
            "https://music.yandex.ru/playlists/lk.6ad1ba0c-30fe-4b39-a4c6-2b53e1c4c0e8",
            _playlist(Platform.YANDEX, "lk.6ad1ba0c-30fe-4b39-a4c6-2b53e1c4c0e8"),
        ),
        (
            "https://music.yandex.ru/playlists/ar.0a6c4cfd-8e1c-4f63-b0f2-2c5b1f8c7c62?utm_source=web",
            _playlist(Platform.YANDEX, "ar.0a6c4cfd-8e1c-4f63-b0f2-2c5b1f8c7c62"),
        ),
        (
            "https://next.music.yandex.ru/playlists/lk.6ad1ba0c-30fe-4b39-a4c6-2b53e1c4c0e8",
            _playlist(Platform.YANDEX, "lk.6ad1ba0c-30fe-4b39-a4c6-2b53e1c4c0e8"),
        ),
        # --- Spotify ---
        (
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            _playlist(Platform.SPOTIFY, "37i9dQZF1DXcBWIGoYBM5M"),
        ),
        (
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=1a2b3c4d5e6f4a7b",
            _playlist(Platform.SPOTIFY, "37i9dQZF1DXcBWIGoYBM5M"),
        ),
        (
            "https://open.spotify.com/intl-ru/playlist/37i9dQZF1DX4Wsb4d7NKfP?si=abc",
            _playlist(Platform.SPOTIFY, "37i9dQZF1DX4Wsb4d7NKfP"),
        ),
        (
            "https://open.spotify.com/intl-pt-br/playlist/37i9dQZF1DX4Wsb4d7NKfP",
            _playlist(Platform.SPOTIFY, "37i9dQZF1DX4Wsb4d7NKfP"),
        ),
        (
            "https://open.spotify.com/user/spotify/playlist/37i9dQZF1DXcBWIGoYBM5M",
            _playlist(Platform.SPOTIFY, "37i9dQZF1DXcBWIGoYBM5M"),
        ),
        (
            "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
            _playlist(Platform.SPOTIFY, "37i9dQZF1DXcBWIGoYBM5M"),
        ),
        (
            "spotify:user:spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
            _playlist(Platform.SPOTIFY, "37i9dQZF1DXcBWIGoYBM5M"),
        ),
        ("https://open.spotify.com/collection/tracks", LibraryLink(Platform.SPOTIFY)),
        # --- VK ---
        (
            "https://vk.com/music/playlist/-147845620_2949_ba3f9a6d26c5e5e8d1",
            _playlist(Platform.VK, "-147845620_2949_ba3f9a6d26c5e5e8d1"),
        ),
        (
            "https://vk.ru/music/playlist/12345678_42",
            _playlist(Platform.VK, "12345678_42"),
        ),
        (
            "https://m.vk.com/music/playlist/12345678_42_0f1e2d3c4b5a",
            _playlist(Platform.VK, "12345678_42_0f1e2d3c4b5a"),
        ),
        (
            "https://vk.com/music/album/-2000123456_7654321_5ec9b3f0a1d2c3b4e5",
            _playlist(Platform.VK, "-2000123456_7654321_5ec9b3f0a1d2c3b4e5"),
        ),
        (
            "https://vk.com/audios12345678?z=audio_playlist-147845620_2949%2Fba3f9a6d26c5e5e8d1",
            _playlist(Platform.VK, "-147845620_2949_ba3f9a6d26c5e5e8d1"),
        ),
        (
            "https://vk.com/music?z=audio_playlist12345678_42",
            _playlist(Platform.VK, "12345678_42"),
        ),
        (
            "https://m.vk.com/audio?act=audio_playlist-147845620_2949&access_hash=ba3f9a6d26c5",
            _playlist(Platform.VK, "-147845620_2949_ba3f9a6d26c5"),
        ),
        (
            "https://vk.com/audio_playlist-147845620_2949",
            _playlist(Platform.VK, "-147845620_2949"),
        ),
        # --- SoundCloud ---
        (
            "https://soundcloud.com/lofi_girl/sets/lofi-hip-hop-beats",
            _playlist(Platform.SOUNDCLOUD, "lofi_girl/sets/lofi-hip-hop-beats"),
        ),
        (
            "https://soundcloud.com/user-123/sets/my-set/s-AbC123xYz?si=0f1e&utm_source=clipboard",
            _playlist(Platform.SOUNDCLOUD, "user-123/sets/my-set/s-AbC123xYz"),
        ),
        (
            "https://m.soundcloud.com/lofi_girl/sets/lofi-hip-hop-beats",
            _playlist(Platform.SOUNDCLOUD, "lofi_girl/sets/lofi-hip-hop-beats"),
        ),
        ("https://soundcloud.com/you/likes", LibraryLink(Platform.SOUNDCLOUD)),
        (
            "https://soundcloud.com/some-user/likes",
            _playlist(Platform.SOUNDCLOUD, "some-user/likes"),
        ),
        # --- YouTube Music / YouTube ---
        (
            "https://music.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
            _playlist(Platform.YTMUSIC, "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"),
        ),
        (
            "https://music.youtube.com/playlist?list=OLAK5uy_kRt9tYjQ6CNmqrZ4KqH0xF7mBbXo9w1Q0&si=x",
            _playlist(Platform.YTMUSIC, "OLAK5uy_kRt9tYjQ6CNmqrZ4KqH0xF7mBbXo9w1Q0"),
        ),
        (
            "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
            _playlist(Platform.YTMUSIC, "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"),
        ),
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI&index=2",
            _playlist(Platform.YTMUSIC, "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"),
        ),
        (
            "https://youtu.be/dQw4w9WgXcQ?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
            _playlist(Platform.YTMUSIC, "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"),
        ),
        (
            "https://music.youtube.com/browse/VLPLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
            _playlist(Platform.YTMUSIC, "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"),
        ),
        (
            "https://music.youtube.com/playlist?list=VLPLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
            _playlist(Platform.YTMUSIC, "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"),
        ),
        ("https://music.youtube.com/playlist?list=LM", LibraryLink(Platform.YTMUSIC)),
    ],
)
def test_parse_link_real_formats(url: str, expected: PlaylistLink | LibraryLink) -> None:
    assert parse_link(url) == expected


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://music.yandex.ru/album/4766/track/57703", "not_a_playlist"),
        ("https://music.yandex.ru/album/4766", "not_a_playlist"),
        ("https://music.yandex.ru/artist/36800", "not_a_playlist"),
        ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", "not_a_playlist"),
        ("https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3", "not_a_playlist"),
        ("https://vk.com/id12345678", "not_a_playlist"),
        ("https://soundcloud.com/lofi_girl/some-track", "not_a_playlist"),
        ("https://soundcloud.com/discover/likes", "not_a_playlist"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "not_a_playlist"),
        ("https://www.youtube.com/playlist?list=LL", "not_a_playlist"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ", "mix_not_supported"),
        ("https://example.com/users/alice/playlists/3", "unknown_host"),
        ("https://music.yandex.ru.evil.com/users/alice/playlists/3", "unknown_host"),
        ("ftp://music.yandex.ru/users/alice/playlists/3", "invalid_url"),
        ("javascript:alert(1)", "invalid_url"),
    ],
)
def test_parse_link_rejects_non_playlists(url: str, reason: str) -> None:
    with pytest.raises(UnsupportedLinkError) as caught:
        parse_link(url)
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "  https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M  ",
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        ),
        (
            "Слушай плейлист «Хиты» на Яндекс Музыке: https://music.yandex.ru/users/a/playlists/5.",
            "https://music.yandex.ru/users/a/playlists/5",
        ),
        ("music.yandex.ru/users/a/playlists/5", "https://music.yandex.ru/users/a/playlists/5"),
    ],
)
def test_extract_url_from_shared_text(raw: str, expected: str) -> None:
    assert extract_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "просто текст", "x" * 3000])
def test_extract_url_rejects_garbage(raw: str) -> None:
    with pytest.raises(UnsupportedLinkError):
        extract_url(raw)


class _StubExpander:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping
        self.expanded: list[str] = []

    def is_short_link(self, host: str) -> bool:
        return host in {"vk.cc", "on.soundcloud.com", "spotify.link"}

    async def expand(self, url: str) -> str:
        self.expanded.append(url)
        return self._mapping[url]


@pytest.mark.parametrize(
    ("short", "full", "expected"),
    [
        (
            "https://vk.cc/cAbCdE",
            "https://vk.com/music/playlist/-147845620_2949_ba3f9a6d26c5e5e8d1",
            _playlist(Platform.VK, "-147845620_2949_ba3f9a6d26c5e5e8d1"),
        ),
        (
            "https://on.soundcloud.com/Xy7ZkQ3Wb2mN8pLq9",
            "https://soundcloud.com/lofi_girl/sets/lofi-hip-hop-beats?utm_source=clipboard",
            _playlist(Platform.SOUNDCLOUD, "lofi_girl/sets/lofi-hip-hop-beats"),
        ),
        (
            "https://spotify.link/AbCdEfGhIjK",
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x",
            _playlist(Platform.SPOTIFY, "37i9dQZF1DXcBWIGoYBM5M"),
        ),
    ],
)
async def test_resolver_expands_short_links(short: str, full: str, expected: PlaylistLink) -> None:
    expander = _StubExpander({short: full})

    assert await LinkResolver(expander).resolve(short) == expected
    assert expander.expanded == [short]


async def test_resolver_does_not_expand_full_links() -> None:
    expander = _StubExpander({})

    link = await LinkResolver(expander).resolve("https://music.yandex.ru/users/a/playlists/5")

    assert link == _playlist(Platform.YANDEX, "a:5")
    assert expander.expanded == []


async def test_resolver_rejects_short_link_to_foreign_host() -> None:
    expander = _StubExpander({"https://vk.cc/evil": "http://169.254.169.254/latest/meta-data"})

    with pytest.raises(UnsupportedLinkError) as caught:
        await LinkResolver(expander).resolve("https://vk.cc/evil")
    assert caught.value.reason == "unknown_host"
