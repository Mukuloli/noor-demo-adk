# Noor ADK — Google ADK Dental Booking Agent

Standalone text-based booking agent using **Google ADK** + **Firestore** (same DB as the voice bot).

The database implementation and schema live in the backend repository's shared `database/` folder. Clone `Mukuloli/livkit-audio-demo-hospital` into `../livkit/backend` before installing requirements; ADK installs `../livkit/backend/database`. `noor/db.py` adapts ADK tool calls to the common Firestore booking service. Voice and chat share transactional slot reservations.

## Folder Structure

```
noor-adk/
├── main.py          ← Terminal chat (run this)
├── server.py        ← Optional FastAPI server (POST /chat)
├── .env             ← API keys & config
├── requirements.txt
└── noor/
    ├── agent.py     ← Google ADK LlmAgent with all tools
    ├── db.py        ← Firestore helpers (same collections)
    └── config.py    ← Settings from .env
```

## Setup

```powershell
cd d:\demo\noor-adk

# Create venv
python -m venv .venv
.\.venv\Scripts\activate

# Install
pip install -r requirements.txt
pip install pydantic-settings  # needed by config.py
```

## Run — Terminal Chat

```powershell
python main.py
# or with specific patient:
python main.py <firebase-uid> "Patient Name"
```

## Run — API Server (optional)

```powershell
uvicorn server:app --reload --port 8001
# Then POST http://127.0.0.1:8001/chat
```

On Windows, `powershell -ExecutionPolicy Bypass -File .\start.ps1` starts the chat server on port 8001. The sibling `livkit/start.ps1` launcher also starts it automatically when `noor-adk/.venv` exists.

Configure `GOOGLE_API_KEY`, `GEMINI_MODEL`, `FIREBASE_PROJECT_ID`, and `GOOGLE_APPLICATION_CREDENTIALS` in your local `.env`. Use the same Firebase project and Firestore database as the voice backend. The frontend uses `NEXT_PUBLIC_ADK_URL=http://127.0.0.1:8001` and sends the signed-in patient's Firebase token. For another frontend origin, set `FRONTEND_ORIGINS` to a comma-separated list in the chat server's `.env`.

Environment files and credentials are excluded from Git. Settings load from this repository's `.env` regardless of the working directory.

## What It Can Do

| Feature | Status |
|---------|--------|
| Check availability | ✅ |
| Book appointment | ✅ Firestore |
| Confirm/Cancel booking | ✅ Firestore |
| View appointments | ✅ Firestore |
| Hindi/Hinglish | ✅ |
| Same DB as voice bot | ✅ |
