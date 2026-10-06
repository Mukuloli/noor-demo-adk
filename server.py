"""FastAPI server for ADK chat — connects to frontend via Firebase auth."""
import json
import logging
import uuid
import asyncio
from time import perf_counter

from firebase_admin import auth as firebase_auth
from noor_database.firestore.client import get_firebase_app
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig, StreamingMode
from noor_database.adk_sessions import CachedAdkSessionService, agent_turn
from noor_database.adk_streaming import text_chunks
from noor_database.text_replies import quick_greeting, record_quick_reply
from google.genai import types as genai_types

from noor.agent import build_agent
from noor.config import settings
from noor.db import get_service, warm_user

# ─── Firebase init ────────────────────────────────────────────────────────────
# ─── ADK session store ────────────────────────────────────────────────────────
_session_service = CachedAdkSessionService(settings, get_service)
APP_NAME = 'noor-adk'
logger = logging.getLogger('uvicorn.error.noor_adk')

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
        return firebase_auth.verify_id_token(token, app=get_firebase_app(settings))
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


# ─── Helpers ──────────────────────────────────────────────────────────────────
async def _warm_profile(uid, name, email):
    started = perf_counter()
    try:
        return await asyncio.to_thread(warm_user, uid, name, email)
    finally:
        logger.info('ADK profile preparation seconds=%.3f', perf_counter() - started)


async def _ensure_session(uid: str, session_id: str, name: str = '', email: str = ''):
    """Ensure ADK session exists (carries conversation history)."""
    adk_session_id = f'{uid}-{session_id}'
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=uid, session_id=adk_session_id
    )
    warmup = None
    if session is None:
        warmup = asyncio.create_task(_warm_profile(uid, name, email))
        try:
            await _session_service.create_session(
                app_name=APP_NAME, user_id=uid, session_id=adk_session_id
            )
        except BaseException:
            # Join the verified identity task even when session creation fails.
            await asyncio.shield(warmup)
            raise
    return adk_session_id, warmup


async def _authenticate(authorization):
    started = perf_counter()
    claims = await asyncio.to_thread(verify_token, authorization)
    logger.info('ADK authentication seconds=%.3f', perf_counter() - started)
    return claims


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.get('/health')
def health():
    return {
        'ok': True,
        'model': settings.gemini_model,
        'booking_mode': settings.booking_mode,
        'service': 'noor-adk',
        'cache': _session_service.cache.status(),
    }


async def _reply_chunks(body, claims):
    """Share fast replies, identity preparation and durable history across routes."""
    uid = claims['uid']
    async with agent_turn(_session_service, app_name=APP_NAME, user_id=uid,
                          session_id=f'{uid}-{body.session_id}'):
        greeting = quick_greeting(body.message)
        if greeting:
            # No model, profile query or history read stands before this chunk.
            yield {'text': greeting, 'done': False}
        started = perf_counter()
        adk_session_id, warmup = await _ensure_session(uid, body.session_id,
                                                    claims.get('name', ''), claims.get('email', ''))
        logger.info('ADK session preparation seconds=%.3f', perf_counter() - started)
        try:
            if greeting:
                await record_quick_reply(_session_service, app_name=APP_NAME, uid=uid,
                                         session_id=adk_session_id, message=body.message, reply=greeting)
                return
            agent = build_agent(uid=uid, session_id=adk_session_id, profile_ready=warmup)
            runner = Runner(agent=agent, app_name=APP_NAME, session_service=_session_service)
            user_content = genai_types.Content(role='user', parts=[genai_types.Part(text=body.message)])
            async for chunk in text_chunks(runner.run_async(
                user_id=uid,
                session_id=adk_session_id,
                new_message=user_content,
                run_config=RunConfig(streaming_mode=StreamingMode.SSE),
            ), logger=logger):
                chunk['done'] = False
                yield chunk
        finally:
            if warmup is not None:
                await asyncio.shield(warmup)


@app.post('/chat', response_model=ChatResponse)
async def chat(body: ChatRequest, authorization: str = Header(default='')):
    """Chat with Noor (non-streaming fallback). Requires Firebase Bearer token."""
    started = perf_counter()
    claims = await _authenticate(authorization)
    if not settings.google_api_key:
        raise HTTPException(503, 'Chat is not configured yet. Please try again later.')
    try:
        reply_parts = [chunk['text'] async for chunk in _reply_chunks(body, claims)]
    except Exception as exc:
        logger.exception('ADK chat failed')
        raise HTTPException(503, 'Noor could not respond right now. Please try again.') from exc
    logger.info('ADK response total_seconds=%.3f', perf_counter() - started)
    return ChatResponse(reply=''.join(reply_parts).strip() or "I couldn't process that. Please try again.",
                        session_id=body.session_id)


@app.post('/chat/stream')
async def chat_stream(body: ChatRequest, authorization: str = Header(default='')):
    """Chat with Noor via SSE streaming. Text chunks arrive as they are generated."""
    started = perf_counter()
    claims = await _authenticate(authorization)
    if not settings.google_api_key:
        raise HTTPException(503, 'Chat is not configured yet. Please try again later.')

    async def event_generator():
        first_text = False
        try:
            async for chunk in _reply_chunks(body, claims):
                if chunk.get('text') and not first_text:
                    logger.info('ADK response first_text_seconds=%.3f', perf_counter() - started)
                    first_text = True
                yield f'data: {json.dumps(chunk)}\n\n'
            # Completion is sent only after durable history is saved.
            yield f'data: {json.dumps({"text": "", "done": True})}\n\n'
            logger.info('ADK response total_seconds=%.3f', perf_counter() - started)
        except Exception:
            logger.exception('ADK stream failed')
            error = json.dumps({'error': 'Noor could not respond right now. Please try again.', 'done': True})
            yield f'data: {error}\n\n'

    return StreamingResponse(event_generator(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
