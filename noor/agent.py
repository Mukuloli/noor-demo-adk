"""Google ADK dental booking agent — tools wired to shared noor-database."""
from datetime import datetime
from zoneinfo import ZoneInfo

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, ToolContext
from noor_database import classify_appointments
from noor_database.users import UserDataService
from noor_database.adk_latency import async_tool, generation_config
from noor_database.hospital import hospital_info
from noor_database.changes import require_change_reason, require_later_confirmation
from noor_database.errors import BookingError

from .config import settings
from . import db as firestore_db

INSTRUCTIONS = """You are Noor, a friendly dental appointment assistant.
Help the user book, view, reschedule or cancel appointments, and answer hospital information questions.
Speak in the user's language — English, Hindi, or Hinglish is fine.

Rules:
- Use the current clinic date/time supplied below. Call get_context only when you need the patient's profile.
- For greetings, reply directly without calling tools. For hospital facts, use get_hospital_info.
- Call get_doctors when you need available doctors for a booking or availability request.
- Call check_availability with doctor_id and date before suggesting any slot.
- For booking, call get_context to check patient.booking_contact before asking for contact details. Reuse saved client_name and phone_number. Ask only for missing details on a first booking; never ask again on repeat bookings or rescheduling. Do NOT ask for email.
- For rescheduling, call get_appointments, select the user's appointment (clarify which only if multiple), reuse its contact details and pass appointment_id to prepare_booking.
- Ask only for a missing date or time. Never re-ask a date/time the user already provided. Check availability for the user's requested time; if unavailable, offer alternatives and let the user choose.
- For new repeat bookings with saved contact and a date/time explicitly requested or selected by the user, call prepare_booking with confirm_requested_slot=true to book directly. No extra confirmation question is needed. Never use this flag for an unselected suggested slot, first booking, changed contact, or cancellation.
- Use get_hospital_info for hospital services, hours and location. Answer only from configured information; refer missing facts to reception.
- Call prepare_booking with name, phone, doctor_id and a slot's 'start' value.
- If prepare_booking returns requires_confirmation=false, the booking is already saved: report its date/time without asking contact details or confirmation. Otherwise read back name, phone, date/time and ask for confirmation.
- Call confirm_booking ONLY after user says yes/haan/confirm/okay.
- Call release_hold if user says no/nahi/cancel.
- For viewing appointments: call get_appointments.
- For cancellation: call get_appointments → prepare_cancellation → confirm_booking.
- Never make up slots or IDs — only use values returned by tools.
- Be brief and friendly.
- Reply in plain text. Do not use Markdown, asterisks, bold markers, or headings.
- Never show appointment IDs, hold IDs or other technical identifiers to the patient. Describe appointments using their date, time and service.
- Present booking details on separate lines (Name, Date, Time, Doctor) in the user's language. Say a booking is confirmed only after confirm_booking succeeds.


For BOTH cancellation and rescheduling: ask for a reason unless the caller already supplied it. Pass that reason as change_reason, separate from the appointment service/reason. Never invent it. Prepare the change, read back the appointment, reason and (for rescheduling) old/new date and time, then WAIT for explicit yes/confirm in a later turn before calling confirm_action or confirm_booking. Reuse saved name and phone without asking again. A requested change, date/time, or reason is not itself confirmation. Cancellation/rescheduling emails are requested only after the change succeeds. Claim a notification only when customer_notified=true or customer_invited=true. Explain a failed notification without claiming that the successful change failed.

Appointment status rules:
- NEVER count past/back-date appointments as current or active appointments! When the user asks "mere appointments", "do I have any appointments?", only count and list UPCOMING appointments as their active appointments.
- 'upcoming': future appointments. Show date and time normally (e.g. "Aapka 1 upcoming appointment hai: [Date] at [Time]").
- 'delayed' or 'left': back-date appointments whose scheduled time has already passed.
  - DO NOT say "Aapka appointment hai is date ko" for past dates.
  - Tell the user: "Aapka [Date] wala appointment miss ho gaya tha."
  - Proactively offer: "Kya aap naya appointment book karna chahte hain?"
  - If there are NO upcoming appointments, say: "Aapka koi upcoming appointment nahi hai." Then if they had a past missed appointment, mention: "Aapka pichla appointment miss ho gaya tha, kya aap naya slot book karna chahte hain?"
"""

def build_agent(uid: str, session_id: str) -> LlmAgent:
    """Build a fresh LlmAgent with tools bound to this user/session."""

    def get_context() -> dict:
        """Get today's clinic date and time."""
        tz = ZoneInfo(settings.clinic_timezone)
        return {
            'clinic_time': datetime.now(tz).isoformat(),
            'timezone': settings.clinic_timezone,
            'patient': UserDataService(firestore_db.get_service()).profile_for_booking(uid),
        }

    def get_doctors() -> dict:
        """List available clinic doctors."""
        doctors = firestore_db.get_doctors()
        return {'ok': True, 'doctors': doctors, 'message': 'Available doctors.'}

    def get_hospital_info() -> dict:
        """Get configured hospital services, opening hours and locations."""
        return hospital_info(firestore_db.get_service())

    def get_appointments() -> dict:
        """Get the patient's confirmed appointments. Strictly separates upcoming (active) from missed/past (back-date)."""
        return classify_appointments(firestore_db.get_appointments(uid))

    def check_availability(doctor_id: str, date: str) -> dict:
        """Check free slots for a doctor on a date (YYYY-MM-DD)."""
        slots = firestore_db.check_availability(doctor_id, date)
        if not slots:
            return {'ok': True, 'slots': [], 'message': 'No available slots on this date.'}
        return {'ok': True, 'slots': slots,
                'message': f'Found {len(slots)} available slot(s).'}

    def prepare_booking(doctor_id: str, start: str, client_name: str = '',
                        phone_number: str = '', reason: str = 'Appointment',
                        client_email: str = '', appointment_id: str = '',
                        confirm_requested_slot: bool = False, change_reason: str = '', tool_context: ToolContext = None) -> dict:
        """Reuse saved contact. Confirm directly only for returning users requesting this exact slot; appointment_id reschedules."""
        result = firestore_db.prepare_booking(
            uid=uid, session_id=session_id, doctor_id=doctor_id,
            start=start, client_name=client_name, phone_number=phone_number,
            reason=reason, client_email=client_email, appointment_id=appointment_id,
            confirm_requested_slot=confirm_requested_slot,
            change_reason=change_reason,
        )
        if result.get('ok') and result.get('hold_id'):
            firestore_db.get_context_store().save(uid, session_id, {
                'stage': 'confirmation',
                'hold_id': result['hold_id'],
                'confirmation_invocation_id': tool_context.invocation_id if tool_context else '',
            })
        elif result.get('ok') and result.get('requires_confirmation') is False:
            firestore_db.get_context_store().save(uid, session_id, {})
        return result

    def confirm_booking(tool_context: ToolContext = None) -> dict:
        """Confirm the pending booking or cancellation after patient says yes."""
        state = firestore_db.get_context_store().get(uid, session_id)
        try:
            require_later_confirmation(state, tool_context.invocation_id if tool_context else '')
        except BookingError as exc:
            return {'ok': False, 'code': exc.code, 'message': exc.message}
        if state.get('stage') == 'confirmation':
            result = firestore_db.confirm_booking(uid, session_id, state['hold_id'])
            if result.get('ok'):
                firestore_db.get_context_store().save(uid, session_id, {})
            return result
        elif state.get('stage') == 'cancel_confirmation':
            result = firestore_db.cancel_appointment(uid, state['appointment_id'], change_reason=state.get('change_reason', ''))
            if result.get('ok'):
                firestore_db.get_context_store().save(uid, session_id, {})
            return result
        return {'ok': False, 'message': 'Nothing pending to confirm. Please prepare a booking first.'}

    def release_hold() -> dict:
        """Release the held slot when patient says no."""
        result = firestore_db.release_hold(uid, session_id)
        if result.get('ok'):
            firestore_db.get_context_store().save(uid, session_id, {})
        return result

    def prepare_cancellation(appointment_id: str, change_reason: str = '', tool_context: ToolContext = None) -> dict:
        """Prepare cancellation of a given appointment by ID."""
        appts = firestore_db.get_appointments(uid)
        target = next((a for a in appts if a['id'] == appointment_id), None)
        if not target:
            return {'ok': False, 'message': 'Appointment not found.'}
        try:
            explanation = require_change_reason(change_reason, 'cancelling')
        except BookingError as exc:
            return {'ok': False, 'code': exc.code, 'message': exc.message, 'retryable': True}
        firestore_db.get_context_store().save(uid, session_id, {
            'stage': 'cancel_confirmation',
            'appointment_id': appointment_id,
            'change_reason': explanation,
            'confirmation_invocation_id': tool_context.invocation_id if tool_context else '',
        })
        name = target.get('client_name', 'your appointment')
        return {'ok': True, 'appointment': target,
                'change_reason': explanation, 'requires_confirmation': True,
                'message': f"Cancel appointment on {target['start'][:10]} for {name}? Reason: {explanation}. Please confirm or decline."}

    return LlmAgent(
        name='noor',
        model=settings.gemini_model,
        instruction=INSTRUCTIONS + '\nCurrent clinic time: ' + datetime.now(ZoneInfo(settings.clinic_timezone)).isoformat()
                    + '\nClinic timezone: ' + settings.clinic_timezone,
        generate_content_config=generation_config(settings.gemini_model),
        tools=[
            FunctionTool(async_tool(get_context)),
            FunctionTool(async_tool(get_doctors)),
            FunctionTool(async_tool(get_hospital_info)),
            FunctionTool(async_tool(get_appointments)),
            FunctionTool(async_tool(check_availability)),
            FunctionTool(async_tool(prepare_booking)),
            FunctionTool(async_tool(confirm_booking)),
            FunctionTool(async_tool(release_hold)),
            FunctionTool(async_tool(prepare_cancellation)),
        ],
    )
