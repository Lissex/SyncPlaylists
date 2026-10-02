from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ValueObject:
    pass


@dataclass(frozen=True, slots=True)
class DomainEvent:
    occurred_at: datetime


# Наследники Entity/AggregateRoot обязаны сами быть помечены
# @dataclass(eq=False, slots=True) — иначе dataclass сгенерирует собственный
# __eq__ по всем полям и обнулит __hash__, и равенство по id сломается.
@dataclass(eq=False, slots=True)
class Entity:
    id: UUID

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Entity) and type(other) is type(self) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass(eq=False, slots=True)
class AggregateRoot(Entity):
    # kw_only=True — иначе любой наследник с собственными обязательными полями после
    # этого (дефолтного) поля не собрался бы как dataclass ("non-default after default").
    _domain_events: list[DomainEvent] = field(
        default_factory=list, repr=False, compare=False, kw_only=True
    )

    def record_event(self, event: DomainEvent) -> None:
        self._domain_events.append(event)

    def pull_domain_events(self) -> list[DomainEvent]:
        events = list(self._domain_events)
        self._domain_events.clear()
        return events
