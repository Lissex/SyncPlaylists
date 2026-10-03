from sqlalchemy import MetaData

from syncplaylists.infrastructure.db.base import Base


def load_orm_models() -> MetaData:
    """Импортирует ORM-модули всех контекстов (побочный эффект — регистрация таблиц в
    Base.metadata) и возвращает metadata. Единый список для Alembic (env.py,
    autogenerate/check) и теста на дрейф схемы: новая модель, забытая здесь, не будет
    видна ни autogenerate, ни тесту — поэтому список один."""
    from syncplaylists.modules.accounts.infrastructure import orm as accounts_orm
    from syncplaylists.modules.catalog.infrastructure import orm as catalog_orm
    from syncplaylists.modules.identity.infrastructure import orm as identity_orm
    from syncplaylists.modules.matching.infrastructure import orm as matching_orm
    from syncplaylists.modules.transfers.infrastructure import orm as transfers_orm

    del accounts_orm, catalog_orm, identity_orm, matching_orm, transfers_orm
    return Base.metadata
