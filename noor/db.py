"""ADK tool adapter. All database rules live in the shared noor-database package."""
from functools import lru_cache
from zoneinfo import ZoneInfo

from noor_database import BookingError
from noor_database.factory import create_appointment_service

from .config import settings


@lru_cache(maxsize=1)
def get_service():
    return create_appointment_service(settings)


def get_db():
    return get_service().db


def get_clinic_tz():
    return ZoneInfo(settings.clinic_timezone)


def get_doctors() -> list[dict]:
    return get_service().public_doctors()


def check_availability(doctor_id: str, date_str: str) -> list[dict]:
    try:
        return get_service().availability(doctor_id, date_str)['slots']
    except BookingError:
        return []


def _booking_call(method, *args, **kwargs):
    try:
        return getattr(get_service(), method)(*args, **kwargs)
    except BookingError as exc:
        return {'ok': False, 'code': exc.code, 'message': exc.message}


def prepare_booking(uid: str, session_id: str, doctor_id: str, start: str,
                    client_name: str, phone_number: str, reason: str = 'Appointment',
                    client_email: str = '', appointment_id: str = '') -> dict:
    contact = ({'client_name': client_name, 'phone_number': phone_number, 'client_email': client_email}
               if getattr(get_service(), 'supports_contact_details', False) else {})
    return _booking_call('prepare', uid, session_id, doctor_id, start, reason,
                         appointment_id or None, **contact)


def confirm_booking(uid: str, session_id: str, hold_id: str) -> dict:
    return _booking_call('confirm', uid, session_id, hold_id)


def release_hold(uid: str, session_id: str) -> dict:
    return _booking_call('release', uid, session_id)


def get_appointments(uid: str) -> list[dict]:
    return get_service().appointments(uid)['appointments']


def cancel_appointment(uid: str, appointment_id: str) -> dict:
    return _booking_call('cancel', uid, appointment_id)
