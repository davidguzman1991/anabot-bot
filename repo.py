from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import Patient, Appointment


def get_patient_by_dni(db: Session, dni: str) -> Optional[Patient]:
    stmt = select(Patient).where(Patient.dni == dni)
    return db.scalar(stmt)


def upsert_patient(
    db: Session,
    dni: str,
    full_name: str,
    phone: str = "",
    email: str = "",
    dob: Optional[datetime.date] = None,
) -> Patient:
    patient = get_patient_by_dni(db, dni)
    if patient:
        patient.full_name = full_name or patient.full_name
        patient.phone = phone or patient.phone
        patient.email = email or patient.email
        patient.dob = dob or patient.dob
    else:
        patient = Patient(
            dni=dni,
            full_name=full_name,
            phone=phone or None,
            email=email or None,
            dob=dob,
        )
        db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def create_appointment(
    db: Session,
    patient_id,
    start_at: datetime,
    end_at: datetime,
    location: str = "",
    source: str = "telegram",
    calendar_event_id: Optional[str] = None,
    calendar_link: Optional[str] = None,
) -> Appointment:
    appt = Appointment(
        patient_id=patient_id,
        start_at=start_at,
        end_at=end_at,
        location=location,
        source=source,
        status="scheduled",
        calendar_event_id=calendar_event_id,
        calendar_link=calendar_link,
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return appt


def get_next_appointment(db: Session, patient_id) -> Optional[Appointment]:
    stmt = (
        select(Appointment)
        .where(Appointment.patient_id == patient_id, Appointment.status == "scheduled")
        .order_by(Appointment.start_at.asc())
        .limit(1)
    )
    return db.scalar(stmt)


def get_last_appointment(db: Session, patient_id) -> Optional[Appointment]:
    stmt = (
        select(Appointment)
        .where(Appointment.patient_id == patient_id)
        .order_by(Appointment.start_at.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def cancel_appointment(db: Session, appointment_id) -> bool:
    stmt = (
        update(Appointment)
        .where(Appointment.id == appointment_id)
        .values(status="cancelled")
        .returning(Appointment.id)
    )
    result = db.execute(stmt)
    db.commit()
    return result.scalar_one_or_none() is not None
