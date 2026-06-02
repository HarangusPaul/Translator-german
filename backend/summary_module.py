"""
Generates a short plain-text summary of a translator session using a
HuggingFace BART seq2seq model fine-tuned on dialogue summarization.
Haragus Paul Andrei + Stefan Lupu
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

from config import SummaryConfig

logger = logging.getLogger(__name__)


class SummaryModule:
    def __init__(self, config: SummaryConfig) -> None:
        self.cfg = config
        self._tokenizer: Optional[AutoTokenizer] = None
        self._model: Optional[AutoModelForSeq2SeqLM] = None
        self._device: str = "cpu"

    def load(self) -> None:
        hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN") or None
        self._device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info("Loading summary model %s on %s …", self.cfg.model_id, self._device)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.cfg.model_id, token=hf_token
        )
        self._model = AutoModelForSeq2SeqLM.from_pretrained(
            self.cfg.model_id,
            token=hf_token,
            torch_dtype=torch.float32,
        ).to(self._device)
        self._model.eval()
        logger.info("Summary model ready on %s", self._device)

    def summarize(self, transcript: str, output_language: str) -> str:
        if not transcript.strip():
            return ""
        if self._tokenizer is None or self._model is None:
            raise RuntimeError("Call load() before summarize().")

        transcript = transcript.strip()
        if len(transcript) > self.cfg.max_transcript_chars:
            transcript = transcript[-self.cfg.max_transcript_chars:]

        inputs = self._tokenizer(
            transcript,
            return_tensors="pt",
            max_length=1024,
            truncation=True,
        ).to(self._device)

        with torch.no_grad():
            output_ids = self._model.generate(
                inputs["input_ids"],
                max_length=self.cfg.max_new_tokens,
                min_length=30,
                num_beams=4,
                early_stopping=True,
                no_repeat_ngram_size=3,
            )

        return self._tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()
