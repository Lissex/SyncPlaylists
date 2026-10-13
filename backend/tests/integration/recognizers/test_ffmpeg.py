"""FfmpegTrimmer. Сама вырезка — только в контейнере (маркер audio): ffmpeg на Windows-хосте
не запускаем (CLAUDE.md). Запуск: make test-audio."""

import asyncio
import os
import tempfile
import wave
from io import BytesIO
from pathlib import Path

import pytest

from syncplaylists.integrations.recognizers.ffmpeg import FfmpegTrimmer, ffmpeg_command
from syncplaylists.modules.recognition.domain.errors import AudioUnavailableError
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow, SourceAudio


def test_command_cuts_window_after_input_and_resamples() -> None:
    command = ffmpeg_command("ffmpeg", Path("in"), Path("out.wav"), 2_500, 18_000)

    assert command[command.index("-i") + 1] == "in"
    assert command.index("-ss") > command.index("-i")  # точная вырезка, после -i
    assert command[command.index("-ss") + 1] == "2.500"
    assert command[command.index("-t") + 1] == "18.000"
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[-1] == "out.wav"


async def _sine_mp3(seconds: int) -> bytes:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={seconds}", "-f", "mp3", "-",
        stdout=asyncio.subprocess.PIPE,
    )  # fmt: skip
    data, _ = await process.communicate()
    assert process.returncode == 0
    return data


@pytest.mark.audio
async def test_trims_window_to_wav_16k_mono_and_cleans_up() -> None:
    temp_root = tempfile.mkdtemp(prefix="trimmer-test-")
    trimmer = FfmpegTrimmer(temp_dir=temp_root, timeout_seconds=30)
    audio = SourceAudio(await _sine_mp3(30), FragmentWindow(5_000, 18_000), lead_ms=5_000)

    wav = await trimmer.to_wav(audio)

    with wave.open(BytesIO(wav)) as reader:
        assert reader.getnchannels() == 1
        assert reader.getframerate() == 16_000
        assert reader.getnframes() / 16_000 == pytest.approx(18, abs=0.1)
    assert os.listdir(temp_root) == []  # временный каталог вызова удалён


@pytest.mark.audio
async def test_garbage_is_no_audio_and_cleans_up() -> None:
    temp_root = tempfile.mkdtemp(prefix="trimmer-test-")
    trimmer = FfmpegTrimmer(temp_dir=temp_root, timeout_seconds=30)

    with pytest.raises(AudioUnavailableError):
        await trimmer.to_wav(SourceAudio(b"not audio at all", FragmentWindow(0, 18_000)))

    assert os.listdir(temp_root) == []
