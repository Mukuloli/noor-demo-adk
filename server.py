"""FastAPI server for ADK chat — connects to frontend via Firebase auth."""
import os
import uuid

from dotenv import load_dotenv
load_dotenv()
os.environ.setdefault('GOOGLE_API_KEY', os.getenv('GOOGLE_API_KEY', ''))

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from noor.agent import build_agent
from noor.config import settings

# ─── Firebase init ────────────────────────────────────────────────────────────
if not firebase_admin._apps:
    cred = credentials.Certificate(settings.google_application_credentials)
    firebase_admin.initialize_app(cred)

# ─── ADK session store ────────────────────────────────────────────────────────
_session_service = InMemorySessionService()
APP_NAME = 'noor-adk'

# ─── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(title='Noor ADK Chat', version='1.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:3000', 'http://127.0.0.1:3000'],
    allow_methods=['*'],
    allow_headers=['*'],
)


# ─── Auth helper ──────────────────────────────────────────────────────────────
def verify_token(authorization: str) -> dict:
    """Verify Firebase ID token and return decoded claims."""
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Missing or invalid Authorization header.')
    token = authorization.split(' ', 1)[1]
    try:
        return firebase_auth.verify_id_token(token)
    except Exception as exc:
        raise HTTPException(401, f'Invalid Firebase token: {exc}') from exc


# ─── Models ───────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    ok: bool = True
    reply: str
    session_id: str


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.get('/health')
def health():
    return {
        'ok': True,
        'model': settings.gemini_model,
        'booking_mode': settings.booking_mode,
        'service': 'noor-adk',
    }


@app.post('/chat', response_model=ChatResponse)
async def chat(body: ChatRequest, authorization: str = Header(...)):
    """Chat with Noor. Requires Firebase Bearer token from frontend."""
    claims = verify_token(authorization)
    uid = claims['uid']

    # Ensure ADK session exists (carries conversation history)
    adk_session_id = f'{uid}-{body.session_id}'
    try:
        await _session_service.get_session(
            app_name=APP_NAME, user_id=uid, session_id=adk_session_id
        )
    except Exception:
        await _session_service.create_session(
            app_name=APP_NAME, user_id=uid, session_id=adk_session_id
        )

    agent = build_agent(uid=uid, session_id=body.session_id)
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=_session_service)

    user_content = genai_types.Content(
        role='user',
        parts=[genai_types.Part(text=body.message)],
    )

    reply_parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id=uid,
            session_id=adk_session_id,
            new_message=user_content,
        ):
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        reply_parts.append(part.text)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f'Agent error: {exc}') from exc

    return ChatResponse(
        reply=' '.join(reply_parts).strip() or "I couldn't process that. Please try again.",
        session_id=body.session_id,
    )
