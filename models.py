from __future__ import annotations

import uuid
from datetime import datetime, date

from sqlalchemy import Column, Date, DateTime, ForeignKey, String, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db import Base


class Patient(Base):
    __tablename__ = "patients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dni = Column(String(20), unique=True, nullable=False, index=True)
    full_name = Column(String(200), nullable=False)
    phone = Column(String(30), nullable=True)
    email = Column(String(200), nullable=True)
    dob = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    appointments = relationship("Appointment", back_populates="patient", cascade="all, delete-orphan")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    start_at = Column(DateTime(timezone=True), nullable=False)
    end_at = Column(DateTime(timezone=True), nullable=False)
    location = Column(String(200), nullable=True)
    source = Column(String(50), nullable=False, default="telegram")
    status = Column(String(30), nullable=False, default="scheduled")
    calendar_event_id = Column(String(120), nullable=True)
    calendar_link = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    patient = relationship("Patient", back_populates="appointments")

    __table_args__ = (
        UniqueConstraint("calendar_event_id", name="uq_calendar_event_id"),
        Index("ix_appointments_patient_start", "patient_id", "start_at"),
    )
