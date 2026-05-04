import os
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@db:5432/ray_hotel",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class Hotel(Base):
    __tablename__ = "hotels"

    hotel_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    city = Column(String, nullable=False)
    rooms = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class Reservation(Base):
    __tablename__ = "reservations"

    reservation_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    hotel_id = Column(String, nullable=False)
    room_type = Column(String, nullable=False)
    nights = Column(Integer, nullable=False)
    total_price = Column(Float, nullable=False)
    payment_id = Column(String, nullable=False)
    status = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    refund_percent = Column(Integer, nullable=True)
    refund_amount = Column(Float, nullable=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def save_hotel_snapshot(hotel_id: str, name: str, city: str, rooms: dict):
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        hotel = session.get(Hotel, hotel_id)
        if hotel:
            hotel.name = name
            hotel.city = city
            hotel.rooms = rooms
            hotel.updated_at = now
        else:
            session.add(
                Hotel(
                    hotel_id=hotel_id,
                    name=name,
                    city=city,
                    rooms=rooms,
                    updated_at=now,
                )
            )
        session.commit()


def load_hotels() -> list[dict]:
    with SessionLocal() as session:
        hotels = session.query(Hotel).all()
        return [
            {
                "hotel_id": hotel.hotel_id,
                "name": hotel.name,
                "city": hotel.city,
                "rooms": hotel.rooms,
            }
            for hotel in hotels
        ]


def save_reservation(reservation: dict):
    created_at = datetime.fromtimestamp(reservation["created_at"], tz=timezone.utc)
    with SessionLocal() as session:
        db_res = session.get(Reservation, reservation["reservation_id"])
        if db_res:
            db_res.status = reservation["status"]
            db_res.refund_percent = reservation.get("refund_percent")
            db_res.refund_amount = reservation.get("refund_amount")
        else:
            session.add(
                Reservation(
                    reservation_id=reservation["reservation_id"],
                    user_id=reservation["user_id"],
                    hotel_id=reservation["hotel_id"],
                    room_type=reservation["room_type"],
                    nights=reservation["nights"],
                    total_price=reservation["total_price"],
                    payment_id=reservation["payment_id"],
                    status=reservation["status"],
                    created_at=created_at,
                    refund_percent=reservation.get("refund_percent"),
                    refund_amount=reservation.get("refund_amount"),
                )
            )
        session.commit()


def update_reservation_status(reservation_id: str, status: str, refund_percent: int, refund_amount: float):
    with SessionLocal() as session:
        db_res = session.get(Reservation, reservation_id)
        if not db_res:
            return
        db_res.status = status
        db_res.refund_percent = refund_percent
        db_res.refund_amount = refund_amount
        session.commit()


def load_reservations() -> list[dict]:
    with SessionLocal() as session:
        reservations = session.query(Reservation).all()
        return [_reservation_to_dict(item) for item in reservations]


def load_reservations_by_user(user_id: str) -> list[dict]:
    with SessionLocal() as session:
        reservations = session.query(Reservation).filter_by(user_id=user_id).all()
        return [_reservation_to_dict(item) for item in reservations]


def _reservation_to_dict(item: Reservation) -> dict:
    return {
        "reservation_id": item.reservation_id,
        "user_id": item.user_id,
        "hotel_id": item.hotel_id,
        "room_type": item.room_type,
        "nights": item.nights,
        "total_price": item.total_price,
        "payment_id": item.payment_id,
        "status": item.status,
        "created_at": item.created_at.timestamp(),
        "refund_percent": item.refund_percent,
        "refund_amount": item.refund_amount,
    }
