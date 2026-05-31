# German ↔ English Real-Time Translator

Real-time speech translation between German and English using Whisper (ASR), MarianMT (NMT), and Edge TTS.

## How it works

- **ASR**: faster-whisper (`large-v3`) transcribes microphone audio
- **Translation**: HuggingFace MarianMT models (`Helsinki-NLP/opus-mt-de-en` / `en-de`)
- **TTS**: Microsoft Edge Neural TTS reads German output aloud
- **Frontend**: React + Vite, communicates over WebSocket

---

## Prerequisites

- Python 3.10+
- Node.js 18+
- **ffmpeg** (required for audio decoding)
  - macOS: `brew install ffmpeg`
  - Ubuntu: `sudo apt install ffmpeg`
  - Windows: `winget install --id Gyan.FFmpeg`
- **PortAudio** (required by sounddevice)
  - macOS: `brew install portaudio`
  - Ubuntu: `sudo apt install portaudio19-dev`
  - Windows: usually bundled automatically

---

## Setup

### 1. Backend

```bash
cd backend
python -m venv venv

# macOS/Linux
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt

# Download spaCy language models (one-time)
python -m spacy download de_core_news_sm
python -m spacy download en_core_web_sm
```

> **GPU (optional):** For CUDA 12.x, replace the torch install:
> ```bash
> pip install torch>=2.1.0 --index-url https://download.pytorch.org/whl/cu121
> ```

### 2. Frontend

```bash
cd frontend
npm install
```

---

## Running

### Option A — Windows one-click

Double-click **`start.bat`** in the project root. It creates the venv, installs deps, and opens both services in separate terminal windows, then opens the browser.

### Option B — Manual (macOS/Linux/Windows)

**Terminal 1 — Backend**
```bash
cd backend
source venv/bin/activate   # Windows: venv\Scripts\activate
uvicorn server:app --reload --port 8000
```

**Terminal 2 — Frontend**
```bash
cd frontend
npm run dev
```

Then open **http://localhost:5173** in your browser.

---

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Check if backend is up and models are loaded |
| `WS /ws` | WebSocket for real-time audio streaming |

---

## First-run notes

- On first startup the backend downloads Whisper `large-v3` (~3 GB) and MarianMT models (~300 MB each) into the HuggingFace cache (`~/.cache/huggingface/`).
- Model loading takes 30–60 seconds; watch the backend logs for `All models ready`.
- CPU inference works but is slow; a CUDA GPU is recommended for real-time use.
