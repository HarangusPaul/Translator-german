"""
RealtimePipeline – wires AudioCapture → ASR → Translation → TTS via asyncio.Queue.

Queue topology
--------------
  mic audio (np.ndarray)
      ↓  asr_in  [maxsize=20]
  Transcript objects
      ↓  tr_in   [maxsize=20]
  translated str
      ↓  tts_in  [maxsize=20]
  speaker

Each stage propagates a None sentinel when it is done, causing downstream
stages to flush their buffers and shut down gracefully.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from audio_capture import AudioCapture
from asr_module import ASRModule
from config import ASRConfig, AudioConfig, Direction, TranslationConfig, TTSConfig
from translation_module import TranslationModule
from tts_module import TTSModule

logger = logging.getLogger(__name__)


class RealtimePipeline:
    """Orchestrates the full audio translation pipeline."""

    def __init__(
        self,
        direction: Direction,
        audio_config: Optional[AudioConfig] = None,
        asr_config: Optional[ASRConfig] = None,
        translation_config: Optional[TranslationConfig] = None,
        tts_config: Optional[TTSConfig] = None,
    ) -> None:
        self.direction = direction
        self.audio_cfg = audio_config or AudioConfig()
        self.asr_cfg = asr_config or ASRConfig()
        self.tr_cfg = translation_config or TranslationConfig()
        self.tts_cfg = tts_config or TTSConfig()

        self._running = False
        # Modules are created in load_models() because ASRModule needs to
        # download weights, which is easier to do before the asyncio loop starts.
        self.asr: Optional[ASRModule] = None
        self.translator: Optional[TranslationModule] = None
        self.tts: Optional[TTSModule] = None
        self.audio_capture: Optional[AudioCapture] = None

    # ------------------------------------------------------------------
    # Blocking model load (call this before asyncio.run)
    # ------------------------------------------------------------------

    def load_models(self) -> None:
        self.asr = ASRModule(self.asr_cfg, language=self.direction.src_lang)
        self.translator = TranslationModule(self.tr_cfg, direction=self.direction)
        self.tts = TTSModule(self.tts_cfg, direction=self.direction)

        self.asr.load()
        self.translator.load()
        logger.info("All models ready – pipeline can start")

    # ------------------------------------------------------------------
    # Async entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        assert self.asr is not None, "Call load_models() before run()"

        loop = asyncio.get_running_loop()
        self._running = True
        self.audio_capture = AudioCapture(self.audio_cfg, loop)

        asr_in: asyncio.Queue = asyncio.Queue(maxsize=20)
        tr_in: asyncio.Queue = asyncio.Queue(maxsize=20)
        tts_in: asyncio.Queue = asyncio.Queue(maxsize=20)

        logger.info(
            "Pipeline running: %s → %s  |  Speak into the microphone. Ctrl+C to stop.",
            self.direction.src_lang.upper(),
            self.direction.tgt_lang.upper(),
        )

        t_start = time.perf_counter()

        async def feed_audio() -> None:
            async for utt in self.audio_capture.stream():
                if not self._running:
                    break
                elapsed = time.perf_counter() - t_start
                logger.debug(
                    "[%.2f s] Utterance → ASR (%.2f s audio)",
                    elapsed,
                    len(utt) / self.audio_cfg.sample_rate,
                )
                await asr_in.put(utt)
            await asr_in.put(None)

        try:
            await asyncio.gather(
                feed_audio(),
                self.asr.run(asr_in, tr_in),
                self.translator.run(tr_in, tts_in),
                self.tts.run(tts_in),
            )
        except asyncio.CancelledError:
            logger.info("Pipeline cancelled")
        finally:
            self._running = False
            if self.audio_capture:
                self.audio_capture.stop()
            logger.info("Pipeline stopped after %.1f s", time.perf_counter() - t_start)

    def stop(self) -> None:
        self._running = False
        if self.audio_capture:
            self.audio_capture.stop()
