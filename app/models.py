from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SearchRequest(BaseModel):
    city: Optional[str] = None
    max_price: Optional[float] = Field(default=None, gt=0)
    room_type: Optional[str] = None


class RoomConfig(BaseModel):
    available: int = Field(ge=0)
    price: float = Field(gt=0)


class HotelUpsertRequest(BaseModel):
    hotel_id: str
    name: str
    city: str
    rooms: Dict[str, RoomConfig]


class ReservationCreateRequest(BaseModel):
    user_id: str
    hotel_id: str
    room_type: str
    nights: int = Field(default=1, ge=1)
    payment_method: str = "card"
    idempotency_key: Optional[str] = Field(default=None, min_length=1)


class ReservationCancelRequest(BaseModel):
    user_id: str
    reservation_id: str


class ReservationResponse(BaseModel):
    ok: bool
    message: str
    reservation_id: Optional[str] = None
    payment_id: Optional[str] = None
    total_price: Optional[float] = None
    refund_percent: Optional[int] = None
    refund_amount: Optional[float] = None


class UserReservationsResponse(BaseModel):
    user_id: str
    reservations: List[dict]
