"""
TTS stage: Microsoft Edge Neural TTS via edge-tts (async, no local model).

Pipeline role:  text_queue → [TTS] → speaker

Design notes
------------
- edge-tts streams MP3 chunks from Microsoft's servers; no GPU needed.
- MP3 decoding uses ffmpeg subprocess (fast, reliable on all platforms).
  pydub is tried as a fallback; if both fail the segment is skipped with a warning.
- A dedicated asyncio Queue serialises playback so sentences never overlap.
- Synthesis and decode run concurrently with playback: while the speaker plays
  sentence N, sentence N+1 is already being synthesised (1-deep pipeline).
- sounddevice.play(blocking=True) runs in a thread pool to avoid stalling the loop.
    Haragus Paul Andrei
"""
from __future__ import annotations

import asyncio
import io
import logging
import subprocess
import time
from typing import Optional

import numpy as np
import sounddevice as sd

from config import Direction, TTSConfig

logger = logging.getLogger(__name__)


class TTSModule:
    """Synthesises translated text and plays it through the default audio output."""

    def __init__(self, config: TTSConfig, direction: Direction) -> None:
        self.cfg = config
        # Output language is the TARGET of translation
        self.voice = (
            config.en_voice if direction == Direction.DE_TO_EN else config.de_voice
        )
        # maxsize=2 lets synthesis run one sentence ahead of playback
        self._play_queue: asyncio.Queue[Optional[np.ndarray]] = asyncio.Queue(maxsize=2)

    # ------------------------------------------------------------------
    # Async pipeline stage
    # ------------------------------------------------------------------

    async def run(self, in_queue: asyncio.Queue) -> None:
        playback = asyncio.create_task(self._playback_worker())

        while True:
            text: Optional[str] = await in_queue.get()
            if text is None:
                await self._play_queue.put(None)
                in_queue.task_done()
                break

            t0 = time.perf_counter()
            audio = await self._synthesise(text)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if audio is not None:
                logger.info(
                    "[TTS  %5.0f ms] %d samples → %r",
                    elapsed_ms,
                    len(audio),
                    text[:70],
                )
                await self._play_queue.put(audio)
            else:
                logger.warning("TTS returned no audio for: %r", text[:70])

            in_queue.task_done()

        await playback

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    async def _synthesise(self, text: str) -> Optional[np.ndarray]:
        import edge_tts

        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.cfg.rate,
                volume=self.cfg.volume,
            )
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])

            if not chunks:
                return None

            mp3_data = b"".join(chunks)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._decode_mp3, mp3_data)

        except Exception:
            logger.exception("TTS synthesis error")
            return None

    # ------------------------------------------------------------------
    # MP3 decoding
    # ------------------------------------------------------------------

    def _decode_mp3(self, mp3_data: bytes) -> Optional[np.ndarray]:
        """Decode MP3 → float32 PCM; ffmpeg primary, pydub fallback."""
        audio = self._decode_ffmpeg(mp3_data)
        if audio is not None:
            return audio
        return self._decode_pydub(mp3_data)

    def _decode_ffmpeg(self, mp3_data: bytes) -> Optional[np.ndarray]:
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-loglevel", "quiet",
                    "-i", "pipe:0",
                    "-f", "f32le",
                    "-ar", str(self.cfg.output_sample_rate),
                    "-ac", "1",
                    "pipe:1",
                ],
                input=mp3_data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            return np.frombuffer(result.stdout, dtype=np.float32)
        except FileNotFoundError:
            logger.warning("ffmpeg not found – falling back to pydub (also needs ffmpeg)")
            return None
        except subprocess.CalledProcessError as exc:
            logger.warning("ffmpeg decode failed: %s", exc.stderr.decode()[:200])
            return None

    def _decode_pydub(self, mp3_data: bytes) -> Optional[np.ndarray]:
        try:
            from pydub import AudioSegment

            seg = (
                AudioSegment.from_mp3(io.BytesIO(mp3_data))
                .set_channels(1)
                .set_frame_rate(self.cfg.output_sample_rate)
            )
            samples = np.array(seg.get_array_of_samples(), dtype=np.int16)
            return samples.astype(np.float32) / 32_768.0
        except Exception:
            logger.exception("pydub decode error")
            return None

    # ------------------------------------------------------------------
    # Sequential playback worker
    # ------------------------------------------------------------------

    async def _playback_worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            audio = await self._play_queue.get()
            if audio is None:
                self._play_queue.task_done()
                break
            t0 = time.perf_counter()
            await loop.run_in_executor(None, self._play_blocking, audio)
            logger.debug("Playback %.2f s", time.perf_counter() - t0)
            self._play_queue.task_done()

    def _play_blocking(self, audio: np.ndarray) -> None:
        sd.play(audio, samplerate=self.cfg.output_sample_rate, blocking=True)
        sd.wait()
