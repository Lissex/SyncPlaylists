from dataclasses import dataclass

from fastapi import Request, Response

_BASE_NAME = "sp_session"


@dataclass(frozen=True, slots=True)
class SessionCookiePolicy:
    """Параметры cookie сессии. Собирается в bootstrap из Settings.security —
    presentation сам Settings не читает."""

    secure: bool
    max_age_seconds: int

    @property
    def name(self) -> str:
        # Префикс __Host- браузер принимает только с Secure, Path=/ и без Domain —
        # cookie нельзя перезаписать с поддомена. По http (dev) такой cookie не
        # установится вовсе, поэтому без Secure — обычное имя.
        return f"__Host-{_BASE_NAME}" if self.secure else _BASE_NAME

    def read(self, request: Request) -> str | None:
        return request.cookies.get(self.name)

    def set(self, response: Response, token: str) -> None:
        response.set_cookie(
            key=self.name,
            value=token,
            max_age=self.max_age_seconds,
            path="/",
            secure=self.secure,
            httponly=True,
            samesite="lax",
        )

    def clear(self, response: Response) -> None:
        response.delete_cookie(
            key=self.name, path="/", secure=self.secure, httponly=True, samesite="lax"
        )
