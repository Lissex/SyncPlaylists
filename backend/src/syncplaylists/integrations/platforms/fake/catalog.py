from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DemoTrack:
    slug: str
    title: str
    artist: str
    duration_ms: int
    isrc: str


# Один и тот же демо-каталог для всех площадок (разные external_id, общий ISRC) —
# перенос между двумя FakeMusicPlatformGateway находит совпадения через IsrcStrategy.
DEMO_TRACKS: tuple[DemoTrack, ...] = (
    DemoTrack("starboy", "Starboy", "The Weeknd", 230_000, "USUM71703861"),
    DemoTrack("blinding-lights", "Blinding Lights", "The Weeknd", 200_000, "USUM71901007"),
    DemoTrack("one-dance", "One Dance", "Drake", 173_000, "USUM71502950"),
)
