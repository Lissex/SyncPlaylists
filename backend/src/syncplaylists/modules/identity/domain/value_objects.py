import re
from dataclasses import dataclass
from typing import Final

from syncplaylists.modules.identity.domain.errors import InvalidEmailError
from syncplaylists.shared_kernel.domain.base import ValueObject

# Намеренно простая проверка формата: строгая валидация (DNS, IDN) — забота
# presentation (EmailStr), домену нужен только инвариант «похоже на адрес».
_EMAIL_PATTERN: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_LENGTH: Final = 320


@dataclass(frozen=True, slots=True)
class Email(ValueObject):
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if len(normalized) > _MAX_LENGTH or not _EMAIL_PATTERN.match(normalized):
            raise InvalidEmailError(f"Некорректный email: {self.value!r}")
        # frozen dataclass — нормализуем через object.__setattr__, чтобы
        # Email(" A@B.com ") == Email("a@b.com").
        object.__setattr__(self, "value", normalized)
