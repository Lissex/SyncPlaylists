from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from syncplaylists.modules.transfers.application.dto import TransferDto, TransferItemDto
from syncplaylists.modules.transfers.application.links import LinkKind, ResolvedLinkDto
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    LibraryDestination,
    LibrarySource,
    NewPlaylist,
    PlaylistSource,
    TrackDestination,
    TrackSource,
)
from syncplaylists.shared_kernel.domain.links import MAX_LINK_LENGTH
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform, PlaylistRef

# FileSource/импорт из файла — отдельный вид источника на этапе backups, здесь не принимается.


class PlaylistSourceSchema(BaseModel):
    kind: Literal["playlist"] = "playlist"
    platform: Platform
    external_id: str

    def to_domain(self) -> PlaylistSource:
        return PlaylistSource(ref=PlaylistRef(self.platform, self.external_id))


class LibrarySourceSchema(BaseModel):
    kind: Literal["library"] = "library"
    platform: Platform
    account_id: UUID

    def to_domain(self) -> LibrarySource:
        return LibrarySource(platform=self.platform, account_id=self.account_id)


class LinkSchema(BaseModel):
    """Сырая ссылка от пользователя (или текст «Поделиться» со ссылкой внутри) —
    сервер сам разберёт, плейлист это или медиатека (ResolvePlaylistLinkUseCase)."""

    kind: Literal["link"] = "link"
    url: str = Field(min_length=1, max_length=MAX_LINK_LENGTH)


TrackSourceSchema = Annotated[
    PlaylistSourceSchema | LibrarySourceSchema | LinkSchema, Field(discriminator="kind")
]


class ExistingPlaylistSchema(BaseModel):
    kind: Literal["existing"] = "existing"
    platform: Platform
    external_id: str

    def to_domain(self) -> ExistingPlaylist:
        return ExistingPlaylist(ref=PlaylistRef(self.platform, self.external_id))


class NewPlaylistSchema(BaseModel):
    kind: Literal["new"] = "new"
    platform: Platform
    title: str
    description: str | None = None

    def to_domain(self) -> NewPlaylist:
        return NewPlaylist(platform=self.platform, title=self.title, description=self.description)


class LibraryDestinationSchema(BaseModel):
    kind: Literal["library"] = "library"
    platform: Platform
    account_id: UUID

    def to_domain(self) -> LibraryDestination:
        return LibraryDestination(platform=self.platform, account_id=self.account_id)


TrackDestinationSchema = Annotated[
    ExistingPlaylistSchema | NewPlaylistSchema | LibraryDestinationSchema | LinkSchema,
    Field(discriminator="kind"),
]


def source_to_schema(source: TrackSource) -> PlaylistSourceSchema | LibrarySourceSchema:
    if isinstance(source, PlaylistSource):
        return PlaylistSourceSchema(
            platform=source.ref.platform, external_id=source.ref.external_id
        )
    assert isinstance(source, LibrarySource)
    return LibrarySourceSchema(platform=source.platform, account_id=source.account_id)


def destination_to_schema(
    destination: TrackDestination,
) -> ExistingPlaylistSchema | NewPlaylistSchema | LibraryDestinationSchema:
    if isinstance(destination, ExistingPlaylist):
        return ExistingPlaylistSchema(
            platform=destination.ref.platform, external_id=destination.ref.external_id
        )
    if isinstance(destination, NewPlaylist):
        return NewPlaylistSchema(
            platform=destination.platform,
            title=destination.title,
            description=destination.description,
        )
    assert isinstance(destination, LibraryDestination)
    return LibraryDestinationSchema(
        platform=destination.platform, account_id=destination.account_id
    )


class StartTransferRequest(BaseModel):
    # user_id не принимаем от клиента — он берётся из сессии (cookie).
    source: TrackSourceSchema
    destination: TrackDestinationSchema


class TrackCandidateSchema(BaseModel):
    platform: Platform
    external_id: str
    title: str
    artist: str
    duration_ms: int | None = None
    isrc: str | None = None
    artists: list[str] = []
    cover_url: str | None = None


class MatchResultSchema(BaseModel):
    target_platform: Platform
    target_external_id: str
    method: str
    score: float


class TransferItemResponse(BaseModel):
    position: int
    status: str
    source_platform: Platform
    source_external_id: str
    match: MatchResultSchema | None
    candidates: list[TrackCandidateSchema]

    @classmethod
    def from_dto(cls, dto: TransferItemDto) -> "TransferItemResponse":
        match = (
            MatchResultSchema(
                target_platform=dto.match.target_ref.platform,
                target_external_id=dto.match.target_ref.external_id,
                method=dto.match.method,
                score=dto.match.score.value,
            )
            if dto.match is not None
            else None
        )
        return cls(
            position=dto.position,
            status=dto.status,
            source_platform=dto.source_track.platform,
            source_external_id=dto.source_track.external_id,
            match=match,
            candidates=[
                TrackCandidateSchema(
                    platform=c.ref.platform,
                    external_id=c.ref.external_id,
                    title=c.title,
                    artist=c.artist,
                    duration_ms=c.duration.milliseconds if c.duration else None,
                    isrc=c.isrc.value if c.isrc else None,
                    artists=list(c.artists),
                    cover_url=c.cover_url,
                )
                for c in dto.candidates
            ],
        )


class TransferResponse(BaseModel):
    id: UUID
    user_id: UUID
    status: str
    source: PlaylistSourceSchema | LibrarySourceSchema
    destination: ExistingPlaylistSchema | NewPlaylistSchema | LibraryDestinationSchema
    items: list[TransferItemResponse]

    @classmethod
    def from_dto(cls, dto: TransferDto) -> "TransferResponse":
        return cls(
            id=dto.id,
            user_id=dto.user_id,
            status=dto.status,
            source=source_to_schema(dto.source),
            destination=destination_to_schema(dto.destination),
            items=[TransferItemResponse.from_dto(item) for item in dto.items],
        )


class ResolveItemRequest(BaseModel):
    chosen_platform: Platform | None = None
    chosen_external_id: str | None = None

    def chosen_ref(self) -> ExternalTrackRef | None:
        if self.chosen_platform is None or self.chosen_external_id is None:
            return None
        return ExternalTrackRef(self.chosen_platform, self.chosen_external_id)


class ResolveLinkRequest(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_LINK_LENGTH)


class ResolvedLinkResponse(BaseModel):
    platform: Platform
    kind: LinkKind
    external_id: str | None
    account_id: UUID | None
    title: str | None
    track_count: int | None

    @classmethod
    def from_dto(cls, dto: ResolvedLinkDto) -> "ResolvedLinkResponse":
        return cls(
            platform=dto.platform,
            kind=dto.kind,
            external_id=dto.external_id,
            account_id=dto.account_id,
            title=dto.title,
            track_count=dto.track_count,
        )
