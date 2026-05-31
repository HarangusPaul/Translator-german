"""
Central configuration dataclasses for every pipeline stage.
All timing values are in milliseconds / seconds as noted.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Direction(Enum):
    DE_TO_EN = "de_to_en"
    EN_TO_DE = "en_to_de"

    @property
    def src_lang(self) -> str:
        return "de" if self == Direction.DE_TO_EN else "en"

    @property
    def tgt_lang(self) -> str:
        return "en" if self == Direction.DE_TO_EN else "de"


@dataclass
class AudioConfig:
    sample_rate: int = 16_000
    channels: int = 1
    # sounddevice blocksize (100 ms gives good VAD resolution without callback overhead)
    block_ms: int = 100
    # VAD: speech prob must exceed this to start buffering
    vad_threshold: float = 0.5
    # VAD: prob must fall below this to stop buffering
    vad_neg_threshold: float = 0.35
    # Minimum continuous speech before forwarding to ASR (avoids single-word spurts)
    min_speech_ms: int = 400
    # Consecutive silence that marks end-of-utterance
    end_silence_ms: int = 600
    # Hard cap on utterance length – forces a flush to bound ASR latency
    max_utterance_s: float = 15.0


@dataclass
class ASRConfig:
    model_size: str = "large-v3"
    device: str = "cuda"
    # float16 on GPU; use int8 or int8_float16 for CPU / low-VRAM GPU
    compute_type: str = "float16"
    beam_size: int = 5
    # temperature=0 → pure beam search, minimises hallucinations
    temperature: float = 0.0
    word_timestamps: bool = True
    # faster-whisper built-in VAD — disabled on macOS (Silero torch.hub download
    # crashes with OMP conflict); client-side RMS gate in audio worklet suffices
    vad_filter: bool = False
    vad_min_silence_ms: int = 500
    # Disable to keep each utterance independent and reduce drift
    condition_on_previous_text: bool = False


@dataclass
class TranslationConfig:
    de_to_en_model: str = "Helsinki-NLP/opus-mt-de-en"
    en_to_de_model: str = "Helsinki-NLP/opus-mt-en-de"
    max_input_length: int = 512
    num_beams: int = 4
    de_spacy_model: str = "de_core_news_sm"
    en_spacy_model: str = "en_core_web_sm"


@dataclass
class TTSConfig:
    # Microsoft Edge Neural TTS voices (no local model download required)
    en_voice: str = "en-US-JennyNeural"
    de_voice: str = "de-DE-KatjaNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    # edge-tts always streams MP3; we resample to this for playback
    output_sample_rate: int = 24_000


@dataclass
class SummaryConfig:
    # philschmid/bart-large-cnn-samsum: ~400 MB, public, fine-tuned on dialogue
    model_id: str = "philschmid/bart-large-cnn-samsum"
    max_new_tokens: int = 150
    do_sample: bool = False
    max_transcript_chars: int = 6_000
