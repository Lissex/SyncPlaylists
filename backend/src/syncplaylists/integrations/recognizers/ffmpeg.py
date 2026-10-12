"""Вырезка окна и перекодирование в wav 16 кГц mono — ffmpeg, только в контейнере
(worker-recognize). Входные байты и результат лежат во временном каталоге одного вызова
(в compose — tmpfs /tmp/recognize) и удаляются в любом исходе, в том числе при ошибке."""

import asyncio
import contextlib
import tempfile
from pathlib import Path

from syncplaylists.modules.recognition.domain.errors import (
    AudioTemporarilyUnavailableError,
    AudioUnavailableError,
    NoAudioReason,
)
from syncplaylists.modules.recognition.domain.fragments import SourceAudio

_WAV_HEADER_BYTES = 44
SAMPLE_RATE = 16_000


def ffmpeg_command(
    ffmpeg: str, source: Path, target: Path, lead_ms: int, length_ms: int
) -> list[str]:
    # -ss после -i: точная (по декодированным сэмплам) вырезка — вход небольшой, а
    # склеенные HLS-сегменты и кусок mp3 «с середины» быстрая перемотка понимает плохо.
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ss",
        f"{lead_ms / 1000:.3f}",
        "-t",
        f"{length_ms / 1000:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "wav",
        str(target),
    ]


class FfmpegTrimmer:
    def __init__(
        self, *, temp_dir: str | None, timeout_seconds: float, ffmpeg: str = "ffmpeg"
    ) -> None:
        self._temp_dir = temp_dir
        self._timeout = timeout_seconds
        self._ffmpeg = ffmpeg

    async def to_wav(self, audio: SourceAudio) -> bytes:
        if self._temp_dir is not None:
            Path(self._temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="recognize-", dir=self._temp_dir) as workdir:
            source, target = Path(workdir) / "in", Path(workdir) / "out.wav"
            source.write_bytes(audio.data)
            command = ffmpeg_command(
                self._ffmpeg, source, target, audio.lead_ms, audio.window.length_ms
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise AudioTemporarilyUnavailableError(f"ffmpeg не запустился: {exc}") from exc
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
            except TimeoutError as exc:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
                raise AudioTemporarilyUnavailableError("ffmpeg: таймаут") from exc
            if process.returncode != 0:
                detail = stderr.decode(errors="replace").strip()[:200]
                raise AudioUnavailableError(NoAudioReason.NO_STREAM, f"ffmpeg: {detail}")
            wav = target.read_bytes() if target.exists() else b""
        if len(wav) <= _WAV_HEADER_BYTES:
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, "ffmpeg: пустой wav")
        return wav
