"""Settings loaded from .env"""
from pathlib import Path

from dotenv import load_dotenv
from noor_database.config import DatabaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=True)


class Settings(DatabaseSettings):
    google_api_key: str = ''
    gemini_model: str = 'gemini-3.1-flash-lite'
    firebase_project_id: str = ''
    google_application_credentials: str = ''
    firestore_database_id: str = '(default)'
    booking_mode: str = 'firestore_calendar'
    clinic_timezone: str = 'Asia/Dubai'
    doctor_id: str = 'primary-doctor'
    doctor_name: str = 'Clinic team'
    doctor_specialty: str = 'General Dentistry'
    doctor_services: str = 'checkup,cleaning,whitening'
    frontend_origins: str = 'http://localhost:3000,http://127.0.0.1:3000'
    test_patient_uid: str = 'test-patient-001'
    test_patient_name: str = 'Test Patient'

    model_config = {'env_file': BASE_DIR / '.env', 'extra': 'ignore'}


settings = Settings(data_root=BASE_DIR)
