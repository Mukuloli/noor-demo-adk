# Noor ADK — Google ADK Dental Booking Agent

Standalone text-based booking agent using **Google ADK** + **Firestore** (same DB as the voice bot).

Confirmed bookings save a booking name and phone separately from Google identity. Returning users and rescheduling requests reuse that contact: only a missing date/time is asked, and an explicitly selected available slot can be confirmed directly. First bookings, changed contact and cancellations still require confirmation. Hospital services, hours and locations come from the configured clinic; missing information is referred to reception. Patient-facing replies and confirmation messages use names and appointment times instead of internal IDs.

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

Set `REDIS_URL` to the same value as the backend to share cached profiles, bookings and session context. ADK history is cached during generation and saved to Firestore once per turn. See the backend's `database/schemas/redis.md` for Redis setup and `python scripts/check_cache.py` to verify your connection. Redis is optional; missing entries or outages fall back to Firestore.

Authenticated chat loads the verified profile when a conversation is created. Appointment lists are fetched only when a tool needs them, so a greeting does not wait for a bookings query. New conversation history is saved once after the turn rather than writing an empty snapshot before generation. Browser replies use SSE chunks and completion events, with proxy buffering disabled.

Terminal chat also warms the user's existing profile at startup and uses the shared Redis-first history service. Its displayed patient name does not overwrite the stored profile. Each completed or interrupted turn saves a durable Firestore history snapshot.

Text chat uses low Gemini thinking, supplies clinic time directly, and calls database tools only when needed. Blocking tools run in threads; terminal and browser replies stream as generated. History is loaded once per turn and published to Redis once after the durable save. The server logs authentication, preparation, and generation timings without message content. Run `python scripts/check_chat_latency.py --compare` to measure a generic greeting with default versus low thinking; this uses Gemini but no patient data or live database.

## What It Can Do

| Feature | Status |
|---------|--------|
| Check availability | ✅ |
| Book appointment | ✅ Firestore |
| Confirm/Cancel booking | ✅ Firestore |
| View appointments | ✅ Firestore |
| Hindi/Hinglish | ✅ |
| Same DB as voice bot | ✅ |
