from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CampaignLog(Base):
    """Кампании, созданные через обёртку."""
    __tablename__ = "campaign_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255))
    geo: Mapped[str] = mapped_column(String(255))
    offer_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ActionLog(Base):
    """Журнал добавления/удаления офферов."""
    __tablename__ = "action_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(32))  # add / remove / restore / pin / push / cancel / fetch
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    stream_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Stream(Base):
    """Локальный слепок потока Keitaro."""
    __tablename__ = "stream"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=0)
    links: Mapped[list["StreamOffer"]] = relationship(
        cascade="all, delete-orphan", order_by="StreamOffer.id", back_populates="stream")


class StreamOffer(Base):
    """Привязка оффера к потоку: желаемое состояние + что сейчас в Keitaro (synced_*)."""
    __tablename__ = "stream_offer"
    __table_args__ = (UniqueConstraint("stream_id", "offer_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("stream.id", ondelete="CASCADE"))
    offer_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))          # active | removed
    share: Mapped[int] = mapped_column(Integer, default=0)
    pin_value: Mapped[int | None] = mapped_column(Integer, nullable=True)  # закреплённый вес
    is_new: Mapped[bool] = mapped_column(Boolean, default=False)           # добавлен, ещё не опубликован
    synced_status: Mapped[str] = mapped_column(String(16))   # active | removed (в Keitaro)
    synced_share: Mapped[int] = mapped_column(Integer, default=0)
    stream: Mapped[Stream] = relationship(back_populates="links")
