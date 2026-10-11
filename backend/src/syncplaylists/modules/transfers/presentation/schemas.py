from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from syncplaylists.modules.transfers.application.dto import (
    TransferDto,
    TransferItemDto,
    TransferProgressDto,
)
from syncplaylists.modules.transfers.application.links import LinkKind, ResolvedLinkDto
from syncplaylists.modules.transfers.application.ports import TransferSummary
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
    uploader: str | None = None
    rights_holder: bool = False
    # "preview_only" — без подписки площадки слышно только превью (SoundCloud Go+).
    restriction: str | None = None


class MatchResultSchema(BaseModel):
    target_platform: Platform
    target_external_id: str
    method: str
    score: float
    # Пометка для отчёта: "preview_only" — найденный трек полностью доступен только с
    # подпиской площадки (SoundCloud Go+).
    restriction: str | None = None


class TransferItemResponse(BaseModel):
    position: int
    status: str
    source_platform: Platform
    source_external_id: str
    # Что искали — название трека источника (null, если его нет в каталоге).
    source_title: str | None = None
    source_artist: str | None = None
    source_duration_ms: int | None = None
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
                restriction=dto.match.restriction,
            )
            if dto.match is not None
            else None
        )
        return cls(
            position=dto.position,
            status=dto.status,
            source_platform=dto.source_track.platform,
            source_external_id=dto.source_track.external_id,
            source_title=dto.source_meta.title if dto.source_meta else None,
            source_artist=dto.source_meta.artist if dto.source_meta else None,
            source_duration_ms=dto.source_meta.duration_ms if dto.source_meta else None,
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
                    uploader=c.uploader,
                    rights_holder=c.rights_holder,
                    restriction=c.restriction.value if c.restriction else None,
                )
                for c in dto.candidates
            ],
        )


class TransferProgressSchema(BaseModel):
    status: str
    total: int
    pending: int
    matched: int
    uncertain: int
    not_found: int
    added: int
    failed: int
    # Пропущены пользователем на ревью (в т.ч. не решённые сомнительные при «Записать»).
    skipped: int = 0
    # Оценка оставшегося времени матчинга по скорости последних треков, секунды.
    # Только для status=running; null — данных пока мало или фаза не матчинг.
    eta_seconds: int | None
    # status=paused_quota: площадка исчерпала квоту, перенос продолжится сам в это время
    # (UTC) — фронт показывает «продолжим в HH:MM».
    resume_at: datetime | None = None
    # status=paused_client: перенос ждёт браузерное расширение — offline (браузер закрыт),
    # timeout, no_permission, logged_out, session_mismatch, captcha. Продолжится сам,
    # когда расширение будет готово.
    pause_reason: str | None = None

    @classmethod
    def from_dto(cls, dto: TransferProgressDto) -> "TransferProgressSchema":
        return cls(
            status=dto.status,
            total=dto.total,
            pending=dto.pending,
            matched=dto.matched,
            uncertain=dto.uncertain,
            not_found=dto.not_found,
            added=dto.added,
            failed=dto.failed,
            skipped=dto.skipped,
            eta_seconds=dto.eta_seconds,
            resume_at=dto.resume_at,
            pause_reason=dto.pause_reason,
        )


class TransferResponse(BaseModel):
    id: UUID
    user_id: UUID
    status: str
    source: PlaylistSourceSchema | LibrarySourceSchema
    destination: ExistingPlaylistSchema | NewPlaylistSchema | LibraryDestinationSchema
    items: list[TransferItemResponse]
    progress: TransferProgressSchema | None = None
    # Созданные под NewPlaylist плейлисты: обычно один, несколько — если треков больше,
    # чем вмещает плейлист площадки («<название> (1/N)», ...).
    resolved_targets: list[str] = []
    source_title: str | None = None
    created_at: datetime | None = None
    # Сколько треков найдено каждым методом: isrc, fuzzy, audio, manual, ...
    match_methods: dict[str, int] = {}

    @classmethod
    def from_dto(cls, dto: TransferDto) -> "TransferResponse":
        return cls(
            id=dto.id,
            user_id=dto.user_id,
            status=dto.status,
            source=source_to_schema(dto.source),
            destination=destination_to_schema(dto.destination),
            items=[TransferItemResponse.from_dto(item) for item in dto.items],
            progress=TransferProgressSchema.from_dto(dto.progress) if dto.progress else None,
            resolved_targets=[ref.external_id for ref in dto.resolved_targets],
            source_title=dto.source_title,
            created_at=dto.created_at,
            match_methods=dict(dto.match_methods),
        )


class TransferItemsPage(BaseModel):
    items: list[TransferItemResponse]
    # Передать как `after` за следующей страницей; null — страниц больше нет.
    next_after: int | None


class TransferSummaryResponse(BaseModel):
    id: UUID
    status: str
    source: PlaylistSourceSchema | LibrarySourceSchema
    destination: ExistingPlaylistSchema | NewPlaylistSchema | LibraryDestinationSchema
    source_title: str | None
    total: int
    matched: int
    uncertain: int
    not_found: int
    added: int
    failed: int
    skipped: int
    resolved_targets: list[str]
    resume_at: datetime | None
    pause_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_summary(cls, summary: TransferSummary) -> "TransferSummaryResponse":
        progress = summary.progress
        return cls(
            id=summary.id,
            status=summary.status.value,
            source=source_to_schema(summary.source),
            destination=destination_to_schema(summary.destination),
            source_title=summary.source_title,
            total=progress.total,
            matched=progress.matched,
            uncertain=progress.uncertain,
            not_found=progress.not_found,
            added=progress.added,
            failed=progress.failed,
            skipped=progress.skipped,
            resolved_targets=[ref.external_id for ref in summary.resolved_targets],
            resume_at=summary.resume_at,
            pause_reason=summary.pause_reason,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
        )


class TransferHistoryPage(BaseModel):
    items: list[TransferSummaryResponse]
    # Передать как `cursor` за следующей страницей; null — это последняя.
    next_cursor: str | None


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
