"""
Summary module: generates a persistent session summary with Gemma.

This is intentionally separate from the translation/transcription pipeline so:
  - WebSocket streaming stays responsive
  - The (expensive) summary generation can be run once after session end
  - The produced summary text is saved into Firestore (not in-memory cache)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from config import SummaryConfig

logger = logging.getLogger(__name__)


class SummaryModule:
    def __init__(self, config: SummaryConfig) -> None:
        self.cfg = config
        self._tokenizer: Optional[AutoTokenizer] = None
        self._model: Optional[AutoModelForCausalLM] = None

    def load(self) -> None:
        """
        Blocking model load.
        Call this once at process start or lazily on first use.
        """
        hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
        device = "cuda" if torch.cuda.is_available() else "cpu"

        torch_dtype = torch.float16 if device == "cuda" else torch.float32

        logger.info("Loading summary model: %s", self.cfg.model_id)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.cfg.model_id,
            token=hf_token,
            use_fast=True,
        )

        # device_map keeps this practical on mixed hardware; it requires accelerate,
        # which we add to backend requirements.
        self._model = AutoModelForCausalLM.from_pretrained(
            self.cfg.model_id,
            token=hf_token,
            torch_dtype=torch_dtype,
            device_map="auto" if self.cfg.use_device_map_auto else None,
        )

        self._model.eval()
        logger.info("Summary model ready")

    def _make_prompt(self, transcript: str, output_language: str) -> str:
        # Keep the prompt stable to make outputs consistent over time.
        return (
            "You are a helpful assistant that summarizes translator session transcripts.\n"
            f"Write the summary in {output_language}.\n\n"
            "Return exactly these sections:\n"
            "1) Short summary (2-3 sentences)\n"
            "2) Key points (5-8 bullet points)\n"
            "3) Notable terminology (comma-separated)\n\n"
            "Transcript:\n"
            f"{transcript}\n"
        )

    def summarize(self, transcript: str, output_language: str) -> str:
        if not transcript.strip():
            return ""
        if self._tokenizer is None or self._model is None:
            raise RuntimeError("SummaryModule.load() must be called before summarize().")

        # Hard trim to avoid context overflow.
        transcript = transcript.strip()
        if len(transcript) > self.cfg.max_transcript_chars:
            transcript = transcript[-self.cfg.max_transcript_chars :]

        prompt = self._make_prompt(transcript=transcript, output_language=output_language)
        inputs = self._tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self.cfg.max_input_tokens,
        )

        # For non-sharded models, moving inputs to the model device avoids device mismatch.
        # For device_map="auto" sharded models this may be a no-op / best-effort.
        try:
            model_device = next(self._model.parameters()).device  # type: ignore[union-attr]
            inputs = {k: v.to(model_device) for k, v in inputs.items()}
        except Exception:
            pass

        with torch.no_grad():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=self.cfg.max_new_tokens,
                do_sample=self.cfg.do_sample,
                temperature=self.cfg.temperature,
                top_p=self.cfg.top_p,
                repetition_penalty=self.cfg.repetition_penalty,
                pad_token_id=self._tokenizer.eos_token_id,
            )

        full_text = self._tokenizer.decode(output_ids[0], skip_special_tokens=True)
        # Remove the prompt prefix if the model echoed it.
        if full_text.startswith(prompt):
            return full_text[len(prompt) :].strip()
        return full_text.strip()

