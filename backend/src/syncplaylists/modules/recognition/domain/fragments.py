from dataclasses import dataclass, field

from syncplaylists.shared_kernel.domain.base import ValueObject


@dataclass(frozen=True, slots=True)
class FragmentWindow(ValueObject):
    """Какой кусок трека слушаем: смещение от начала и длина, мс."""

    start_ms: int
    length_ms: int

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.length_ms <= 0:
            raise ValueError(f"Некорректное окно фрагмента: {self}")

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.length_ms

    @classmethod
    def middle(cls, duration_ms: int | None, length_ms: int) -> "FragmentWindow":
        """Окно из середины: вступление и концовка чаще тихие или общие у разных версий.
        Трек короче окна (или длительность неизвестна) — с начала, сколько есть."""
        if duration_ms is None or duration_ms <= length_ms:
            return cls(0, length_ms)
        return cls(duration_ms // 2 - length_ms // 2, length_ms)


@dataclass(frozen=True, slots=True)
class SourceAudio(ValueObject):
    """Скачанные байты, покрывающие окно (кусок mp3/aac/HLS-сегменты как есть).
    lead_ms — где внутри data начинается окно: источник качает с запасом (Range по
    оценке битрейта, целые HLS-сегменты), точную вырезку делает AudioTrimmer.
    Живут только в памяти воркера распознавания до конца распознавания."""

    data: bytes = field(repr=False)
    window: FragmentWindow
    lead_ms: int = 0
    # Площадка отдала только превью (SoundCloud Go+ SNIP, Яндекс без подписки): окно
    # взято из середины превью, а не трека.
    preview: bool = False
