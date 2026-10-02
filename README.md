# Noor ADK — Google ADK Dental Booking Agent

Standalone text-based booking agent using **Google ADK** + **Firestore** (same DB as the voice bot).

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

## What It Can Do

| Feature | Status |
|---------|--------|
| Check availability | ✅ |
| Book appointment | ✅ Firestore |
| Confirm/Cancel booking | ✅ Firestore |
| View appointments | ✅ Firestore |
| Hindi/Hinglish | ✅ |
| Same DB as voice bot | ✅ |
