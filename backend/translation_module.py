"""
NMT stage: Helsinki-NLP MarianMT with sentence-boundary buffering.

Pipeline role:  transcript_queue → [Translation] → text_queue

Design notes
------------
- Sentence buffering prevents feeding partial sentences to MarianMT, which
  causes quality degradation (the model sees truncated context).
- spaCy provides accurate SBD; a regex fallback activates if spaCy is absent.
- The incomplete "tail" sentence is held in a buffer and prepended to the
  next transcript, so no text is lost across chunk boundaries.
- A forced flush on None sentinel ensures the final partial sentence is translated.
- MarianMT is moved to CUDA when available; inference runs in a dedicated thread
  pool to avoid blocking the asyncio event loop.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import torch
from transformers import MarianMTModel, MarianTokenizer

from asr_module import Transcript
from config import Direction, TranslationConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentence boundary detection
# ---------------------------------------------------------------------------

class SentenceSplitter:
    # Splits on whitespace that follows sentence-ending punctuation and
    # precedes a capital/uppercase letter (covers German nouns too).
    _FALLBACK_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜÀ-ÖØ-Þ])")

    def __init__(self, spacy_model: str) -> None:
        self._nlp = None
        try:
            import spacy

            self._nlp = spacy.load(spacy_model, disable=["ner", "textcat", "lemmatizer"])
            logger.info("spaCy '%s' ready for SBD", spacy_model)
        except Exception as exc:
            logger.warning("spaCy unavailable (%s) – falling back to regex SBD", exc)

    def split(self, text: str) -> List[str]:
        text = text.strip()
        if not text:
            return []
        if self._nlp is not None:
            return [s.text.strip() for s in self._nlp(text).sents if s.text.strip()]
        parts = self._FALLBACK_RE.split(text)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def is_complete(text: str) -> bool:
        """True if the text ends with a sentence-terminal character."""
        return bool(re.search(r"[.!?]\s*$", text.strip()))


# ---------------------------------------------------------------------------
# Translation module
# ---------------------------------------------------------------------------

class TranslationModule:
    """MarianMT with async sentence-buffered translation."""

    def __init__(self, config: TranslationConfig, direction: Direction) -> None:
        self.cfg = config
        self.direction = direction
        self._tokenizer: Optional[MarianTokenizer] = None
        self._model: Optional[MarianMTModel] = None
        self._buffer = ""   # carries incomplete sentence across utterance boundaries
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marian")

        spacy_model = (
            config.de_spacy_model
            if direction == Direction.DE_TO_EN
            else config.en_spacy_model
        )
        self._sbd = SentenceSplitter(spacy_model)

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        model_name = (
            self.cfg.de_to_en_model
            if self.direction == Direction.DE_TO_EN
            else self.cfg.en_to_de_model
        )
        logger.info("Loading MarianMT: %s …", model_name)
        self._tokenizer = MarianTokenizer.from_pretrained(model_name)
        self._model = MarianMTModel.from_pretrained(model_name)
        self._model.eval()
        if torch.cuda.is_available():
            self._model = self._model.cuda()
        logger.info("MarianMT ready")

    # ------------------------------------------------------------------
    # Async pipeline stage
    # ------------------------------------------------------------------

    async def run(
        self,
        in_queue: asyncio.Queue,   # receives Transcript objects
        out_queue: asyncio.Queue,  # emits translated str objects
    ) -> None:
        loop = asyncio.get_running_loop()
        while True:
            item: Optional[Transcript] = await in_queue.get()

            if item is None:
                # Flush whatever remains in the buffer before shutting down
                if self._buffer.strip():
                    result = await self._translate(loop, self._buffer.strip())
                    if result:
                        await out_queue.put(result)
                    self._buffer = ""
                await out_queue.put(None)
                in_queue.task_done()
                break

            # Append new transcription to the running buffer
            self._buffer = (self._buffer + " " + item.text).strip()
            await self._flush_sentences(loop, out_queue)
            in_queue.task_done()

    async def _flush_sentences(
        self, loop: asyncio.AbstractEventLoop, out_queue: asyncio.Queue
    ) -> None:
        sentences = self._sbd.split(self._buffer)

        if not sentences:
            return

        if len(sentences) == 1:
            # Single sentence – translate only if it looks complete
            if SentenceSplitter.is_complete(self._buffer):
                result = await self._translate(loop, self._buffer)
                if result:
                    await out_queue.put(result)
                self._buffer = ""
            return

        # Multiple sentences: translate all complete ones; keep last (may be partial)
        complete_text = " ".join(sentences[:-1])
        self._buffer = sentences[-1]

        result = await self._translate(loop, complete_text)
        if result:
            await out_queue.put(result)

    # ------------------------------------------------------------------
    # Async ↔ sync bridge
    # ------------------------------------------------------------------

    async def _translate(
        self, loop: asyncio.AbstractEventLoop, text: str
    ) -> Optional[str]:
        t0 = time.perf_counter()
        result = await loop.run_in_executor(self._executor, self._translate_sync, text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if result:
            logger.info("[NMT  %5.0f ms] %r → %r", elapsed_ms, text[:80], result[:80])
        return result

    def _translate_sync(self, text: str) -> Optional[str]:
        assert self._tokenizer and self._model
        try:
            inputs = self._tokenizer(
                text,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=self.cfg.max_input_length,
            )
            if next(self._model.parameters()).is_cuda:
                inputs = {k: v.cuda() for k, v in inputs.items()}

            with torch.no_grad():
                output_ids = self._model.generate(
                    **inputs,
                    num_beams=self.cfg.num_beams,
                    max_length=self.cfg.max_input_length,
                )
            return self._tokenizer.decode(output_ids[0], skip_special_tokens=True)
        except Exception:
            logger.exception("Translation error")
            return None
