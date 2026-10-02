"""Settings loaded from .env"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_api_key: str = ''
    gemini_model: str = 'gemini-2.5-flash'
    firebase_project_id: str = ''
    google_application_credentials: str = ''
    firestore_database_id: str = '(default)'
    booking_mode: str = 'firestore_calendar'
    clinic_timezone: str = 'Asia/Dubai'
    doctor_id: str = 'primary-doctor'
    doctor_name: str = 'Clinic team'
    doctor_specialty: str = 'General Dentistry'
    doctor_services: str = 'checkup,cleaning,whitening'
    test_patient_uid: str = 'test-patient-001'
    test_patient_name: str = 'Test Patient'

    model_config = {'env_file': '.env', 'extra': 'ignore'}


settings = Settings()
