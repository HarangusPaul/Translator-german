"""FastAPI backend — German ↔ English web translator.

WebSocket protocol
------------------
Client → Server
  JSON  {"type":"start","direction":"de_to_en"|"en_to_de","sampleRate":16000}
  BIN   float32-LE PCM chunks at <sampleRate> Hz
  JSON  {"type":"stop"}

Server → Client
  JSON  {"type":"status","message":"ready"}
  JSON  {"type":"transcript","text":"…","lang":"de"|"en"}
  JSON  {"type":"translation","text":"…","lang":"en"|"de"}
  JSON  {"type":"audio","data":"<base64-mp3>"}   (en_to_de only)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

sys.path.insert(0, str(Path(__file__).parent))
from config import Direction, TranslationConfig, TTSConfig
from translation_module import TranslationModule

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("backend")

app = FastAPI(title="German ↔ English Translator API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Model state ──────────────────────────────────────────────────────────────

_whisper = None
_whisper_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
_tr_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marian")
_translator_de_en: Optional[TranslationModule] = None
_translator_en_de: Optional[TranslationModule] = None


@app.on_event("startup")
async def _startup() -> None:
    global _whisper, _translator_de_en, _translator_en_de
    import torch
    from faster_whisper import WhisperModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    logger.info("Loading Whisper large-v3 on %s (%s)…", device, compute_type)
    _whisper = WhisperModel("large-v3", device=device, compute_type=compute_type)
    logger.info("Whisper ready")

    _translator_de_en = TranslationModule(TranslationConfig(), Direction.DE_TO_EN)
    _translator_en_de = TranslationModule(TranslationConfig(), Direction.EN_TO_DE)
    _translator_de_en.load()
    _translator_en_de.load()
    logger.info("All models ready")


# ── Audio buffer with energy-based VAD ───────────────────────────────────────

class AudioBuffer:
    """Accumulates float32 PCM, yields complete utterances on silence."""

    def __init__(
        self,
        sample_rate: int = 16_000,
        rms_threshold: float = 0.018,
        end_silence_ms: int = 700,
        min_speech_ms: int = 250,
        max_s: float = 15.0,
    ) -> None:
        self._sr = sample_rate
        self._rms = rms_threshold
        self._end_sil = int(end_silence_ms * sample_rate / 1_000)
        self._min_spe = int(min_speech_ms * sample_rate / 1_000)
        self._max_sam = int(max_s * sample_rate)
        self._buf: list[np.ndarray] = []
        self._sil_acc = 0
        self._spe_acc = 0
        self._in_speech = False

    def add(self, chunk: np.ndarray) -> Optional[np.ndarray]:
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        self._buf.append(chunk)
        if rms > self._rms:
            self._in_speech = True
            self._spe_acc += len(chunk)
            self._sil_acc = 0
        elif self._in_speech:
            self._sil_acc += len(chunk)

        total = sum(len(b) for b in self._buf)
        if self._in_speech and (
            self._sil_acc >= self._end_sil or total >= self._max_sam
        ):
            return self._pop()
        return None

    def flush(self) -> Optional[np.ndarray]:
        return self._pop() if (self._in_speech and self._buf) else None

    def _pop(self) -> Optional[np.ndarray]:
        utt = np.concatenate(self._buf) if self._buf else None
        self._buf, self._sil_acc, self._spe_acc, self._in_speech = [], 0, 0, False
        return utt if (utt is not None and len(utt) >= self._min_spe) else None


# ── ML helpers ────────────────────────────────────────────────────────────────

def _transcribe_sync(audio: np.ndarray, lang: str) -> str:
    segs, _ = _whisper.transcribe(
        audio,
        language=lang,
        beam_size=5,
        temperature=0.0,
        vad_filter=True,
        vad_parameters={"threshold": 0.5, "min_silence_duration_ms": 500},
    )
    return " ".join(s.text.strip() for s in segs)


async def _transcribe(audio: np.ndarray, lang: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_whisper_exec, _transcribe_sync, audio, lang)


async def _translate(text: str, direction: Direction) -> Optional[str]:
    tr = _translator_de_en if direction == Direction.DE_TO_EN else _translator_en_de
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_tr_exec, tr._translate_sync, text)


async def _tts_mp3(text: str, voice: str) -> Optional[bytes]:
    import edge_tts
    try:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        chunks = [c["data"] async for c in communicate.stream() if c["type"] == "audio"]
        return b"".join(chunks) if chunks else None
    except Exception:
        logger.exception("TTS error")
        return None


def _resample_to_16k(audio: np.ndarray, orig_sr: int) -> np.ndarray:
    target_len = int(len(audio) * 16_000 / orig_sr)
    return np.interp(
        np.linspace(0, len(audio) - 1, target_len),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    direction = Direction.DE_TO_EN
    sample_rate = 16_000
    buf = AudioBuffer()
    tts_cfg = TTSConfig()

    def _ws_open() -> bool:
        return ws.client_state == WebSocketState.CONNECTED

    async def _send(payload: dict) -> bool:
        """Send JSON; return False (and swallow the error) if the socket is gone."""
        if not _ws_open():
            return False
        try:
            await ws.send_json(payload)
            return True
        except RuntimeError:
            return False

    async def process(audio: np.ndarray) -> None:
        src_lang = direction.src_lang
        transcript = await _transcribe(audio, src_lang)
        if not transcript.strip():
            return

        if not await _send({"type": "transcript", "text": transcript, "lang": src_lang}):
            return

        translation = await _translate(transcript, direction)
        if not translation:
            return

        tgt_lang = direction.tgt_lang
        if not await _send({"type": "translation", "text": translation, "lang": tgt_lang}):
            return

        if direction == Direction.EN_TO_DE:
            mp3 = await _tts_mp3(translation, tts_cfg.de_voice)
            if mp3:
                await _send({
                    "type": "audio",
                    "data": base64.b64encode(mp3).decode(),
                })

    pending: set[asyncio.Task] = set()

    def _spawn(audio: np.ndarray) -> None:
        t = asyncio.create_task(process(audio))
        pending.add(t)
        t.add_done_callback(pending.discard)

    try:
        while True:
            try:
                msg = await ws.receive()
            except RuntimeError:
                # Disconnect frame already consumed; exit cleanly
                break

            if "text" in msg:
                data = json.loads(msg["text"])
                kind = data.get("type")
                if kind == "start":
                    direction = Direction(data.get("direction", "de_to_en"))
                    sample_rate = int(data.get("sampleRate", 16_000))
                    buf = AudioBuffer()
                    await ws.send_json({"type": "status", "message": "ready"})
                elif kind == "stop":
                    # Flush any buffered audio as a task so mid-flight tasks
                    # and the flush all race to the same gather below
                    utt = buf.flush()
                    if utt is not None:
                        _spawn(utt)
                    # Wait for every in-flight process() to finish, then close
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    await _send({"type": "done"})
                    break

            elif "bytes" in msg:
                raw = np.frombuffer(msg["bytes"], dtype=np.float32)
                if sample_rate != 16_000:
                    raw = _resample_to_16k(raw, sample_rate)
                utt = buf.add(raw)
                if utt is not None:
                    _spawn(utt)

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception:
        logger.exception("WebSocket session error")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "models_loaded": _whisper is not None}


# Serve built frontend in production
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"  # HitlerTranslator/frontend/dist
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
