from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, SecretStr

from syncplaylists.modules.identity.application.dto import UserDto
from syncplaylists.modules.identity.application.use_cases import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
)


class CredentialsRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class LoginRequest(BaseModel):
    # При входе длину пароля не валидируем: 422 на «короткий пароль» выдавал бы
    # политику паролей и отличался бы от обычного 401.
    email: str = Field(max_length=320)
    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)


class UserResponse(BaseModel):
    id: UUID
    email: str
    created_at: datetime

    @classmethod
    def from_dto(cls, dto: UserDto) -> "UserResponse":
        return cls(id=dto.id, email=dto.email, created_at=dto.created_at)
