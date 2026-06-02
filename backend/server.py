"""
FastAPI WebSocket + REST server for the real-time translation app.

WebSocket /ws  — streaming audio → StreamingTranslationPipeline → JSON events
REST /sessions — CRUD backed by Firestore, all routes require Firebase ID token

Run:
    uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

# ── Path setup so existing backend modules are importable ──────────────────
sys.path.insert(0, str(Path(__file__).parent))

# ── Load .env before any Firebase / config imports ────────────────────────
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import firebase_admin
from firebase_admin import auth as fb_auth
from firebase_admin import credentials, firestore

from config import ASRConfig, Direction, SummaryConfig, TranslationConfig, TTSConfig
from asr_module import ASRModule
from translation_module import TranslationModule
from tts_module import TTSModule
from summary_module import SummaryModule

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(levelname)-8s %(name)-20s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
for _lib in ("transformers", "faster_whisper", "httpx", "httpcore", "urllib3"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

logger = logging.getLogger("server")

# ── Firebase init ──────────────────────────────────────────────────────────
_cred_path = os.getenv(
    "FIREBASE_CREDENTIALS",
    str(Path(__file__).parent / "firebase-credentials.json"),
)
firebase_admin.initialize_app(credentials.Certificate(_cred_path))
db = firestore.client()

# ── FastAPI app ────────────────────────────────────────────────────────────
app = FastAPI(title="HitlerTranslator API", redirect_slashes=False)


class _StripTrailingSlash(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/" and request.url.path.endswith("/"):
            request.scope["path"] = request.url.path.rstrip("/")
        return await call_next(request)


app.add_middleware(_StripTrailingSlash)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": str(exc)})

# ── Model cache (keyed by Direction.value) ─────────────────────────────────
_pipeline_models: dict[str, tuple[ASRModule, TranslationModule, TTSModule]] = {}
_model_load_lock = asyncio.Lock()


async def _get_models(direction: Direction) -> tuple[ASRModule, TranslationModule, TTSModule]:
    key = direction.value
    if key in _pipeline_models:
        return _pipeline_models[key]

    async with _model_load_lock:
        if key in _pipeline_models:
            return _pipeline_models[key]

        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "float32"
        logger.info("Loading models for %s on %s …", direction.value, device)

        asr = ASRModule(
            ASRConfig(device=device, compute_type=compute_type),
            language=direction.src_lang,
        )
        translator = TranslationModule(TranslationConfig(), direction=direction)
        tts = TTSModule(TTSConfig(), direction=direction)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, asr.load)
        await loop.run_in_executor(None, translator.load)

        _pipeline_models[key] = (asr, translator, tts)
        logger.info("Models ready for %s", direction.value)
        return asr, translator, tts


# ── Summary module (Gemma) state ──────────────────────────────────────────
_summary_module: Optional[SummaryModule] = None
_summary_load_lock = asyncio.Lock()
_summary_generation_lock = asyncio.Lock()


async def _get_summary_module() -> SummaryModule:
    global _summary_module
    if _summary_module:
        return _summary_module

    async with _summary_load_lock:
        if _summary_module:
            return _summary_module

        module = SummaryModule(SummaryConfig())
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, module.load)
        _summary_module = module
        return _summary_module


def _build_transcript_for_summary(messages: list[dict], direction: Direction) -> str:
    # Build a compact bilingual transcript from saved Firestore message pairs.
    # This is fed into Gemma to generate a session summary.
    src_is_de = direction.src_lang == "de"
    lines: list[str] = []
    for m in messages:
        src = (m.get("source_text") or "").strip()
        tr = (m.get("translated_text") or "").strip()
        if not src and not tr:
            continue

        if src_is_de:
            # de_to_en: source=German, translated=English
            lines.append(f"DE: {src}\nEN: {tr}".strip())
        else:
            # en_to_de: source=English, translated=German
            lines.append(f"EN: {src}\nDE: {tr}".strip())
    return "\n\n".join(lines).strip()


async def _generate_and_persist_session_summary(
    uid: str, session_id: str, direction: Direction
) -> None:
    """
    Generates a summary once and persists it into:
      users/{uid}/sessions/{session_id}
    This avoids "losing" summaries via in-memory caching.
    """
    if not session_id:
        return

    sess_ref = (
        db.collection("users").document(uid).collection("sessions").document(session_id)
    )

    # 1) Check if we already have a saved summary.
    def _get_session_data():
        doc = sess_ref.get()
        return doc.to_dict() if doc.exists else None

    data = await asyncio.to_thread(_get_session_data)
    if data and data.get("summary_status") == "done" and data.get("summary"):
        return
    if data and data.get("summary_status") == "pending":
        return

    # 2) Mark as pending so we don't start multiple generations.
    await asyncio.to_thread(
        lambda: sess_ref.set(
            {
                "summary_status": "pending",
            },
            merge=True,
        )
    )

    try:
        # 3) Load messages from Firestore.
        def _read_messages():
            msgs = []
            for d in (
                sess_ref.collection("messages").order_by("timestamp").stream()
            ):
                msgs.append(d.to_dict())
            return msgs

        messages = await asyncio.to_thread(_read_messages)
        transcript = _build_transcript_for_summary(messages, direction)
        if not transcript:
            await asyncio.to_thread(
                lambda: sess_ref.set(
                    {"summary_status": "done", "summary": ""},
                    merge=True,
                )
            )
            return

        # 4) Generate summary (run in a lock so only one model.generate at a time).
        async with _summary_generation_lock:
            summary_module = await _get_summary_module()

            output_language = "German" if direction.src_lang == "de" else "English"
            summary_text = await asyncio.to_thread(
                summary_module.summarize, transcript, output_language
            )

        # 5) Persist.
        await asyncio.to_thread(
            lambda: sess_ref.set(
                {
                    "summary": summary_text,
                    "summary_status": "done",
                    "summary_generated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
        )
    except Exception as exc:
        logger.exception("Summary generation failed for %s/%s", uid, session_id)
        await asyncio.to_thread(
            lambda: sess_ref.set(
                {
                    "summary_status": "error",
                    "summary_error": str(exc),
                },
                merge=True,
            )
        )


# ── Auth helper ────────────────────────────────────────────────────────────

async def _verify_token(token: str) -> str:
    """Return uid or raise HTTPException(401)."""
    try:
        decoded = await asyncio.to_thread(fb_auth.verify_id_token, token)
        return decoded["uid"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def get_current_user(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    return await _verify_token(authorization[7:])


# ── Streaming pipeline (one per WebSocket connection) ─────────────────────

class StreamingTranslationPipeline:
    """
    Implements the 3-layer real-time strategy:
      Layer 1 — partial Whisper transcription every 800 ms
      Layer 2 — incremental MarianMT translation on word-boundary deltas
      Layer 3 — predictive edge-tts pre-synthesis on sentence-terminal partials
    """

    PARTIAL_INTERVAL_S: float = 0.8
    MAX_UTTERANCE_S: float = 30.0                              # force-flush at 30 s
    SAMPLE_RATE: int = 16_000
    BYTES_PER_SAMPLE: int = 2                                  # Int16
    MIN_PARTIAL_BYTES: int = int(SAMPLE_RATE * 0.4 * BYTES_PER_SAMPLE)  # 400 ms
    MIN_FINAL_BYTES: int = int(SAMPLE_RATE * 0.3 * BYTES_PER_SAMPLE)    # 300 ms
    MAX_UTTERANCE_BYTES: int = int(SAMPLE_RATE * MAX_UTTERANCE_S * BYTES_PER_SAMPLE)

    def __init__(
        self,
        uid: str,
        session_id: Optional[str],
        direction: Direction,
        websocket: WebSocket,
        asr: ASRModule,
        translator: TranslationModule,
        tts: TTSModule,
    ) -> None:
        self.uid = uid
        self.session_id = session_id
        self.direction = direction
        self.ws = websocket
        self._asr = asr
        self._translator = translator
        self._tts = tts

        self._audio_buf = bytearray()
        # Serialises all transcription / translation calls for this connection
        self._transcribe_lock = asyncio.Lock()
        # Per-connection executor (max_workers=1) keeps model calls sequential
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ws-infer")

        # Incremental translation state (reset on each silence boundary)
        self._last_translated_prefix: str = ""
        self._last_translation_so_far: str = ""

        self._partial_timer_task: Optional[asyncio.Task] = None
        self._pre_synth_task: Optional[asyncio.Task] = None
        self._pre_synth_mp3: Optional[bytes] = None

    # ── Audio ingestion ────────────────────────────────────────────────────

    async def handle_audio_frame(self, frame: bytes) -> None:
        if len(frame) == 0:
            if self._audio_buf:
                logger.info("Silence boundary — buf=%d bytes, finalising", len(self._audio_buf))
            await self._finalize_utterance()
            return

        if not self._audio_buf:
            logger.info("First audio frame received (%d bytes)", len(frame))
        self._audio_buf.extend(frame)

        # Force-flush when the buffer exceeds Whisper's sweet spot
        if len(self._audio_buf) >= self.MAX_UTTERANCE_BYTES:
            logger.info("Max utterance length reached — force-flushing")
            await self._finalize_utterance()
            return

        # Start the partial-transcription heartbeat if it isn't running
        if self._partial_timer_task is None or self._partial_timer_task.done():
            self._partial_timer_task = asyncio.create_task(self._partial_timer_loop())

    # ── Partial transcription loop (Layer 1 + 2) ──────────────────────────

    async def _partial_timer_loop(self) -> None:
        while True:
            await asyncio.sleep(self.PARTIAL_INTERVAL_S)
            try:
                await self._run_partial()
            except Exception:
                logger.exception("Partial transcription loop error")

    async def _run_partial(self) -> None:
        if len(self._audio_buf) < self.MIN_PARTIAL_BYTES:
            return

        # Hold the lock for the full snapshot + inference to prevent overlap
        async with self._transcribe_lock:
            audio = self._buf_as_float32()
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(
                self._executor, self._partial_transcribe_sync, audio
            )

        if not text:
            return

        await self.ws.send_json({"type": "transcript_partial", "text": text})

        translation = await self._incremental_translate(text)
        if translation:
            await self.ws.send_json({"type": "translation_partial", "text": translation})

            # Pre-synthesize if we hit sentence-terminal punctuation (Layer 3)
            if translation.rstrip()[-1:] in ".!?" and self._pre_synth_task is None:
                self._pre_synth_task = asyncio.create_task(
                    self._begin_pre_synthesis(translation)
                )

    def _partial_transcribe_sync(self, audio: np.ndarray) -> str:
        """Fast, approximate transcription — beam_size=1, VAD off."""
        try:
            segments, _ = self._asr._model.transcribe(
                audio,
                language=self._asr.language,
                beam_size=1,
                temperature=0.0,
                vad_filter=False,
                condition_on_previous_text=True,
                word_timestamps=False,
            )
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception:
            logger.exception("Partial transcription error")
            return ""

    # ── Incremental translation (Layer 2) ─────────────────────────────────

    async def _incremental_translate(self, transcript: str) -> Optional[str]:
        # Only translate if the last character is a word boundary
        if not transcript or transcript[-1] not in " .,!?;:":
            return None

        prefix = self._last_translated_prefix
        if transcript.startswith(prefix):
            new_words = transcript[len(prefix):].strip()
        else:
            # Transcript changed significantly — restart incremental state
            new_words = transcript
            self._last_translation_so_far = ""

        if not new_words:
            return None

        loop = asyncio.get_running_loop()
        new_part = await loop.run_in_executor(
            self._executor, self._translator._translate_sync, new_words
        )
        if not new_part:
            return None

        self._last_translated_prefix = transcript
        self._last_translation_so_far = (
            self._last_translation_so_far + (" " if self._last_translation_so_far else "") + new_part
        ).strip()
        return self._last_translation_so_far

    # ── Pre-synthesis (Layer 3) ────────────────────────────────────────────

    async def _begin_pre_synthesis(self, text: str) -> None:
        self._pre_synth_mp3 = await self._synthesise_mp3(text)

    # ── Finalization (silence boundary) ───────────────────────────────────

    async def _finalize_utterance(self) -> None:
        # Cancel and drain the partial-transcription heartbeat
        if self._partial_timer_task and not self._partial_timer_task.done():
            self._partial_timer_task.cancel()
            try:
                await self._partial_timer_task
            except asyncio.CancelledError:
                pass
        self._partial_timer_task = None

        if len(self._audio_buf) < self.MIN_FINAL_BYTES:
            self._audio_buf.clear()
            return

        # Snapshot and clear under the lock so no partial can race us
        async with self._transcribe_lock:
            audio = self._buf_as_float32()
            self._audio_buf.clear()

        # Reset incremental translation state for the next utterance
        self._last_translated_prefix = ""
        self._last_translation_so_far = ""

        import numpy as _np
        rms = float(_np.sqrt(_np.mean(audio ** 2)))
        logger.info("Final transcription: %.3f s audio, RMS=%.4f", len(audio) / 16000, rms)

        # Final transcription — beam_size=1, fast
        await self.ws.send_json({"type": "status", "message": "transcribing"})
        loop = asyncio.get_running_loop()
        logger.info("Transcribing %.3f s …", len(audio) / 16000)
        import time as _time
        _t0 = _time.perf_counter()
        transcript_obj = await loop.run_in_executor(
            self._executor, self._asr._transcribe_sync, audio
        )
        logger.info("Transcription done in %.1f s", _time.perf_counter() - _t0)
        if not transcript_obj or not transcript_obj.text.strip():
            logger.warning("Transcription returned empty for %.3f s audio (RMS=%.4f)", len(audio) / 16000, rms)
            return

        final_text = transcript_obj.text.strip()
        logger.info("Transcript: %r → sending to client", final_text)
        await self.ws.send_json({"type": "transcript_final", "text": final_text})

        # Full, clean translation
        translation = await loop.run_in_executor(
            self._executor, self._translator._translate_sync, final_text
        )
        if not translation:
            return

        await self.ws.send_json({"type": "translation_final", "text": translation})

        # Resolve pre-synthesized audio or synthesize fresh
        if self._pre_synth_task and not self._pre_synth_task.done():
            await self._pre_synth_task

        mp3_data = self._pre_synth_mp3
        self._pre_synth_task = None
        self._pre_synth_mp3 = None

        if mp3_data is None:
            mp3_data = await self._synthesise_mp3(translation)

        if mp3_data:
            await self.ws.send_json(
                {"type": "audio", "data": base64.b64encode(mp3_data).decode()}
            )

    # ── TTS synthesis (returns raw MP3 bytes) ─────────────────────────────

    async def _synthesise_mp3(self, text: str) -> Optional[bytes]:
        import edge_tts

        try:
            communicate = edge_tts.Communicate(text=text, voice=self._tts.voice)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks) if chunks else None
        except Exception:
            logger.exception("TTS synthesis error")
            return None

    # ── Firestore persistence ──────────────────────────────────────────────

    async def handle_save_message(self, data: dict) -> None:
        if not self.session_id:
            return

        def _save() -> None:
            (
                db.collection("users")
                .document(self.uid)
                .collection("sessions")
                .document(self.session_id)
                .collection("messages")
                .add(
                    {
                        "source_text": data.get("source_text", ""),
                        "translated_text": data.get("translated_text", ""),
                        "direction": data.get("direction", self.direction.value),
                        "timestamp": firestore.SERVER_TIMESTAMP,
                    }
                )
            )

        try:
            await asyncio.to_thread(_save)
        except Exception:
            logger.exception("Firestore save error")

    # ── Internal helpers ───────────────────────────────────────────────────

    def _buf_as_float32(self) -> np.ndarray:
        return np.frombuffer(bytes(self._audio_buf), dtype=np.int16).astype(np.float32) / 32768.0

    def cleanup(self) -> None:
        if self._partial_timer_task and not self._partial_timer_task.done():
            self._partial_timer_task.cancel()
        self._executor.shutdown(wait=False)


# ── WebSocket endpoint ─────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    pipeline: Optional[StreamingTranslationPipeline] = None
    uid: Optional[str] = None
    session_id: Optional[str] = None
    direction: Optional[Direction] = None

    try:
        # ── Init frame ──────────────────────────────────────────────────
        try:
            init = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
        except asyncio.TimeoutError:
            await ws.send_json({"type": "error", "message": "Init timeout"})
            return

        token = init.get("token")
        session_id = init.get("session_id")        # optional
        direction_str = init.get("direction", "de_to_en")

        if not token:
            await ws.send_json({"type": "error", "message": "Missing token"})
            await ws.close(code=1008)
            return

        try:
            uid = await _verify_token(token)
        except HTTPException:
            await ws.send_json({"type": "error", "message": "Unauthorized"})
            await ws.close(code=1008)
            return

        try:
            direction = Direction(direction_str)
        except ValueError:
            await ws.send_json({"type": "error", "message": f"Unknown direction: {direction_str}"})
            await ws.close(code=1008)
            return

        # direction is guaranteed now
        asr, translator, tts_mod = await _get_models(direction)  # type: ignore[arg-type]

        pipeline = StreamingTranslationPipeline(
            uid=uid,
            session_id=session_id,
            direction=direction,
            websocket=ws,
            asr=asr,
            translator=translator,
            tts=tts_mod,
        )
        logger.info("WS session started uid=%s dir=%s", uid, direction.value)

        # ── Frame loop ──────────────────────────────────────────────────
        while True:
            message = await ws.receive()
            msg_type = message.get("type")

            if msg_type == "websocket.disconnect":
                break

            frame_bytes: Optional[bytes] = message.get("bytes")
            frame_text: Optional[str] = message.get("text")

            if frame_bytes is not None:
                # Binary frame: PCM Int16 audio (or zero-length silence marker)
                await pipeline.handle_audio_frame(frame_bytes)

            elif frame_text:
                try:
                    data = json.loads(frame_text)
                    cmd = data.get("type")
                    if cmd == "save_message":
                        await pipeline.handle_save_message(data)
                    elif cmd == "stop":
                        # Client stopped recording — treat as silence boundary then exit
                        await pipeline.handle_audio_frame(b"")
                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error")
        try:
            await ws.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass
    finally:
        if pipeline:
            pipeline.cleanup()
        logger.info("WS session ended")

        # Generate a persistent summary once the client disconnects (session end).
        if uid and session_id and direction:
            asyncio.create_task(
                _generate_and_persist_session_summary(uid=uid, session_id=session_id, direction=direction)
            )


# ── REST — sessions ────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    title: str
    direction: str
    notes: str = ""


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None


@app.post("/sessions", status_code=201)
async def create_session(
    body: SessionCreate, uid: str = Depends(get_current_user)
):
    def _create() -> str:
        ref = (
            db.collection("users").document(uid).collection("sessions").document()
        )
        ref.set(
            {
                "title": body.title,
                "direction": body.direction,
                "notes": body.notes,
                "created_at": firestore.SERVER_TIMESTAMP,
                "last_active": firestore.SERVER_TIMESTAMP,
            }
        )
        return ref.id

    session_id = await asyncio.to_thread(_create)
    return {"id": session_id}


@app.get("/sessions")
async def list_sessions(uid: str = Depends(get_current_user)):
    def _list() -> list:
        docs = (
            db.collection("users")
            .document(uid)
            .collection("sessions")
            .order_by("last_active", direction=firestore.Query.DESCENDING)
            .stream()
        )
        return [{"id": d.id, **d.to_dict()} for d in docs]

    return await asyncio.to_thread(_list)


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, uid: str = Depends(get_current_user)):
    def _get() -> Optional[dict]:
        doc = (
            db.collection("users")
            .document(uid)
            .collection("sessions")
            .document(session_id)
            .get()
        )
        return {"id": doc.id, **doc.to_dict()} if doc.exists else None

    result = await asyncio.to_thread(_get)
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: SessionUpdate,
    uid: str = Depends(get_current_user),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    def _update() -> None:
        db.collection("users").document(uid).collection("sessions").document(
            session_id
        ).update(updates)

    await asyncio.to_thread(_update)
    return {"id": session_id, **updates}


@app.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, uid: str = Depends(get_current_user)):
    def _delete() -> None:
        sess_ref = (
            db.collection("users")
            .document(uid)
            .collection("sessions")
            .document(session_id)
        )
        for msg_doc in sess_ref.collection("messages").stream():
            msg_doc.reference.delete()
        sess_ref.delete()

    await asyncio.to_thread(_delete)


@app.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    limit: Optional[int] = None,
    uid: str = Depends(get_current_user),
):
    def _list() -> list:
        q = (
            db.collection("users")
            .document(uid)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .order_by("timestamp")
        )
        if limit is not None:
            q = q.limit(limit)
        docs = q.stream()
        return [{"id": d.id, **d.to_dict()} for d in docs]

    return await asyncio.to_thread(_list)


# ── Conversation analytics helpers ────────────────────────────────────────

_POS_WORDS = {
    "good","great","excellent","yes","agree","helpful","clear","understand","perfect",
    "thank","thanks","right","correct","sure","happy","glad","wonderful","nice",
    "absolutely","definitely","pleasure","appreciate","well","fine","success",
    "solved","resolved","satisfied","positive","confirm","confirmed","approved","accept",
}
_NEG_WORDS = {
    "no","problem","issue","difficult","bad","wrong","disagree","confused","sorry",
    "unfortunately","mistake","error","fail","failed","impossible","never","nothing",
    "disappointed","frustrated","concern","worried","trouble","unclear","misunderstanding",
    "deny","denied","reject","rejected",
}
_FORMAL_MARKERS = {
    "please","would","could","should","regarding","pursuant","therefore","however",
    "moreover","furthermore","accordingly","hence","thus","whereas","sincerely",
    "respectfully","kindly","hereby","aforementioned",
}
_INFORMAL_MARKERS = {
    "yeah","yep","nope","gonna","wanna","gotta","ok","okay","hey","hi","cool","awesome",
}


def _sentiment(texts: list[str]) -> tuple[str, float]:
    words = [w.strip(".,!?;:'\"") for w in " ".join(texts).lower().split()]
    pos = sum(1 for w in words if w in _POS_WORDS)
    neg = sum(1 for w in words if w in _NEG_WORDS)
    total = pos + neg or 1
    ratio = pos / total
    if ratio >= 0.60:
        return "Positive", round(ratio, 2)
    if ratio <= 0.40:
        return "Negative", round(1 - ratio, 2)
    return "Neutral", 0.50


def _formality(texts: list[str]) -> str:
    words = [w.strip(".,!?;:'\"") for w in " ".join(texts).lower().split()]
    formal   = sum(1 for w in words if w in _FORMAL_MARKERS)
    informal = sum(1 for w in words if w in _INFORMAL_MARKERS)
    sentences = [s.strip() for s in " ".join(texts).replace("!", ".").replace("?", ".").split(".") if s.strip()]
    avg_len = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)
    return "Formal" if (formal > informal or avg_len >= 12) else "Informal"


# ── REST — standalone summarize ────────────────────────────────────────────

class SummarizeMessage(BaseModel):
    sender:          str
    source_text:     str
    translated_text: str
    direction:       str = "de_to_en"
    timestamp:       Optional[str] = None


class SummarizeRequest(BaseModel):
    conversation_time: str
    duration_seconds:  Optional[int] = None
    output_language:   str = "English"
    messages:          list[SummarizeMessage]


@app.post("/summarize")
async def summarize_conversation(
    body: SummarizeRequest,
    uid: str = Depends(get_current_user),
):
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages list is empty")

    lines: list[str] = []
    for msg in body.messages:
        src = (msg.source_text or "").strip()
        tr  = (msg.translated_text or "").strip()
        if src: lines.append(f"{msg.sender}: {src}")
        if tr:  lines.append(f"(translation): {tr}")

    transcript = "\n".join(lines).strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="all messages are empty")

    try:
        async with _summary_generation_lock:
            module = await _get_summary_module()
            summary_text = await asyncio.to_thread(
                module.summarize, transcript, body.output_language
            )
    except Exception:
        logger.exception("POST /summarize — model error")
        raise HTTPException(status_code=500, detail="Summary generation failed")

    all_text = [m.source_text + " " + m.translated_text for m in body.messages]
    sentiment_label, sentiment_score = _sentiment(all_text)
    formality_label = _formality(all_text)
    total_words  = sum(len((m.source_text + " " + m.translated_text).split()) for m in body.messages)
    avg_words    = round(total_words / len(body.messages)) if body.messages else 0
    de_turns     = sum(1 for m in body.messages if m.direction == "de_to_en")
    en_turns     = sum(1 for m in body.messages if m.direction == "en_to_de")
    topic_sentences = [s.strip() for s in summary_text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    main_topic   = (topic_sentences[0] + ".") if topic_sentences else ""

    return {
        "summary":           summary_text,
        "message_count":     len(body.messages),
        "first_sender":      body.messages[0].sender,
        "conversation_time": body.conversation_time,
        "duration_seconds":  body.duration_seconds,
        "analysis": {
            "sentiment":          sentiment_label,
            "sentiment_score":    sentiment_score,
            "formality":          formality_label,
            "main_topic":         main_topic,
            "total_words":        total_words,
            "avg_words_per_turn": avg_words,
            "de_speaker_turns":   de_turns,
            "en_speaker_turns":   en_turns,
        },
    }
