"""Terminal chat loop — run with: python main.py"""
import asyncio
import os
import uuid
import sys

from dotenv import load_dotenv
load_dotenv()

# Set GOOGLE_API_KEY for google-genai
os.environ.setdefault('GOOGLE_API_KEY', os.getenv('GOOGLE_API_KEY', ''))

from google.adk.runners import Runner
from noor_database.adk_sessions import CachedAdkSessionService, agent_turn
from google.genai import types as genai_types

from noor.agent import build_agent
from noor.config import settings
from noor.db import get_service, warm_user


BANNER = """
╔══════════════════════════════════════════╗
║   Noor — Dental Appointment Assistant   ║
║   Powered by Google ADK + Firestore     ║
╚══════════════════════════════════════════╝
Type your message and press Enter.
Type 'quit' or 'exit' to leave.
"""


async def run_chat(uid: str, patient_name: str):
    session_service = CachedAdkSessionService(settings, get_service)
    session_id = str(uuid.uuid4())
    app_name = 'noor-adk'

    await asyncio.to_thread(warm_user, uid)
    await session_service.create_session(
        app_name=app_name, user_id=uid, session_id=session_id
    )

    print(BANNER)
    print(f"Patient: {patient_name}  (uid: {uid})\n")

    # First greeting
    print("Noor: Hi! I'm Noor 👋 I can help you book, view, or cancel dental appointments. How can I help you today?\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nNoor: Goodbye! Take care 😊")
            break

        if not user_input:
            continue
        if user_input.lower() in {'quit', 'exit', 'bye', 'goodbye'}:
            print("Noor: Goodbye! Take care 😊")
            break

        # Build a fresh agent each turn (tools re-bound to same uid/session_id)
        agent = build_agent(uid=uid, session_id=session_id)
        runner = Runner(
            agent=agent,
            app_name=app_name,
            session_service=session_service,
        )

        user_content = genai_types.Content(
            role='user',
            parts=[genai_types.Part(text=user_input)],
        )

        print("Noor: ", end='', flush=True)
        reply_parts = []
        try:
            async with agent_turn(session_service, app_name=app_name, user_id=uid,
                                  session_id=session_id):
                async for event in runner.run_async(
                    user_id=uid,
                    session_id=session_id,
                    new_message=user_content,
                ):
                    if event.is_final_response() and event.content and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, 'text') and part.text:
                                reply_parts.append(part.text)
        except Exception as exc:
            print(f"\n[Error: {exc}]")
            continue

        reply = ' '.join(reply_parts).strip()
        print(reply if reply else "(no response)")
        print()


def main():
    uid = settings.test_patient_uid
    name = settings.test_patient_name

    # Allow: python main.py <uid> <name>
    if len(sys.argv) >= 3:
        uid = sys.argv[1]
        name = sys.argv[2]
    elif len(sys.argv) == 2:
        uid = sys.argv[1]

    asyncio.run(run_chat(uid, name))


if __name__ == '__main__':
    main()
