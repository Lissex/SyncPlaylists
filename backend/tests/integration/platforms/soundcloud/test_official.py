"""OFFICIAL-транспорт SoundCloud (OAuth 2.1 + PKCE, api.soundcloud.com). Включается только
с приложением в Settings; live не проверен (нет Artist Pro) — только respx по
документированным формам."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.factory import (
    OfficialApp,
    SoundCloudApiFactory,
    SoundCloudGatewayBuilder,
    SoundCloudLimits,
    SoundCloudOAuthProvider,
)
from syncplaylists.integrations.platforms.soundcloud.tokens import TOKEN_URL, TokenEndpoint
from syncplaylists.integrations.platforms.soundcloud.transport import OFFICIAL_BASE_URL
from syncplaylists.shared_kernel.domain.errors import PlatformNotSupportedError
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    Platform,
    PlaylistRef,
    Transport,
)
from tests.integration.platforms.soundcloud.conftest import (
    MemoryRefresher,
    fixture,
    make_access,
)

_APP = OfficialApp(client_id="our-app", client_secret="our-secret")


@pytest.fixture
def official_apis(
    http: httpx.AsyncClient, client_ids: ClientIdProvider, refresher: MemoryRefresher
) -> SoundCloudApiFactory:
    return SoundCloudApiFactory(
        http,
        client_ids,
        TokenEndpoint(http, timeout_seconds=5),
        timeout_seconds=5,
        refresher=refresher,
        official=_APP,
    )


def test_authorization_url_uses_pkce(
    official_apis: SoundCloudApiFactory, http: httpx.AsyncClient
) -> None:
    provider = SoundCloudOAuthProvider(_APP, TokenEndpoint(http, timeout_seconds=5), official_apis)
    url = provider.authorization_url(
        state="st", code_challenge="ch", redirect_uri="http://localhost:8000/cb"
    )
    parts = urlsplit(url)
    assert (
        f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://secure.soundcloud.com/authorize"
    )
    assert {k: v[0] for k, v in parse_qs(parts.query).items()} == {
        "client_id": "our-app",
        "redirect_uri": "http://localhost:8000/cb",
        "response_type": "code",
        "code_challenge": "ch",
        "code_challenge_method": "S256",
        "state": "st",
    }


async def test_exchange_code_returns_grant_with_profile(
    router: respx.MockRouter, official_apis: SoundCloudApiFactory, http: httpx.AsyncClient
) -> None:
    token = router.post(TOKEN_URL).respond(
        200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    )
    me = router.get(f"{OFFICIAL_BASE_URL}/me").respond(200, json=fixture("me"))
    provider = SoundCloudOAuthProvider(_APP, TokenEndpoint(http, timeout_seconds=5), official_apis)

    grant = await provider.exchange_code(
        code="c", code_verifier="v", redirect_uri="http://localhost:8000/cb"
    )

    assert (grant.external_user_id, grant.display_name) == ("900001", "Test Listener")
    assert (grant.access_token, grant.refresh_token) == ("at", "rt")
    assert grant.expires_at is not None
    assert grant.expires_at - datetime.now(UTC) > timedelta(minutes=59)
    form = dict(httpx.QueryParams(token.calls[0].request.content.decode()))
    assert form["grant_type"] == "authorization_code"
    assert (form["code_verifier"], form["client_secret"]) == ("v", "our-secret")
    # Официальному API client_id в запросе не нужен — только токен.
    assert "client_id" not in me.calls[0].request.url.params
    assert me.calls[0].request.headers["authorization"] == "OAuth at"


async def test_official_gateway_endpoints(
    router: respx.MockRouter, official_apis: SoundCloudApiFactory
) -> None:
    gateway = SoundCloudGatewayBuilder(official_apis, SoundCloudLimits())(
        make_access("at", transport=Transport.OFFICIAL)
    )
    likes = router.get(f"{OFFICIAL_BASE_URL}/me/likes/tracks").respond(
        200, json={"collection": [fixture("search_tracks")["collection"][1]], "next_href": None}
    )
    like = router.post(f"{OFFICIAL_BASE_URL}/likes/tracks/2001").respond(201)
    router.get(f"{OFFICIAL_BASE_URL}/playlists/5001").respond(200, json=fixture("playlist"))
    put = router.put(f"{OFFICIAL_BASE_URL}/playlists/5001").respond(200, json={})

    result = await gateway.add_to_library([ExternalTrackRef(Platform.SOUNDCLOUD, "2001")])
    await gateway.add_tracks(
        PlaylistRef(Platform.SOUNDCLOUD, "5001"), [ExternalTrackRef(Platform.SOUNDCLOUD, "2001")]
    )

    assert likes.called
    assert like.called
    assert len(result.added) == 1
    tracks = json.loads(put.calls[0].request.content)["playlist"]["tracks"]
    assert tracks[-1] == {"id": 2001}


async def test_official_refresh_uses_our_app(
    router: respx.MockRouter, official_apis: SoundCloudApiFactory
) -> None:
    token = router.post(TOKEN_URL).respond(200, json={"access_token": "at2", "expires_in": 3600})
    router.get(f"{OFFICIAL_BASE_URL}/me").respond(200, json=fixture("me"))
    access = make_access(
        "at",
        refresh_token="rt",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        transport=Transport.OFFICIAL,
    )

    await official_apis.for_account(access).me()

    form = dict(httpx.QueryParams(token.calls[0].request.content.decode()))
    assert (form["client_id"], form["client_secret"]) == ("our-app", "our-secret")


def test_official_account_without_app_is_not_supported(apis: SoundCloudApiFactory) -> None:
    with pytest.raises(PlatformNotSupportedError):
        apis.for_account(make_access(transport=Transport.OFFICIAL))
