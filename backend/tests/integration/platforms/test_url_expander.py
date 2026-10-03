from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from syncplaylists.infrastructure.http.url_expander import HttpxUrlExpander
from syncplaylists.shared_kernel.domain.errors import UnsupportedLinkError


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    # assert_all_mocked: любой запрос мимо описанных маршрутов — ошибка теста. Так
    # проверяется главное: на хост площадки и чужие хосты расширитель не ходит.
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest.fixture
async def expander() -> AsyncIterator[HttpxUrlExpander]:
    async with httpx.AsyncClient() as client:
        yield HttpxUrlExpander(client, timeout_seconds=1)


async def test_expands_vk_cc_without_requesting_target(
    mock: respx.MockRouter, expander: HttpxUrlExpander
) -> None:
    target = "https://vk.com/music/playlist/-147845620_2949_ba3f9a6d26c5e5e8d1"
    mock.get("https://vk.cc/cAbCdE").respond(301, headers={"Location": target})

    assert await expander.expand("https://vk.cc/cAbCdE") == target


async def test_follows_chain_of_shorteners(
    mock: respx.MockRouter, expander: HttpxUrlExpander
) -> None:
    mock.get("https://on.soundcloud.com/Xy7Zk").respond(
        302, headers={"Location": "https://soundcloud.app.goo.gl/abc"}
    )
    mock.get("https://soundcloud.app.goo.gl/abc").respond(
        302, headers={"Location": "https://soundcloud.com/lofi_girl/sets/lofi?ref=clipboard"}
    )

    expanded = await expander.expand("https://on.soundcloud.com/Xy7Zk")

    assert expanded == "https://soundcloud.com/lofi_girl/sets/lofi?ref=clipboard"


async def test_relative_location_and_http_upgraded_to_https(
    mock: respx.MockRouter, expander: HttpxUrlExpander
) -> None:
    route = mock.get("https://spotify.link/a").respond(302, headers={"Location": "/b"})
    mock.get("https://spotify.link/b").respond(
        302, headers={"Location": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}
    )

    expanded = await expander.expand("http://spotify.link/a")

    assert expanded == "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
    assert route.called


async def test_redirect_to_foreign_host_is_returned_not_requested(
    mock: respx.MockRouter, expander: HttpxUrlExpander
) -> None:
    mock.get("https://vk.cc/evil").respond(
        302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
    )

    # Сам URL отдаётся парсерам (они его отвергнут: unknown_host); запроса туда нет —
    # иначе assert_all_mocked уронил бы тест.
    assert await expander.expand("https://vk.cc/evil") == "http://169.254.169.254/latest/meta-data/"


async def test_endless_redirect_loop_is_rejected(
    mock: respx.MockRouter, expander: HttpxUrlExpander
) -> None:
    mock.get("https://vk.cc/a").respond(302, headers={"Location": "https://vk.cc/b"})
    mock.get("https://vk.cc/b").respond(302, headers={"Location": "https://vk.cc/a"})

    with pytest.raises(UnsupportedLinkError) as caught:
        await expander.expand("https://vk.cc/a")
    assert caught.value.reason == "short_link_unresolved"


@pytest.mark.parametrize("status", [200, 404, 500])
async def test_short_link_without_redirect_is_rejected(
    mock: respx.MockRouter, expander: HttpxUrlExpander, status: int
) -> None:
    mock.get("https://vk.cc/dead").respond(status)

    with pytest.raises(UnsupportedLinkError):
        await expander.expand("https://vk.cc/dead")


async def test_network_error_is_rejected(
    mock: respx.MockRouter, expander: HttpxUrlExpander
) -> None:
    mock.get("https://vk.cc/slow").mock(side_effect=httpx.ConnectTimeout("timeout"))

    with pytest.raises(UnsupportedLinkError):
        await expander.expand("https://vk.cc/slow")


def test_only_known_shorteners_are_short_links(expander: HttpxUrlExpander) -> None:
    assert expander.is_short_link("vk.cc")
    assert expander.is_short_link("ON.SOUNDCLOUD.COM")
    assert not expander.is_short_link("vk.com")
    assert not expander.is_short_link("bit.ly")
