"""Single SQLAlchemy DeclarativeBase shared by every ORM model in atlas-core."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """All ATLAS ORM models inherit from this base so they share metadata."""
