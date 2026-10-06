# Noor ADK — Google ADK Dental Booking Agent

Standalone text-based booking agent using **Google ADK** + **Firestore** (same DB as the voice bot).

Confirmed bookings save a booking name and phone separately from Google identity. Returning users and rescheduling requests reuse that contact. New repeat bookings can confirm an explicitly selected available slot directly. Cancellation and rescheduling always collect a reason and require confirmation in a later user turn before applying the change. Confirmed changes request a Google Calendar email notification; rescheduling updates the existing event and cancellation marks it cancelled. First bookings and changed contact also require confirmation. Hospital services, hours and locations come from the configured clinic; missing information is referred to reception. Patient-facing replies and confirmation messages use names and appointment times instead of internal IDs.

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

Text chat defaults to `gemini-3.1-flash-lite`. Verified profile preparation runs alongside generation for a new conversation; profile-dependent booking tools wait for it before using patient data. Exact greetings such as "hi" stream immediately without a model or database read, then join profile preparation and save their conversation history before completion. Requests containing appointment actions always reach the booking agent. Appointment lists are fetched only when needed. Configured public hospital facts and doctor details are supplied directly to the agent, avoiding an extra tool/model round trip. Transport connections are reused across turns while patient tools and sessions remain separate. Browser replies use SSE chunks and completion events, with proxy buffering disabled.

Terminal chat also warms the user's existing profile at startup and uses the shared Redis-first history service. Its displayed patient name does not overwrite the stored profile. Each completed or interrupted turn saves a durable Firestore history snapshot.

Text chat uses low Gemini thinking, supplies clinic time directly, and calls database tools only when needed. Blocking tools run in threads; terminal and browser replies stream as generated. History is loaded once per turn and published to Redis once after the durable save. The server logs authentication, preparation, and generation timings without message content. Run `python scripts/check_chat_latency.py --compare` to measure a generic greeting with default versus low thinking; this uses Gemini but no patient data or live database.

Compare models with `python scripts/check_chat_latency.py --models gemini-3.8-flash gemini-3.1-flash-lite --runs 2 --hospital`. Run `python scripts/check_booking_model.py --models gemini-3.1-flash-lite` to exercise saved-contact repeat booking and the reason/confirmation steps for cancellation and rescheduling using synthetic records in memory. Neither diagnostic accesses patient data or sends emails.

## What It Can Do

| Feature | Status |
|---------|--------|
| Check availability | ✅ |
| Book appointment | ✅ Firestore |
| Confirm/Cancel booking | ✅ Firestore |
| View appointments | ✅ Firestore |
| Hindi/Hinglish | ✅ |
| Same DB as voice bot | ✅ |
