from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    LibraryDestination,
    LibrarySource,
    PlaylistSource,
)
from syncplaylists.shared_kernel.application.ports import (
    AccountAccessProvider,
    AccountNotAvailableError,
    GatewayFactory,
)
from syncplaylists.shared_kernel.domain.errors import PlatformNotSupportedError
from syncplaylists.shared_kernel.domain.links import LibraryLink, LinkResolver, PlaylistLink
from syncplaylists.shared_kernel.domain.value_objects import Platform, PlaylistRef


class LinkKind(StrEnum):
    PLAYLIST = "playlist"
    LIBRARY = "library"


@dataclass(frozen=True, slots=True)
class ResolvedLinkDto:
    platform: Platform
    kind: LinkKind
    external_id: str | None = None  # для плейлиста
    account_id: UUID | None = None  # для медиатеки — чья она
    title: str | None = None  # предпросмотр, если есть подключённый аккаунт
    track_count: int | None = None

    def as_source(self) -> PlaylistSource | LibrarySource:
        if self.kind is LinkKind.LIBRARY:
            assert self.account_id is not None
            return LibrarySource(platform=self.platform, account_id=self.account_id)
        assert self.external_id is not None
        return PlaylistSource(ref=PlaylistRef(self.platform, self.external_id))

    def as_destination(self) -> ExistingPlaylist | LibraryDestination:
        if self.kind is LinkKind.LIBRARY:
            assert self.account_id is not None
            return LibraryDestination(platform=self.platform, account_id=self.account_id)
        assert self.external_id is not None
        return ExistingPlaylist(ref=PlaylistRef(self.platform, self.external_id))


class ResolvePlaylistLinkUseCase:
    """Ссылка от пользователя → источник/назначение переноса.

    Доменный LinkResolver разбирает ссылки всех площадок; здесь — то, что требует
    знать пользователя и адаптеры: поддерживается ли площадка, не ведёт ли ссылка на
    медиатеку самого пользователя (Яндекс: users/<свой login>/playlists/3), чей
    аккаунт брать для медиатеки. Бросает UnsupportedLinkError,
    PlatformNotSupportedError, AccountNotAvailableError (ссылка на медиатеку без
    подключённого аккаунта), PlaylistNotFoundError и прочие PlatformError."""

    def __init__(
        self,
        resolver: LinkResolver,
        gateway_factory: GatewayFactory,
        accounts: AccountAccessProvider,
    ) -> None:
        self._resolver = resolver
        self._gateway_factory = gateway_factory
        self._accounts = accounts

    async def execute(self, user_id: UUID, url: str) -> ResolvedLinkDto:
        link = await self._resolver.resolve(url)
        platform = link.platform if isinstance(link, LibraryLink) else link.ref.platform
        if not self._gateway_factory.supports(platform):
            raise PlatformNotSupportedError(platform)

        if isinstance(link, LibraryLink):
            access = await self._accounts.for_platform(user_id, platform)
            return ResolvedLinkDto(platform, LinkKind.LIBRARY, account_id=access.account_id)

        assert isinstance(link, PlaylistLink)
        try:
            access = await self._accounts.for_platform(user_id, platform)
        except AccountNotAvailableError:
            # Без аккаунта ни свою медиатеку не опознать, ни шапку не прочитать —
            # перенос всё равно потребует аккаунт и скажет об этом сам.
            return ResolvedLinkDto(platform, LinkKind.PLAYLIST, external_id=link.ref.external_id)

        gateway = self._gateway_factory.for_account(access)
        if await gateway.is_own_library(link.ref):
            return ResolvedLinkDto(platform, LinkKind.LIBRARY, account_id=access.account_id)
        info = await gateway.playlist_info(link.ref)
        return ResolvedLinkDto(
            platform,
            LinkKind.PLAYLIST,
            external_id=link.ref.external_id,
            title=info.title,
            track_count=info.track_count,
        )
