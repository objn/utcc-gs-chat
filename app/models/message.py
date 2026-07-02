import uuid

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class Message(TimestampMixin, SQLModel, table=True):
    __tablename__ = "messages"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    contact_id: uuid.UUID = Field(foreign_key="contacts.id", index=True)
    direction: str = Field(max_length=10)  # "in" | "out"
    text: str | None = Field(default=None, max_length=4000)
    attachment_type: str | None = Field(default=None, max_length=20)  # image|file|video|audio
    attachment_url: str | None = Field(default=None, max_length=1000)
    fb_message_id: str | None = Field(default=None, max_length=255)
