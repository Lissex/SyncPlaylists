import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "recognition"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeThrottle:
    """Темп распознавателя: wait — что вернуть из acquire (None — можно идти)."""

    def __init__(self, wait: float | None = None) -> None:
        self.wait = wait
        self.acquired: list[str] = []
        self.penalties: list[tuple[str, float]] = []

    async def acquire(self, provider: str) -> float | None:
        self.acquired.append(provider)
        return self.wait

    async def penalize(self, provider: str, seconds: float) -> None:
        self.penalties.append((provider, seconds))


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client
