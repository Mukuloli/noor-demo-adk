"""Exercise real model booking tools against an isolated in-memory database.

No patient records, Google Calendar requests or emails are used. Every scenario
starts with a synthetic returning patient and checks the persisted outcome.
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from firebase_admin import firestore
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from noor import db
from noor.agent import build_agent
from noor.config import settings
from noor_database.config import DatabaseSettings
from noor_database.factory import create_appointment_service
from noor_database.testing import MemoryFirestore, transaction_wrapper
from noor_database.schema import Collections


async def scenario(model, action, verbose=False):
    config = DatabaseSettings(redis_url='', booking_mode='firestore',
                              calendar_invitations_enabled=False, clinic_data_path='')
    service = create_appointment_service(config, db=MemoryFirestore())
    day = datetime.now(service.timezone).date() + timedelta(days=5)
    while day.weekday() == 6:
        day += timedelta(days=1)
    slots = service.availability(config.doctor_id, day.isoformat())['slots']
    uid, identity = 'synthetic-patient', uuid.uuid4().hex
    with patch.object(firestore, 'transactional', transaction_wrapper), \
         patch.object(db, 'get_service', return_value=service), \
         patch.object(settings, 'gemini_model', model), \
         patch.object(settings, 'clinic_timezone', str(service.timezone)):
        hold = service.prepare(uid, 'seed', config.doctor_id, slots[0]['start'],
                               client_name='Test Patient', phone_number='+971501234567')
        appointment_id = service.confirm(uid, 'seed', hold['hold_id'])['appointment_id']
        sessions = InMemorySessionService()
        await sessions.create_session(app_name='model-check', user_id=uid, session_id=identity)
        agent = build_agent(uid, identity)
        runner = Runner(agent=agent, app_name='model-check', session_service=sessions)
        record = service.db.collection(Collections.APPOINTMENTS).document(appointment_id)
        original_start = datetime.fromisoformat(slots[0]['start'])
        target_start = datetime.fromisoformat(slots[1]['start'])
        time_text = target_start.astimezone(service.timezone).strftime('%I:%M %p')
        metrics = []

        async def turn(message):
            first = None
            started = time.perf_counter()
            calls, text = [], []
            streamed = False
            async for event in runner.run_async(user_id=uid, session_id=identity,
                new_message=types.Content(role='user', parts=[types.Part(text=message)]),
                run_config=RunConfig(streaming_mode=StreamingMode.SSE)):
                if not event.partial:
                    calls.extend(call.name for call in event.get_function_calls())
                for part in (event.content.parts if event.content else []):
                    if part.text and not part.thought:
                        first = first or time.perf_counter() - started
                        if event.partial or not streamed:
                            text.append(part.text)
                if event.partial and event.content and any(part.text and not part.thought for part in event.content.parts):
                    streamed = True
                elif not event.partial:
                    streamed = False
            metrics.append({'first_text_seconds': round(first, 3) if first else None,
                            'total_seconds': round(time.perf_counter() - started, 3),
                            'tool_calls': calls})
            if verbose:
                print(json.dumps({'model': model, 'scenario': action, 'synthetic_reply': ''.join(text),
                                  'tool_calls': calls}), flush=True)
            return ''.join(text)

        if action == 'repeat_booking':
            await turn(f'Book another appointment on {day.isoformat()} at {time_text} Dubai time. Use my saved details.')
            booked = service.appointments(uid)['appointments']
            assert len(booked) == 2, 'Repeat booking was not saved directly'
            assert any(datetime.fromisoformat(item['start']) == target_start for item in booked), 'Wrong selected booking time'
            assert all(item['phone_number'] == '+971501234567' for item in booked)
        elif action == 'reschedule':
            await turn(f'Reschedule my appointment to {day.isoformat()} at {time_text} Dubai time.')
            assert record.get().to_dict()['start'] == original_start
            assert db.get_context_store().get(uid, identity).get('stage') != 'confirmation', 'Reason was invented'
            await turn('The reason is a work meeting.')
            assert record.get().to_dict()['start'] == original_start, 'Rescheduled before confirmation'
            assert db.get_context_store().get(uid, identity).get('stage') == 'confirmation'
            await turn('Yes, confirm the rescheduling.')
            assert record.get().to_dict()['start'] == target_start
            assert record.get().to_dict()['reschedule_reason']
        else:
            await turn('Cancel my appointment.')
            assert record.get().to_dict()['status'] == 'confirmed'
            assert not db.get_context_store().get(uid, identity).get('change_reason'), 'Reason was invented'
            await turn('The reason is that I am travelling.')
            assert record.get().to_dict()['status'] == 'confirmed', 'Cancelled before confirmation'
            assert db.get_context_store().get(uid, identity).get('stage') == 'cancel_confirmation'
            await turn('Yes, confirm cancellation.')
            assert record.get().to_dict()['status'] == 'cancelled'
            assert record.get().to_dict()['cancellation_reason']
        return {'model': model, 'scenario': action, 'passed': True, 'turns': metrics}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', default=[settings.gemini_model])
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    failures = 0
    for model in args.models:
        for action in ('repeat_booking', 'reschedule', 'cancel'):
            try:
                result = await asyncio.wait_for(scenario(model, action, args.verbose), timeout=90)
            except Exception as exc:
                failures += 1
                result = {'model': model, 'scenario': action, 'passed': False,
                          'error_type': type(exc).__name__}
                if isinstance(exc, AssertionError):
                    result['check'] = str(exc)
            print(json.dumps(result), flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
