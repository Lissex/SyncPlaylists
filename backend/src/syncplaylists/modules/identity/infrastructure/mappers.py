from syncplaylists.modules.identity.domain.entities import User
from syncplaylists.modules.identity.domain.value_objects import Email
from syncplaylists.modules.identity.infrastructure.orm import UserOrm


def user_to_domain(orm: UserOrm) -> User:
    return User(
        id=orm.id,
        email=Email(orm.email),
        password_hash=orm.password_hash,
        created_at=orm.created_at,
    )


def user_to_orm(user: User) -> UserOrm:
    return UserOrm(
        id=user.id,
        email=user.email.value,
        password_hash=user.password_hash,
        created_at=user.created_at,
    )
