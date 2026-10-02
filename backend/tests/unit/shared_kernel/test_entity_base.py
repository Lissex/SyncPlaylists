from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from syncplaylists.shared_kernel.domain.base import AggregateRoot, DomainEvent, Entity


@dataclass(eq=False, slots=True)
class _DummyEntity(Entity):
    name: str = ""


@dataclass(frozen=True, slots=True)
class _DummyEvent(DomainEvent):
    pass


@dataclass(eq=False, slots=True)
class _DummyAggregate(AggregateRoot):
    name: str = ""


def test_entity_equality_is_by_id_not_fields() -> None:
    shared_id = uuid4()
    a = _DummyEntity(id=shared_id, name="a")
    b = _DummyEntity(id=shared_id, name="b")
    assert a == b
    assert hash(a) == hash(b)


def test_entity_inequality_for_different_ids() -> None:
    a = _DummyEntity(id=uuid4(), name="same")
    b = _DummyEntity(id=uuid4(), name="same")
    assert a != b


def test_aggregate_root_records_and_pulls_domain_events() -> None:
    aggregate = _DummyAggregate(id=uuid4())
    event_a = _DummyEvent(occurred_at=datetime.now(UTC))
    event_b = _DummyEvent(occurred_at=datetime.now(UTC))

    aggregate.record_event(event_a)
    aggregate.record_event(event_b)

    pulled = aggregate.pull_domain_events()
    assert pulled == [event_a, event_b]
    assert aggregate.pull_domain_events() == []
