"""
Entry point for the real-time German ↔ English audio translation system.

Usage examples
--------------
  # GPU (recommended)
  python main.py --direction de_to_en

  # CPU (slower; use a smaller model)
  python main.py --direction en_to_de --device cpu --compute-type int8 --model small

  # Verbose latency logging
  python main.py --direction de_to_en -v
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from config import ASRConfig, AudioConfig, Direction, TranslationConfig, TTSConfig
from pipeline import RealtimePipeline


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Real-time German ↔ English audio translation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--direction",
        choices=["de_to_en", "en_to_de"],
        default="de_to_en",
        help="Translation direction",
    )
    p.add_argument(
        "--device",
        choices=["cuda", "cpu"],
        default=None,
        help="Whisper compute device (auto-detected if omitted)",
    )
    p.add_argument(
        "--compute-type",
        dest="compute_type",
        choices=["float16", "int8_float16", "int8", "float32"],
        default=None,
        help="Whisper quantisation (auto-selected based on device if omitted)",
    )
    p.add_argument(
        "--model",
        dest="model_size",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        default="large-v3",
        help="Whisper model size (use 'medium' or 'small' on CPU)",
    )
    p.add_argument(
        "--end-silence-ms",
        dest="end_silence_ms",
        type=int,
        default=600,
        help="Silence duration (ms) that triggers end-of-utterance detection",
    )
    p.add_argument(
        "--vad-threshold",
        dest="vad_threshold",
        type=float,
        default=0.5,
        help="Silero VAD speech probability threshold [0, 1]",
    )
    p.add_argument(
        "--en-voice",
        dest="en_voice",
        default="en-US-JennyNeural",
        help="Edge TTS English voice name",
    )
    p.add_argument(
        "--de-voice",
        dest="de_voice",
        default="de-DE-KatjaNeural",
        help="Edge TTS German voice name",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    return p


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d %(levelname)-8s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stdout,
    )
    # Silence excessively chatty third-party loggers
    for lib in ("transformers", "faster_whisper", "httpx", "httpcore", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Async main
# ---------------------------------------------------------------------------

async def _async_main(args: argparse.Namespace) -> None:
    import torch

    # Auto-detect device / compute type
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    if args.compute_type is not None:
        compute_type = args.compute_type
    else:
        compute_type = "float16" if device == "cuda" else "int8"

    log = logging.getLogger("main")
    log.info("Device: %s | Compute type: %s | Model: %s", device, compute_type, args.model_size)
    if device == "cpu" and args.model_size in ("large-v2", "large-v3"):
        log.warning(
            "large-v3 on CPU is very slow (>10 s/utterance). "
            "Consider --model medium or --model small."
        )

    direction = Direction(args.direction)

    audio_cfg = AudioConfig(
        end_silence_ms=args.end_silence_ms,
        vad_threshold=args.vad_threshold,
    )
    asr_cfg = ASRConfig(
        model_size=args.model_size,
        device=device,
        compute_type=compute_type,
    )
    tts_cfg = TTSConfig(
        en_voice=args.en_voice,
        de_voice=args.de_voice,
    )

    pipeline = RealtimePipeline(
        direction=direction,
        audio_config=audio_cfg,
        asr_config=asr_cfg,
        tts_config=tts_cfg,
    )

    log.info("Loading ML models (first run will download weights)…")
    pipeline.load_models()

    try:
        await pipeline.run()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
    finally:
        pipeline.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = _build_parser().parse_args()
    _setup_logging(args.verbose)
    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
