"""Check ADK tool adapters use the shared database contract."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from noor_database.config import DatabaseSettings
from noor_database.factory import create_appointment_service
from firebase_admin import firestore
from noor_database.testing import MemoryFirestore, transaction_wrapper
from noor import db


class DatabaseAdapterTests(unittest.TestCase):
    def test_adk_adapter_uses_shared_firestore_service(self):
        with patch.object(firestore, 'transactional', transaction_wrapper):
            config = DatabaseSettings(redis_url='', booking_mode='firestore', calendar_invitations_enabled=False,
                                      clinic_data_path='', doctor_id='primary-doctor')
            service = create_appointment_service(config, db=MemoryFirestore())
            with patch.object(db, 'get_service', return_value=service):
                doctors = db.get_doctors()
                day = datetime.now(ZoneInfo('Asia/Dubai')).date() + timedelta(days=3)
                while day.weekday() == 6:
                    day += timedelta(days=1)
                slot = db.check_availability(doctors[0]['id'], day.isoformat())[0]
                held = db.prepare_booking('patient', 'chat', doctors[0]['id'], slot['start'],
                                          'Test Patient', '+971501234567')
                self.assertTrue(held['ok'])
                confirmed = db.confirm_booking('patient', 'chat', held['hold_id'])
                self.assertEqual(db.get_appointments('patient')[0]['id'], confirmed['appointment_id'])
                self.assertEqual(db.get_appointments('other-patient'), [])
                self.assertTrue(db.cancel_appointment('patient', confirmed['appointment_id'], change_reason='Schedule conflict')['ok'])
                self.assertEqual(db.get_appointments('patient'), [])
