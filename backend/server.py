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
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

# ── Path setup so existing backend modules are importable ──────────────────
sys.path.insert(0, str(Path(__file__).parent))

# ── Load .env before any Firebase / config imports ────────────────────────
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import firebase_admin
from firebase_admin import auth as fb_auth
from firebase_admin import credentials, firestore

from config import ASRConfig, Direction, TranslationConfig, TTSConfig
from asr_module import ASRModule
from translation_module import TranslationModule
from tts_module import TTSModule

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
_cred_path = os.getenv("FIREBASE_CREDENTIALS", "backend/firebase-credentials.json")
firebase_admin.initialize_app(credentials.Certificate(_cred_path))
db = firestore.client()

# ── FastAPI app ────────────────────────────────────────────────────────────
app = FastAPI(title="HitlerTranslator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        compute_type = "float16" if device == "cuda" else "int8"
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
    SAMPLE_RATE: int = 16_000
    BYTES_PER_SAMPLE: int = 2                                  # Int16
    MIN_PARTIAL_BYTES: int = int(SAMPLE_RATE * 0.4 * BYTES_PER_SAMPLE)  # 400 ms
    MIN_FINAL_BYTES: int = int(SAMPLE_RATE * 0.3 * BYTES_PER_SAMPLE)    # 300 ms

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
            await self._finalize_utterance()
            return

        self._audio_buf.extend(frame)

        # Start the partial-transcription heartbeat if it isn't running
        if self._partial_timer_task is None or self._partial_timer_task.done():
            self._partial_timer_task = asyncio.create_task(self._partial_timer_loop())

    # ── Partial transcription loop (Layer 1 + 2) ──────────────────────────

    async def _partial_timer_loop(self) -> None:
        while True:
            await asyncio.sleep(self.PARTIAL_INTERVAL_S)
            await self._run_partial()

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

        # Final transcription — beam_size=5, full quality
        loop = asyncio.get_running_loop()
        transcript_obj = await loop.run_in_executor(
            self._executor, self._asr._transcribe_sync, audio
        )
        if not transcript_obj or not transcript_obj.text.strip():
            return

        final_text = transcript_obj.text.strip()
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

        asr, translator, tts_mod = await _get_models(direction)

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
async def list_messages(session_id: str, uid: str = Depends(get_current_user)):
    def _list() -> list:
        docs = (
            db.collection("users")
            .document(uid)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .order_by("timestamp")
            .stream()
        )
        return [{"id": d.id, **d.to_dict()} for d in docs]

    return await asyncio.to_thread(_list)
