"""FastAPI backend — German ↔ English web translator.

WebSocket protocol (client → server)
--------------------------------------
  JSON  {"token":"…","session_id":"…","direction":"de_to_en"|"en_to_de"}  ← init frame
  BIN   Int16-LE PCM at 16 kHz   (zero-length = silence marker from worklet)
  JSON  {"type":"save_message","source_text":"…","translated_text":"…","direction":"…"}
  JSON  {"type":"stop"}

Server → client
---------------
  JSON  {"type":"status","message":"ready"}
  JSON  {"type":"transcript_final","text":"…","lang":"de"|"en"}
  JSON  {"type":"translation_final","text":"…","lang":"en"|"de"}
  JSON  {"type":"audio","data":"<base64-mp3>"}
  JSON  {"type":"done"}

REST endpoints
--------------
  POST   /sessions                    Create session
  GET    /sessions                    List user sessions (newest first)
  GET    /sessions/{id}               Get session  (triggers summary if status is null)
  DELETE /sessions/{id}               Delete session + messages
  GET    /sessions/{id}/messages      List transcript messages
  POST   /sessions/{id}/summary       Generate summary (idempotent if already done)
                                      Pass ?force=true to regenerate even if done.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials, firestore
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.websockets import WebSocketState

sys.path.insert(0, str(Path(__file__).parent))
from config import Direction, SummaryConfig, TranslationConfig, TTSConfig
from translation_module import TranslationModule

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("backend")

# ── Firebase / Firestore ──────────────────────────────────────────────────────
# try/except guards against the "app already exists" error when uvicorn
# reimports this module on --reload.

_CREDS_PATH = Path(__file__).parent / "firebase-credentials.json"
try:
    _fb_app = firebase_admin.initialize_app(credentials.Certificate(str(_CREDS_PATH)))
except ValueError:
    _fb_app = firebase_admin.get_app()
_db = firestore.client()

# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="German ↔ English Translator API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Translation model state ───────────────────────────────────────────────────

_whisper = None
_whisper_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
_tr_exec     = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marian")
_translator_de_en: Optional[TranslationModule] = None
_translator_en_de: Optional[TranslationModule] = None

# ── Summary model (lazy singleton, loaded on first request) ───────────────────

_summary_lock   = threading.Lock()
_summary_module = None
_summary_exec   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="summary")


def _get_summary_module():
    global _summary_module
    if _summary_module is None:
        with _summary_lock:
            if _summary_module is None:
                from summary_module import SummaryModule
                m = SummaryModule(SummaryConfig())
                m.load()
                _summary_module = m
    return _summary_module


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


# ── Firestore helpers ─────────────────────────────────────────────────────────

def _session_ref(uid: str, session_id: str):
    return _db.collection("users").document(uid).collection("sessions").document(session_id)


def _serialize(obj):
    """Recursively convert Firestore Timestamps to {_seconds, _nanoseconds}."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if hasattr(obj, "seconds") and hasattr(obj, "nanoseconds"):
        return {"_seconds": obj.seconds, "_nanoseconds": obj.nanoseconds}
    return obj


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _verify_token(token: str) -> str:
    return firebase_auth.verify_id_token(token)["uid"]


def _require_auth(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        return _verify_token(header[7:])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Audio buffer with energy-based VAD ───────────────────────────────────────

class AudioBuffer:
    def __init__(
        self,
        sample_rate: int = 16_000,
        rms_threshold: float = 0.018,
        end_silence_ms: int = 700,
        min_speech_ms: int = 250,
        max_s: float = 15.0,
    ) -> None:
        self._sr      = sample_rate
        self._rms     = rms_threshold
        self._end_sil = int(end_silence_ms * sample_rate / 1_000)
        self._min_spe = int(min_speech_ms  * sample_rate / 1_000)
        self._max_sam = int(max_s * sample_rate)
        self._buf: list[np.ndarray] = []
        self._sil_acc = self._spe_acc = 0
        self._in_speech = False

    def add(self, chunk: np.ndarray) -> Optional[np.ndarray]:
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        self._buf.append(chunk)
        if rms > self._rms:
            self._in_speech = True
            self._spe_acc  += len(chunk)
            self._sil_acc   = 0
        elif self._in_speech:
            self._sil_acc += len(chunk)
        total = sum(len(b) for b in self._buf)
        if self._in_speech and (self._sil_acc >= self._end_sil or total >= self._max_sam):
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
        audio, language=lang, beam_size=5, temperature=0.0,
        vad_filter=True, vad_parameters={"threshold": 0.5, "min_silence_duration_ms": 500},
    )
    return " ".join(s.text.strip() for s in segs)


async def _transcribe(audio: np.ndarray, lang: str) -> str:
    return await asyncio.get_running_loop().run_in_executor(
        _whisper_exec, _transcribe_sync, audio, lang
    )


async def _translate(text: str, direction: Direction) -> Optional[str]:
    tr = _translator_de_en if direction == Direction.DE_TO_EN else _translator_en_de
    return await asyncio.get_running_loop().run_in_executor(
        _tr_exec, tr._translate_sync, text
    )


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
        np.arange(len(audio)), audio,
    ).astype(np.float32)


# ── Summary generation (runs in _summary_exec thread) ────────────────────────

def _run_summary(session_id: str, uid: str, direction_value: str) -> None:
    """
    Fetch messages from Firestore, generate a summary with the local model,
    and write it back.  Idempotent: skips silently if status is already 'done'.
    """
    sref = _session_ref(uid, session_id)
    try:
        doc = sref.get()
        if not doc.exists:
            logger.warning("Summary: session %s not found", session_id)
            return
        if doc.to_dict().get("summary_status") == "done":
            logger.info("Summary: session %s already done, skipping", session_id)
            return

        # Fetch messages; fall back to unordered if the index is missing.
        try:
            msgs = sref.collection("messages").order_by("created_at").get()
        except Exception:
            msgs = sref.collection("messages").get()

        lines: list[str] = []
        for m in msgs:
            md = m.to_dict()
            src = (md.get("source_text") or "").strip()
            tr  = (md.get("translated_text") or "").strip()
            if src: lines.append(f"Original: {src}")
            if tr:  lines.append(f"Translation: {tr}")

        transcript = "\n".join(lines)
        if not transcript.strip():
            sref.update({"summary_status": "error"})
            return

        lang = "English" if direction_value == "de_to_en" else "German"
        logger.info("Summary: generating for session %s (%d chars)", session_id, len(transcript))
        text = _get_summary_module().summarize(transcript, lang)
        sref.update({"summary": text, "summary_status": "done"})
        logger.info("Summary: saved for session %s", session_id)

    except Exception:
        logger.exception("Summary failed for session %s", session_id)
        try:
            sref.update({"summary_status": "error"})
        except Exception:
            pass


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    direction   = Direction.DE_TO_EN
    buf         = AudioBuffer()
    tts_cfg     = TTSConfig()
    session_id: Optional[str] = None
    uid:        Optional[str] = None
    initialized = False

    def _ws_open() -> bool:
        return ws.client_state == WebSocketState.CONNECTED

    async def _send(payload: dict) -> bool:
        if not _ws_open():
            return False
        try:
            await ws.send_json(payload)
            return True
        except RuntimeError:
            return False

    async def process(audio: np.ndarray) -> None:
        src_lang   = direction.src_lang
        transcript = await _transcribe(audio, src_lang)
        if not transcript.strip():
            return
        if not await _send({"type": "transcript_final", "text": transcript, "lang": src_lang}):
            return
        translation = await _translate(transcript, direction)
        if not translation:
            return
        tgt_lang = direction.tgt_lang
        if not await _send({"type": "translation_final", "text": translation, "lang": tgt_lang}):
            return
        if direction == Direction.EN_TO_DE:
            mp3 = await _tts_mp3(translation, tts_cfg.de_voice)
            if mp3:
                await _send({"type": "audio", "data": base64.b64encode(mp3).decode()})

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
                break

            if "text" in msg:
                data = json.loads(msg["text"])
                kind = data.get("type")

                if not initialized:
                    # First JSON frame: {token, session_id, direction}
                    token      = data.get("token")
                    session_id = data.get("session_id") or None
                    direction  = Direction(data.get("direction", "de_to_en"))
                    buf        = AudioBuffer()
                    initialized = True
                    if token:
                        try:
                            uid = _verify_token(token)
                        except Exception:
                            await _send({"type": "error", "message": "invalid token"})
                            break
                    await _send({"type": "status", "message": "ready"})

                elif kind == "save_message":
                    if session_id and uid:
                        try:
                            sref = _session_ref(uid, session_id)
                            sref.collection("messages").add({
                                "source_text":     data.get("source_text", ""),
                                "translated_text": data.get("translated_text", ""),
                                "direction":       data.get("direction", direction.value),
                                "created_at":      firestore.SERVER_TIMESTAMP,
                            })
                            sref.update({"last_active": firestore.SERVER_TIMESTAMP})
                        except Exception:
                            logger.exception("Failed to save message")

                elif kind == "stop":
                    utt = buf.flush()
                    if utt is not None:
                        _spawn(utt)
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    await _send({"type": "done"})
                    break

            elif "bytes" in msg:
                if not initialized:
                    continue
                raw = msg["bytes"]
                if len(raw) == 0:
                    utt = buf.flush()
                    if utt is not None:
                        _spawn(utt)
                    continue
                audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                utt = buf.add(audio)
                if utt is not None:
                    _spawn(utt)

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception:
        logger.exception("WebSocket session error")


# ── REST endpoints ────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    title:     str = ""
    direction: str = "de_to_en"
    notes:     str = ""


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
def summarize_conversation(body: SummarizeRequest, request: Request):
    """
    Standalone summary endpoint — no session required.
    Accepts a structured conversation payload and returns a plain-text summary.
    Runs synchronously (FastAPI executes sync endpoints in a thread pool).
    """
    _require_auth(request)

    if not body.messages:
        raise HTTPException(status_code=400, detail="messages list is empty")

    lines: list[str] = []
    for msg in body.messages:
        src = (msg.source_text or "").strip()
        tr  = (msg.translated_text or "").strip()
        if src: lines.append(f"{msg.sender}: {src}")
        if tr:  lines.append(f"(translation): {tr}")

    transcript = "\n".join(lines)
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="all messages are empty")

    try:
        summary_text = _get_summary_module().summarize(transcript, body.output_language)
    except Exception:
        logger.exception("POST /summarize — model error")
        raise HTTPException(status_code=500, detail="Summary generation failed")

    return {
        "summary":           summary_text,
        "message_count":     len(body.messages),
        "first_sender":      body.messages[0].sender,
        "conversation_time": body.conversation_time,
        "duration_seconds":  body.duration_seconds,
    }


@app.post("/sessions")
def create_session(body: SessionCreate, request: Request):
    uid     = _require_auth(request)
    doc_ref = _db.collection("users").document(uid).collection("sessions").document()
    doc_ref.set({
        "title":          body.title,
        "direction":      body.direction,
        "notes":          body.notes,
        "summary":        None,
        "summary_status": None,
        "created_at":     firestore.SERVER_TIMESTAMP,
        "last_active":    firestore.SERVER_TIMESTAMP,
    })
    return {"id": doc_ref.id, **body.model_dump()}


@app.get("/sessions")
def list_sessions(request: Request):
    uid  = _require_auth(request)
    docs = (
        _db.collection("users").document(uid).collection("sessions")
        .order_by("last_active", direction=firestore.Query.DESCENDING)
        .limit(100).get()
    )
    result = []
    for d in docs:
        data = d.to_dict()
        data["id"] = d.id
        result.append(_serialize(data))
    return result


@app.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request):
    uid  = _require_auth(request)
    doc  = _session_ref(uid, session_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Not found")
    data       = doc.to_dict()
    data["id"] = doc.id
    return _serialize(data)


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, request: Request):
    uid  = _require_auth(request)
    sref = _session_ref(uid, session_id)
    for m in sref.collection("messages").list_documents():
        m.delete()
    sref.delete()


@app.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, request: Request, limit: int = 200):
    uid  = _require_auth(request)
    sref = _session_ref(uid, session_id)
    if not sref.get().exists:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        msgs = sref.collection("messages").order_by("created_at").limit(limit).get()
    except Exception:
        msgs = sref.collection("messages").limit(limit).get()
    result = []
    for m in msgs:
        md       = m.to_dict()
        md["id"] = m.id
        result.append(_serialize(md))
    return result


@app.post("/sessions/{session_id}/summary")
def request_summary(session_id: str, request: Request, force: bool = False):
    """
    Trigger summary generation for a session.
    - If summary_status is already 'done' and force is False, return immediately.
    - If force is True (user clicked Refresh), reset status and regenerate.
    """
    uid  = _require_auth(request)
    logger.info("request_summary: uid=%s session_id=%s force=%s", uid, session_id, force)
    sref = _session_ref(uid, session_id)
    doc  = sref.get()

    if not doc.exists:
        # Session document missing — check whether messages exist (orphaned subcollection).
        # This happens when the session doc was never persisted (e.g. creation race) but
        # the WebSocket already saved messages under it.
        try:
            msgs = list(sref.collection("messages").limit(1).get())
        except Exception:
            msgs = []
        if not msgs:
            logger.warning("request_summary: session %s not found for uid %s", session_id, uid)
            raise HTTPException(status_code=404, detail="Session not found")
        # Recreate the session document so subsequent reads work normally.
        logger.warning("request_summary: recreating missing session doc %s", session_id)
        sref.set({
            "title": "",
            "direction": "de_to_en",
            "notes": "",
            "summary": None,
            "summary_status": None,
            "created_at": firestore.SERVER_TIMESTAMP,
            "last_active": firestore.SERVER_TIMESTAMP,
        })
        data: dict = {}
    else:
        data = doc.to_dict()

    if data.get("summary_status") == "done" and not force:
        return {"summary_status": "done", "summary": data.get("summary")}

    # Mark pending and submit background job
    sref.update({"summary": None, "summary_status": "pending"})
    direction = data.get("direction", "de_to_en")
    _summary_exec.submit(_run_summary, session_id, uid, direction)
    return {"summary_status": "pending"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "models_loaded": _whisper is not None}


# Serve built frontend in production
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
