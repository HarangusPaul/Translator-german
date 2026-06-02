"""
Microphone capture + Silero-VAD utterance segmentation.

Architecture
------------
sounddevice callback (OS thread)
    → thread-safe raw_queue (queue.Queue)
    → _segmentation_thread (daemon thread, owns VAD state)
        → asyncio utterance_queue (async-safe via call_soon_threadsafe)
            → async generator stream() consumed by the pipeline
            Stefan Lupu
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import AsyncGenerator, Optional

import numpy as np
import sounddevice as sd
import torch

from config import AudioConfig

logger = logging.getLogger(__name__)

# Silero VAD requires exactly 512 samples at 16 kHz (32 ms per chunk)
_VAD_CHUNK = 512


class AudioCapture:
    """Captures mic audio, applies Silero VAD, and yields complete speech utterances."""

    def __init__(self, config: AudioConfig, loop: asyncio.AbstractEventLoop) -> None:
        self.cfg = config
        self._loop = loop
        self._block_samples = int(config.sample_rate * config.block_ms / 1000)
        self._min_speech = int(config.sample_rate * config.min_speech_ms / 1000)
        self._end_silence = int(config.sample_rate * config.end_silence_ms / 1000)
        self._max_utterance = int(config.sample_rate * config.max_utterance_s)

        # Bridge: SD callback (thread) → segmentation thread
        self._raw: queue.Queue[Optional[np.ndarray]] = queue.Queue(maxsize=100)
        # Bridge: segmentation thread → asyncio event loop
        self._utterances: asyncio.Queue[Optional[np.ndarray]] = asyncio.Queue(maxsize=10)

        self._running = False
        self._vad: Optional[torch.nn.Module] = None
        self._load_vad()

    # ------------------------------------------------------------------
    # VAD
    # ------------------------------------------------------------------

    def _load_vad(self) -> None:
        logger.info("Loading Silero VAD…")
        try:
            model, _ = torch.hub.load(
                "snakers4/silero-vad",
                "silero_vad",
                force_reload=False,
                trust_repo=True,
            )
        except TypeError:
            # PyTorch < 1.12 does not have trust_repo
            model, _ = torch.hub.load(
                "snakers4/silero-vad",
                "silero_vad",
                force_reload=False,
            )
        self._vad = model.eval()
        logger.info("Silero VAD ready")

    def _speech_prob(self, chunk: np.ndarray) -> float:
        """Return speech probability for exactly _VAD_CHUNK samples."""
        t = torch.from_numpy(chunk)
        with torch.no_grad():
            return self._vad(t, self.cfg.sample_rate).item()

    # ------------------------------------------------------------------
    # sounddevice callback – runs in a private OS thread
    # ------------------------------------------------------------------

    def _sd_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning("SD status: %s", status)
        chunk = indata[:, 0].copy().astype(np.float32)
        try:
            self._raw.put_nowait(chunk)
        except queue.Full:
            logger.warning("Raw audio queue full – frame dropped")

    # ------------------------------------------------------------------
    # VAD segmentation thread
    # ------------------------------------------------------------------

    def _segmentation_thread(self) -> None:
        """
        Reads raw blocks, runs per-32ms VAD, accumulates speech segments,
        and posts complete utterances to the asyncio queue via call_soon_threadsafe.
        """
        vad_buf = np.empty(0, dtype=np.float32)   # carries partial VAD chunks
        speech_bufs: list[np.ndarray] = []
        in_speech = False
        silence_acc = 0  # accumulated silence samples while in_speech

        def post_utterance() -> None:
            nonlocal speech_bufs, in_speech, silence_acc
            if speech_bufs:
                utt = np.concatenate(speech_bufs)
                if len(utt) >= self._min_speech:
                    logger.debug(
                        "Utterance ready: %.2f s", len(utt) / self.cfg.sample_rate
                    )
                    self._loop.call_soon_threadsafe(
                        self._utterances.put_nowait, utt.copy()
                    )
            speech_bufs.clear()
            in_speech = False
            silence_acc = 0

        while self._running:
            try:
                block = self._raw.get(timeout=0.25)
            except queue.Empty:
                # Forced flush if we've been in speech too long without audio
                if in_speech and sum(len(b) for b in speech_bufs) > self._max_utterance:
                    logger.warning("Max utterance length – forced flush")
                    post_utterance()
                continue

            if block is None:  # stop sentinel
                post_utterance()
                self._loop.call_soon_threadsafe(self._utterances.put_nowait, None)
                return

            vad_buf = np.concatenate([vad_buf, block])

            while len(vad_buf) >= _VAD_CHUNK:
                chunk = vad_buf[:_VAD_CHUNK]
                vad_buf = vad_buf[_VAD_CHUNK:]

                prob = self._speech_prob(chunk)
                is_speech = prob >= self.cfg.vad_threshold

                if is_speech:
                    if not in_speech:
                        logger.debug("Speech start (p=%.2f)", prob)
                        in_speech = True
                    silence_acc = 0
                    speech_bufs.append(chunk)

                elif in_speech:
                    # Trailing silence: keep buffering (ASR needs word endings) but count
                    silence_acc += _VAD_CHUNK
                    speech_bufs.append(chunk)

                    if silence_acc >= self._end_silence:
                        post_utterance()

                # Force-flush on max utterance length
                if in_speech and sum(len(b) for b in speech_bufs) >= self._max_utterance:
                    logger.warning("Max utterance length – forced flush")
                    post_utterance()

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncGenerator[np.ndarray, None]:
        """Async generator – yields float32 numpy arrays (complete speech utterances)."""
        self._running = True

        seg = threading.Thread(target=self._segmentation_thread, name="vad-seg", daemon=True)
        seg.start()

        with sd.InputStream(
            samplerate=self.cfg.sample_rate,
            channels=self.cfg.channels,
            dtype="float32",
            blocksize=self._block_samples,
            callback=self._sd_callback,
        ):
            logger.info(
                "Microphone open – %d Hz, %d-ms blocks",
                self.cfg.sample_rate,
                self.cfg.block_ms,
            )
            while True:
                utt = await self._utterances.get()
                if utt is None:
                    break
                yield utt

        seg.join(timeout=2.0)

    def stop(self) -> None:
        self._running = False
        try:
            self._raw.put_nowait(None)  # unblock segmentation thread
        except queue.Full:
            pass
