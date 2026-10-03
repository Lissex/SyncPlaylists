from collections.abc import AsyncIterator

from arq.connections import ArqRedis, create_pool
from arq.connections import RedisSettings as ArqRedisSettings
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.infrastructure.db.engine import create_engine
from syncplaylists.infrastructure.db.session import create_session_factory
from syncplaylists.infrastructure.db.uow import SqlUnitOfWork
from syncplaylists.infrastructure.events.redis_publisher import RedisEventPublisher
from syncplaylists.infrastructure.queue.arq_queue import ArqTaskQueue
from syncplaylists.infrastructure.ratelimit.redis_fixed_window import RedisFixedWindowLimiter
from syncplaylists.infrastructure.security.aes_gcm import AesGcmTokenCipher
from syncplaylists.integrations.platforms.fake.factory import FakeGatewayFactory
from syncplaylists.integrations.platforms.fake.oauth import FakeOAuthProvider
from syncplaylists.modules.accounts.application.access import AccountAccessService
from syncplaylists.modules.accounts.application.ports import (
    AccountCredentialsWriter,
    ConnectedAccountRepository,
    OAuthProviderRegistry,
    OAuthStateStore,
    TokenCipher,
)
from syncplaylists.modules.accounts.application.use_cases import (
    CompleteOAuthUseCase,
    ConnectAccountUseCase,
    DisconnectAccountUseCase,
    ListAccountsUseCase,
    OAuthFlowConfig,
    StartOAuthUseCase,
)
from syncplaylists.modules.accounts.infrastructure.oauth import (
    DictOAuthProviderRegistry,
    RedisOAuthStateStore,
)
from syncplaylists.modules.accounts.infrastructure.repository import (
    SqlAccountCredentialsWriter,
    SqlConnectedAccountRepository,
)
from syncplaylists.modules.accounts.presentation.api import FrontendRedirect
from syncplaylists.modules.catalog.application.ports import (
    CanonicalTrackRepository,
    PlatformTrackRepository,
)
from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.catalog.infrastructure.repository import (
    SqlCanonicalTrackRepository,
    SqlPlatformTrackRepository,
)
from syncplaylists.modules.identity.application.ports import (
    AttemptLimiter,
    PasswordHasher,
    SessionTokenService,
    UserRepository,
)
from syncplaylists.modules.identity.application.use_cases import (
    GetCurrentUserUseCase,
    LoginUseCase,
    LogoutAllUseCase,
    LogoutUseCase,
    RegisterUserUseCase,
    ResolveSessionUseCase,
)
from syncplaylists.modules.identity.infrastructure.passwords import Argon2PasswordHasher
from syncplaylists.modules.identity.infrastructure.repository import SqlUserRepository
from syncplaylists.modules.identity.infrastructure.sessions import RedisSessionStore
from syncplaylists.modules.identity.presentation.cookies import SessionCookiePolicy
from syncplaylists.modules.matching.application.pipeline_factory import (
    DefaultMatchingPipelineFactory,
)
from syncplaylists.modules.matching.application.ports import MatchingPipelineFactory
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.ports import TrackMatchRepository
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.infrastructure.repository import SqlTrackMatchRepository
from syncplaylists.modules.transfers.application.ports import TransferRepository
from syncplaylists.modules.transfers.application.use_cases import (
    FailTransferItemUseCase,
    GetTransferUseCase,
    MatchTransferItemUseCase,
    ProcessTransferUseCase,
    ResolveUncertainItemUseCase,
    StartTransferUseCase,
    SweepStaleTransfersUseCase,
    WriteTransferUseCase,
)
from syncplaylists.modules.transfers.infrastructure.repository import SqlTransferRepository
from syncplaylists.shared_kernel.application.ports import (
    AccountAccessProvider,
    EventPublisher,
    GatewayFactory,
    TaskQueue,
    UnitOfWork,
)


class SettingsProvider(Provider):
    scope = Scope.APP

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    @provide
    def get_settings(self) -> Settings:
        return self._settings


class DatabaseProvider(Provider):
    scope = Scope.APP

    @provide
    async def get_engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = create_engine(settings.db)
        yield engine
        await engine.dispose()

    @provide
    def get_session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return create_session_factory(engine)

    @provide(scope=Scope.REQUEST)
    async def get_session(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @provide(scope=Scope.REQUEST)
    def get_unit_of_work(
        self, session: AsyncSession, event_publisher: EventPublisher
    ) -> UnitOfWork:
        return SqlUnitOfWork(session, event_publisher)


class RedisProvider(Provider):
    scope = Scope.APP

    @provide
    async def get_arq_redis(self, settings: Settings) -> AsyncIterator[ArqRedis]:
        pool = await create_pool(ArqRedisSettings.from_dsn(str(settings.redis.dsn)))
        yield pool
        await pool.aclose()

    @provide
    def get_redis(self, arq_redis: ArqRedis) -> Redis:
        return arq_redis  # ArqRedis — подкласс redis.asyncio.Redis, один пул на всё.

    @provide
    def get_task_queue(self, arq_redis: ArqRedis) -> TaskQueue:
        return ArqTaskQueue(arq_redis)

    @provide
    def get_event_publisher(self, redis: Redis) -> EventPublisher:
        return RedisEventPublisher(redis)


class GatewayProvider(Provider):
    scope = Scope.APP

    @provide
    def get_gateway_factory(self) -> GatewayFactory:
        # Настоящих адаптеров площадок ещё нет (этап 4) — единственная реализация
        # GatewayFactory на этом этапе отдаёт in-memory фейковую площадку.
        return FakeGatewayFactory()


class CatalogProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def get_platform_track_repository(self, session: AsyncSession) -> PlatformTrackRepository:
        return SqlPlatformTrackRepository(session)

    @provide
    def get_canonical_track_repository(self, session: AsyncSession) -> CanonicalTrackRepository:
        return SqlCanonicalTrackRepository(session)

    @provide
    def get_ensure_platform_track(
        self,
        platform_tracks: PlatformTrackRepository,
        canonical_tracks: CanonicalTrackRepository,
    ) -> EnsurePlatformTrackUseCase:
        return EnsurePlatformTrackUseCase(platform_tracks, canonical_tracks)


class MatchingProvider(Provider):
    @provide(scope=Scope.APP)
    def get_normalizer(self) -> TrackNormalizer:
        return TrackNormalizer()

    @provide(scope=Scope.APP)
    def get_scorer(self, normalizer: TrackNormalizer) -> MatchScorer:
        return MatchScorer(normalizer)

    @provide(scope=Scope.REQUEST)
    def get_track_match_repository(
        self, session: AsyncSession, platform_tracks: PlatformTrackRepository
    ) -> TrackMatchRepository:
        return SqlTrackMatchRepository(session, platform_tracks)

    @provide(scope=Scope.REQUEST)
    def get_pipeline_factory(
        self,
        gateway_factory: GatewayFactory,
        track_matches: TrackMatchRepository,
        normalizer: TrackNormalizer,
        scorer: MatchScorer,
    ) -> MatchingPipelineFactory:
        return DefaultMatchingPipelineFactory(gateway_factory, track_matches, normalizer, scorer)


class SecurityProvider(Provider):
    scope = Scope.APP

    @provide
    def get_token_cipher(self, settings: Settings) -> TokenCipher:
        return AesGcmTokenCipher(settings.security.token_encryption_key_bytes())

    @provide
    def get_password_hasher(self) -> PasswordHasher:
        return Argon2PasswordHasher()

    @provide
    def get_session_tokens(self, redis: Redis, settings: Settings) -> SessionTokenService:
        return RedisSessionStore(redis, ttl_seconds=settings.security.session_ttl_minutes * 60)

    @provide
    def get_attempt_limiter(self, redis: Redis, settings: Settings) -> AttemptLimiter:
        return RedisFixedWindowLimiter(
            redis,
            attempts=settings.security.auth_rate_limit_attempts,
            window_seconds=settings.security.auth_rate_limit_window_seconds,
        )

    @provide
    def get_cookie_policy(self, settings: Settings) -> SessionCookiePolicy:
        return SessionCookiePolicy(
            secure=settings.security.cookie_secure,
            max_age_seconds=settings.security.session_ttl_minutes * 60,
        )


class IdentityProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def get_user_repository(self, session: AsyncSession) -> UserRepository:
        return SqlUserRepository(session)

    @provide
    def get_register_user(
        self,
        uow: UnitOfWork,
        users: UserRepository,
        hasher: PasswordHasher,
        sessions: SessionTokenService,
        limiter: AttemptLimiter,
    ) -> RegisterUserUseCase:
        return RegisterUserUseCase(uow, users, hasher, sessions, limiter)

    @provide
    def get_login(
        self,
        uow: UnitOfWork,
        users: UserRepository,
        hasher: PasswordHasher,
        sessions: SessionTokenService,
        limiter: AttemptLimiter,
    ) -> LoginUseCase:
        return LoginUseCase(uow, users, hasher, sessions, limiter)

    @provide
    def get_resolve_session(
        self, users: UserRepository, sessions: SessionTokenService
    ) -> ResolveSessionUseCase:
        return ResolveSessionUseCase(users, sessions)

    @provide
    def get_logout(self, sessions: SessionTokenService) -> LogoutUseCase:
        return LogoutUseCase(sessions)

    @provide
    def get_logout_all(self, sessions: SessionTokenService) -> LogoutAllUseCase:
        return LogoutAllUseCase(sessions)

    @provide
    def get_current_user(self, users: UserRepository) -> GetCurrentUserUseCase:
        return GetCurrentUserUseCase(users)


class AccountsProvider(Provider):
    @provide(scope=Scope.APP)
    def get_oauth_config(self, settings: Settings) -> OAuthFlowConfig:
        return OAuthFlowConfig(
            callback_base_url=settings.oauth.callback_base_url,
            state_ttl_seconds=settings.oauth.state_ttl_seconds,
        )

    @provide(scope=Scope.APP)
    def get_frontend_redirect(self, settings: Settings) -> FrontendRedirect:
        return FrontendRedirect(settings.oauth.frontend_redirect_url)

    @provide(scope=Scope.APP)
    def get_oauth_registry(self, settings: Settings) -> OAuthProviderRegistry:
        # Настоящих OAuth-клиентов площадок ещё нет (появятся с адаптерами) — только
        # фейки для площадок из settings.oauth.fake_platforms (dev/тесты).
        return DictOAuthProviderRegistry(
            FakeOAuthProvider(platform) for platform in settings.oauth.fake_platforms
        )

    @provide(scope=Scope.APP)
    def get_oauth_state_store(self, redis: Redis) -> OAuthStateStore:
        return RedisOAuthStateStore(redis)

    @provide(scope=Scope.APP)
    def get_credentials_writer(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> AccountCredentialsWriter:
        return SqlAccountCredentialsWriter(session_factory)

    @provide(scope=Scope.REQUEST)
    def get_account_repository(self, session: AsyncSession) -> ConnectedAccountRepository:
        return SqlConnectedAccountRepository(session)

    @provide(scope=Scope.REQUEST)
    def get_account_access(
        self,
        accounts: ConnectedAccountRepository,
        cipher: TokenCipher,
        writer: AccountCredentialsWriter,
    ) -> AccountAccessProvider:
        return AccountAccessService(accounts, cipher, writer)

    @provide(scope=Scope.REQUEST)
    def get_connect_account(
        self, uow: UnitOfWork, accounts: ConnectedAccountRepository, cipher: TokenCipher
    ) -> ConnectAccountUseCase:
        return ConnectAccountUseCase(uow, accounts, cipher)

    @provide(scope=Scope.REQUEST)
    def get_disconnect_account(
        self, uow: UnitOfWork, accounts: ConnectedAccountRepository
    ) -> DisconnectAccountUseCase:
        return DisconnectAccountUseCase(uow, accounts)

    @provide(scope=Scope.REQUEST)
    def get_list_accounts(self, accounts: ConnectedAccountRepository) -> ListAccountsUseCase:
        return ListAccountsUseCase(accounts)

    @provide(scope=Scope.REQUEST)
    def get_start_oauth(
        self, providers: OAuthProviderRegistry, states: OAuthStateStore, config: OAuthFlowConfig
    ) -> StartOAuthUseCase:
        return StartOAuthUseCase(providers, states, config)

    @provide(scope=Scope.REQUEST)
    def get_complete_oauth(
        self,
        providers: OAuthProviderRegistry,
        states: OAuthStateStore,
        config: OAuthFlowConfig,
        connect_account: ConnectAccountUseCase,
    ) -> CompleteOAuthUseCase:
        return CompleteOAuthUseCase(providers, states, config, connect_account)


class TransfersProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def get_transfer_repository(
        self, session: AsyncSession, platform_tracks: PlatformTrackRepository
    ) -> TransferRepository:
        return SqlTransferRepository(session, platform_tracks)

    @provide
    def get_start_transfer(
        self,
        uow: UnitOfWork,
        transfers: TransferRepository,
        task_queue: TaskQueue,
        accounts: AccountAccessProvider,
    ) -> StartTransferUseCase:
        return StartTransferUseCase(uow, transfers, task_queue, accounts)

    @provide
    def get_process_transfer(
        self,
        uow: UnitOfWork,
        transfers: TransferRepository,
        platform_tracks: PlatformTrackRepository,
        ensure_platform_track: EnsurePlatformTrackUseCase,
        gateway_factory: GatewayFactory,
        task_queue: TaskQueue,
        accounts: AccountAccessProvider,
    ) -> ProcessTransferUseCase:
        return ProcessTransferUseCase(
            uow,
            transfers,
            platform_tracks,
            ensure_platform_track,
            gateway_factory,
            task_queue,
            accounts,
        )

    @provide
    def get_match_transfer_item(
        self,
        uow: UnitOfWork,
        transfers: TransferRepository,
        platform_tracks: PlatformTrackRepository,
        track_matches: TrackMatchRepository,
        ensure_platform_track: EnsurePlatformTrackUseCase,
        pipeline_factory: MatchingPipelineFactory,
        task_queue: TaskQueue,
        accounts: AccountAccessProvider,
    ) -> MatchTransferItemUseCase:
        return MatchTransferItemUseCase(
            uow,
            transfers,
            platform_tracks,
            track_matches,
            ensure_platform_track,
            pipeline_factory,
            task_queue,
            accounts,
        )

    @provide
    def get_fail_transfer_item(
        self, uow: UnitOfWork, transfers: TransferRepository, task_queue: TaskQueue
    ) -> FailTransferItemUseCase:
        return FailTransferItemUseCase(uow, transfers, task_queue)

    @provide
    def get_resolve_uncertain_item(
        self, uow: UnitOfWork, transfers: TransferRepository, task_queue: TaskQueue
    ) -> ResolveUncertainItemUseCase:
        return ResolveUncertainItemUseCase(uow, transfers, task_queue)

    @provide
    def get_write_transfer(
        self,
        uow: UnitOfWork,
        transfers: TransferRepository,
        gateway_factory: GatewayFactory,
        accounts: AccountAccessProvider,
    ) -> WriteTransferUseCase:
        return WriteTransferUseCase(uow, transfers, gateway_factory, accounts)

    @provide
    def get_get_transfer(self, transfers: TransferRepository) -> GetTransferUseCase:
        return GetTransferUseCase(transfers)

    @provide
    def get_sweep_stale_transfers(
        self, transfers: TransferRepository, task_queue: TaskQueue
    ) -> SweepStaleTransfersUseCase:
        return SweepStaleTransfersUseCase(transfers, task_queue)


def make_container(settings: Settings) -> AsyncContainer:
    return make_async_container(
        SettingsProvider(settings),
        DatabaseProvider(),
        RedisProvider(),
        GatewayProvider(),
        SecurityProvider(),
        IdentityProvider(),
        AccountsProvider(),
        CatalogProvider(),
        MatchingProvider(),
        TransfersProvider(),
    )
