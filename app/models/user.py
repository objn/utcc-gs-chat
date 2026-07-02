import uuid

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class User(TimestampMixin, SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    full_name: str = Field(max_length=255)
    email: str = Field(max_length=255, unique=True, index=True)
    hashed_password: str = Field(max_length=255)
    role: str = Field(default="admin", max_length=50)
    is_active: bool = Field(default=True)
