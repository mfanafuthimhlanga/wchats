"""Shared declarative base for all Veridian control DB ORM models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
