import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from firebase_admin import firestore

from noor import db
from noor.agent import build_agent
from noor_database.config import DatabaseSettings
from noor_database.factory import create_appointment_service
from noor_database.testing import MemoryFirestore, transaction_wrapper


class RepeatBookingToolTests(unittest.TestCase):
    def test_adk_tool_reuses_contact_and_reschedules_without_name_or_phone_args(self):
        config = DatabaseSettings(redis_url='', booking_mode='firestore',
                                  calendar_invitations_enabled=False, clinic_data_path='')
        with patch.object(firestore, 'transactional', transaction_wrapper):
            service = create_appointment_service(config, db=MemoryFirestore())
            day = datetime.now(service.timezone).date() + timedelta(days=3)
            while day.weekday() == 6:
                day += timedelta(days=1)
            slots = service.availability(config.doctor_id, day.isoformat())['slots']
            first = service.prepare('alice', 'first', config.doctor_id, slots[0]['start'],
                                    client_name='Alice', phone_number='+971501234567')
            booked = service.confirm('alice', 'first', first['hold_id'])
            with patch.object(db, 'get_service', return_value=service):
                tools = {tool.name: tool for tool in build_agent('alice', 'second').tools}
                declaration = tools['prepare_booking']._get_declaration()
                self.assertEqual(set(declaration.parameters.required), {'doctor_id', 'start'})
                context = asyncio.run(tools['get_context'].run_async(args={}, tool_context=None))
                self.assertEqual(context['patient']['booking_contact']['phone_number'], '+971501234567')
                moved = asyncio.run(tools['prepare_booking'].run_async(args={
                    'doctor_id': config.doctor_id, 'start': slots[1]['start'],
                    'appointment_id': booked['appointment_id'], 'confirm_requested_slot': True,
                }, tool_context=None))
                self.assertFalse(moved['requires_confirmation'])
                self.assertTrue(moved['rescheduled'])
                self.assertEqual(moved['appointment_id'], booked['appointment_id'])
                self.assertEqual(db.get_context_store().get('alice', 'second'), {})
                info = asyncio.run(tools['get_hospital_info'].run_async(args={}, tool_context=None))
                self.assertIn('checkup', info['services'])
                self.assertFalse(info['locations'])
