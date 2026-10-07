from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared metadata; business tables are added in later migrations."""
