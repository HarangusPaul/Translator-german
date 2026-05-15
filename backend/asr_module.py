"""
ASR stage: faster-whisper (CTranslate2 backend for Whisper-large-v3).

Pipeline role:  utterance_queue → [ASR] → transcript_queue

Design notes
------------
- A dedicated ThreadPoolExecutor keeps model inference off the event loop.
- temperature=0 + beam_size=5 gives deterministic, low-hallucination output.
- faster-whisper's internal VAD filter removes segments that are likely noise,
  acting as a second guard after Silero VAD upstream.
- Word-level timestamps are kept for future subtitle/sync applications.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from config import ASRConfig

logger = logging.getLogger(__name__)


@dataclass
class Transcript:
    text: str
    language: str
    asr_latency_ms: float = 0.0
    words: List = field(default_factory=list)  # faster_whisper.transcribe.Word objects


class ASRModule:
    """Wraps faster-whisper for async, non-blocking transcription."""

    def __init__(self, config: ASRConfig, language: str) -> None:
        self.cfg = config
        self.language = language       # "de" or "en"
        self._model = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

    def load(self) -> None:
        from faster_whisper import WhisperModel

        logger.info(
            "Loading Whisper %s on %s (%s) …",
            self.cfg.model_size,
            self.cfg.device,
            self.cfg.compute_type,
        )
        self._model = WhisperModel(
            self.cfg.model_size,
            device=self.cfg.device,
            compute_type=self.cfg.compute_type,
        )
        logger.info("Whisper ready")

    # ------------------------------------------------------------------
    # Async pipeline stage
    # ------------------------------------------------------------------

    async def run(
        self,
        in_queue: asyncio.Queue,   # receives np.ndarray utterances
        out_queue: asyncio.Queue,  # emits Transcript objects
    ) -> None:
        loop = asyncio.get_running_loop()
        while True:
            utterance: Optional[np.ndarray] = await in_queue.get()
            if utterance is None:
                await out_queue.put(None)
                in_queue.task_done()
                break

            t0 = time.perf_counter()
            transcript = await loop.run_in_executor(
                self._executor, self._transcribe_sync, utterance
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if transcript and transcript.text.strip():
                transcript.asr_latency_ms = elapsed_ms
                logger.info(
                    "[ASR  %5.0f ms | %s] %r",
                    elapsed_ms,
                    self.language.upper(),
                    transcript.text,
                )
                await out_queue.put(transcript)
            else:
                logger.debug("ASR: empty result (%.0f ms) – utterance discarded", elapsed_ms)

            in_queue.task_done()

    # ------------------------------------------------------------------
    # Synchronous inference (runs in executor thread)
    # ------------------------------------------------------------------

    def _transcribe_sync(self, audio: np.ndarray) -> Optional[Transcript]:
        assert self._model is not None, "Call load() before run()"
        try:
            segments, info = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=self.cfg.beam_size,
                temperature=self.cfg.temperature,
                word_timestamps=self.cfg.word_timestamps,
                vad_filter=self.cfg.vad_filter,
                vad_parameters=dict(
                    threshold=0.5,
                    min_silence_duration_ms=self.cfg.vad_min_silence_ms,
                ),
                condition_on_previous_text=self.cfg.condition_on_previous_text,
            )
            segs = list(segments)
            if not segs:
                return None
            text = " ".join(s.text.strip() for s in segs)
            words = [w for s in segs if s.words for w in s.words]
            return Transcript(text=text, language=info.language, words=words)
        except Exception:
            logger.exception("Transcription error")
            return None
