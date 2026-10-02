"""FastAPI server for ADK chat — connects to frontend via Firebase auth."""
import logging
import uuid

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
# ─── ADK session store ────────────────────────────────────────────────────────
_session_service = InMemorySessionService()
APP_NAME = 'noor-adk'
logger = logging.getLogger(__name__)

# ─── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(title='Noor ADK Chat', version='1.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.frontend_origins.split(',') if origin.strip()],
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
        if not firebase_admin._apps:
            cred = (credentials.Certificate(settings.google_application_credentials)
                    if settings.google_application_credentials else credentials.ApplicationDefault())
            firebase_admin.initialize_app(cred, {'projectId': settings.firebase_project_id})
        return firebase_auth.verify_id_token(token)
    except Exception as exc:
        raise HTTPException(401, 'Your sign-in has expired. Please sign in again.') from exc


# ─── Models ───────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=100)
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
async def chat(body: ChatRequest, authorization: str = Header(default='')):
    """Chat with Noor. Requires Firebase Bearer token from frontend."""
    claims = verify_token(authorization)
    uid = claims['uid']
    if not settings.google_api_key:
        raise HTTPException(503, 'Chat is not configured yet. Please try again later.')

    # Ensure ADK session exists (carries conversation history)
    adk_session_id = f'{uid}-{body.session_id}'
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=uid, session_id=adk_session_id
    )
    if session is None:
        await _session_service.create_session(
            app_name=APP_NAME, user_id=uid, session_id=adk_session_id
        )

    agent = build_agent(uid=uid, session_id=adk_session_id)
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
        logger.exception('ADK chat failed')
        raise HTTPException(503, 'Noor could not respond right now. Please try again.') from exc

    return ChatResponse(
        reply=' '.join(reply_parts).strip() or "I couldn't process that. Please try again.",
        session_id=body.session_id,
    )
