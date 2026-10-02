from uuid import uuid4

from syncplaylists.shared_kernel.domain.value_objects import ISRC


def random_isrc() -> ISRC:
    digits = str(uuid4().int % 10_000_000).zfill(7)
    return ISRC(f"US{uuid4().hex[:3].upper()}{digits}")
