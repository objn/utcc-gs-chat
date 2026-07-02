import uuid

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class Contact(TimestampMixin, SQLModel, table=True):
    __tablename__ = "contacts"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    platform_id: str = Field(max_length=255, index=True)
    platform: str = Field(default="facebook", max_length=50)
    display_name: str = Field(max_length=255)
    avatar_color: str = Field(default="#d4891c", max_length=20)
    avatar_url: str | None = Field(default=None, max_length=500)
    agent_chat_enabled: bool = Field(default=True)
    page_id: str | None = Field(default=None, max_length=255)
    last_message: str | None = Field(default=None, max_length=1000)
    last_message_at: str | None = Field(default=None, max_length=50)
