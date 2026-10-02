"""Firestore DB helpers — same collections as livkit backend."""
import hashlib
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import firebase_admin
from firebase_admin import credentials, firestore

from .config import settings

_db = None


def get_db():
    global _db
    if _db is None:
        if not firebase_admin._apps:
            cred = credentials.Certificate(settings.google_application_credentials)
            firebase_admin.initialize_app(cred)
        _db = firestore.client(database_id=settings.firestore_database_id)
    return _db


def get_clinic_tz():
    return ZoneInfo(settings.clinic_timezone)


# ─── Doctors ──────────────────────────────────────────────────────────────────

def get_doctors() -> list[dict]:
    return [{
        'id': settings.doctor_id,
        'name': settings.doctor_name,
        'specialty': settings.doctor_specialty,
        'services': [s.strip() for s in settings.doctor_services.split(',')],
    }]


# ─── Availability ─────────────────────────────────────────────────────────────

SLOT_HOURS = [9, 10, 11, 12, 14, 15, 16, 17]  # 9am-12pm, 2pm-5pm


def check_availability(doctor_id: str, date_str: str) -> list[dict]:
    """Return free slots for doctor on date (YYYY-MM-DD). Excludes booked slots."""
    db = get_db()
    tz = get_clinic_tz()
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return []

    # Booked slot keys for that day
    booked = set()
    for snap in db.collection('booking_slots').stream():
        d = snap.to_dict() or {}
        appt_id = d.get('appointment_id')
        if appt_id:
            appt = db.collection('appointments').document(appt_id).get()
            if appt.exists:
                ad = appt.to_dict() or {}
                if ad.get('appointment_date') == date_str and ad.get('doctor_id') == doctor_id:
                    booked.add(snap.id)

    slots = []
    for hour in SLOT_HOURS:
        local_dt = datetime(date.year, date.month, date.day, hour, 30, tzinfo=tz)
        utc_start = local_dt.astimezone(ZoneInfo('UTC'))
        key = hashlib.sha256(f'{doctor_id}:{utc_start.isoformat()}'.encode()).hexdigest()
        if key not in booked:
            slots.append({
                'start': utc_start.isoformat(),
                'end': (utc_start + timedelta(hours=1)).isoformat(),
                'label': local_dt.strftime('%I:%M %p'),
                'slot_key': key,
            })
    return slots


# ─── Hold / Prepare ───────────────────────────────────────────────────────────

HOLD_TTL = 120  # seconds


def prepare_booking(uid: str, session_id: str, doctor_id: str, start: str,
                    client_name: str, phone_number: str,
                    reason: str = 'Appointment', client_email: str = '',
                    appointment_id: str = '') -> dict:
    db = get_db()
    tz = get_clinic_tz()

    if not client_name or len(client_name) > 80:
        return {'ok': False, 'code': 'INVALID_NAME', 'message': 'Please provide a valid name.'}
    import re
    if not re.fullmatch(r'\+?[0-9]{7,15}', phone_number):
        return {'ok': False, 'code': 'INVALID_PHONE', 'message': 'Please provide a valid phone number.'}

    try:
        start_dt = datetime.fromisoformat(start)
    except ValueError:
        return {'ok': False, 'code': 'INVALID_TIME', 'message': 'Invalid time format.'}

    end_dt = start_dt + timedelta(hours=1)
    local_start = start_dt.astimezone(tz)
    slot_key = hashlib.sha256(f'{doctor_id}:{start_dt.isoformat()}'.encode()).hexdigest()

    # Check slot still free
    slot_ref = db.collection('booking_slots').document(slot_key)
    if slot_ref.get().exists:
        return {'ok': False, 'code': 'SLOT_UNAVAILABLE', 'message': 'That slot is no longer available. Please choose another time.'}

    nonce = str(uuid.uuid4())
    hold_ref = db.collection('appointment_holds').document(f'{uid}:{session_id}')
    hold_ref.set({
        'id': nonce,
        'patient_id': uid,
        'session_id': session_id,
        'doctor_id': doctor_id,
        'start': start_dt.isoformat(),
        'end': end_dt.isoformat(),
        'slot_key': slot_key,
        'client_name': client_name,
        'phone_number': phone_number,
        'client_email': client_email,
        'reason': reason,
        'appointment_id': appointment_id or '',
        'expires_at': time.time() + HOLD_TTL,
    })

    doctor = get_doctors()[0]
    return {
        'ok': True,
        'hold_id': nonce,
        'summary': {
            'doctor': doctor['name'],
            'date': local_start.strftime('%A, %d %B %Y'),
            'time': local_start.strftime('%I:%M %p') + f' ({settings.clinic_timezone})',
            'client_name': client_name,
            'phone_number': phone_number,
            'reason': reason,
        },
        'message': (
            f"I've reserved the slot. Here's the summary:\n"
            f"👤 Name: {client_name}\n"
            f"📞 Phone: {phone_number}\n"
            f"📅 Date: {local_start.strftime('%A, %d %B %Y')}\n"
            f"🕐 Time: {local_start.strftime('%I:%M %p')} ({settings.clinic_timezone})\n"
            f"🦷 Reason: {reason}\n\n"
            f"Shall I confirm this booking? (yes/no)"
        ),
    }


# ─── Confirm ──────────────────────────────────────────────────────────────────

def confirm_booking(uid: str, session_id: str, hold_id: str) -> dict:
    db = get_db()
    hold_ref = db.collection('appointment_holds').document(f'{uid}:{session_id}')
    hold_snap = hold_ref.get()
    if not hold_snap.exists:
        return {'ok': False, 'message': 'Hold not found. Please start the booking again.'}

    hold = hold_snap.to_dict()
    if hold.get('id') != hold_id:
        return {'ok': False, 'message': 'Hold ID mismatch.'}
    if hold.get('expires_at', 0) <= time.time():
        return {'ok': False, 'message': 'Hold expired. Please choose a slot again.'}

    slot_key = hold['slot_key']
    slot_ref = db.collection('booking_slots').document(slot_key)
    if slot_ref.get().exists:
        hold_ref.delete()
        return {'ok': False, 'code': 'SLOT_UNAVAILABLE', 'message': 'That slot was just taken. Please choose another time.'}

    appt_id = hold.get('appointment_id') or str(uuid.uuid4())
    appt_ref = db.collection('appointments').document(appt_id)
    tz = get_clinic_tz()
    start_dt = datetime.fromisoformat(hold['start'])
    local_start = start_dt.astimezone(tz)

    appt_ref.set({
        'patient_id': uid,
        'doctor_id': hold['doctor_id'],
        'client_name': hold['client_name'],
        'phone_number': hold['phone_number'],
        'client_email': hold.get('client_email', ''),
        'start': start_dt,
        'end': datetime.fromisoformat(hold['end']),
        'appointment_date': local_start.date().isoformat(),
        'appointment_time': local_start.strftime('%H:%M'),
        'timezone': settings.clinic_timezone,
        'reason': hold['reason'],
        'status': 'confirmed',
        'booking_slot_key': slot_key,
        'booking_source': 'adk_chat',
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    }, merge=True)

    slot_ref.set({'appointment_id': appt_id})
    hold_ref.delete()

    return {
        'ok': True,
        'appointment_id': appt_id,
        'message': (
            f"✅ Appointment confirmed!\n"
            f"📅 {local_start.strftime('%A, %d %B %Y')} at "
            f"{local_start.strftime('%I:%M %p')} ({settings.clinic_timezone})\n"
            f"👤 {hold['client_name']} · 📞 {hold['phone_number']}"
        ),
    }


# ─── Release Hold ─────────────────────────────────────────────────────────────

def release_hold(uid: str, session_id: str) -> dict:
    db = get_db()
    db.collection('appointment_holds').document(f'{uid}:{session_id}').delete()
    return {'ok': True, 'message': 'Hold released.'}


# ─── Appointments ─────────────────────────────────────────────────────────────

def get_appointments(uid: str) -> list[dict]:
    db = get_db()
    tz = get_clinic_tz()
    now = datetime.now(tz)
    results = []
    for snap in (db.collection('appointments')
                 .where('patient_id', '==', uid)
                 .where('status', '==', 'confirmed')
                 .stream()):
        d = snap.to_dict() or {}
        start = d.get('start')
        if start and hasattr(start, 'astimezone'):
            local = start.astimezone(tz)
            if local > now:
                results.append({
                    'id': snap.id,
                    'doctor_id': d.get('doctor_id', ''),
                    'start': local.isoformat(),
                    'reason': d.get('reason', 'Appointment'),
                    'client_name': d.get('client_name', ''),
                    'phone_number': d.get('phone_number', ''),
                    'status': 'confirmed',
                })
    return sorted(results, key=lambda x: x['start'])


# ─── Cancel ───────────────────────────────────────────────────────────────────

def cancel_appointment(uid: str, appointment_id: str) -> dict:
    db = get_db()
    ref = db.collection('appointments').document(appointment_id)
    snap = ref.get()
    if not snap.exists or snap.to_dict().get('patient_id') != uid:
        return {'ok': False, 'message': 'Appointment not found.'}
    d = snap.to_dict()
    slot_key = d.get('booking_slot_key')
    ref.update({'status': 'cancelled', 'updated_at': firestore.SERVER_TIMESTAMP})
    if slot_key:
        db.collection('booking_slots').document(slot_key).delete()
    return {'ok': True, 'message': '✅ Appointment cancelled successfully.'}
