from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class Config(TimestampMixin, SQLModel, table=True):
    __tablename__ = "configs"

    config_id: str = Field(primary_key=True, max_length=100)
    data: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
