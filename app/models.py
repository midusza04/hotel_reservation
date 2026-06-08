"""Pydantic request and response schemas for the hotel reservation API."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials accepted by the demo authentication endpoint."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Bearer token returned after successful authentication."""

    access_token: str
    token_type: str = "bearer"


class SearchRequest(BaseModel):
    """Optional filters used when searching hotel offers."""

    city: Optional[str] = None
    max_price: Optional[float] = Field(default=None, gt=0)
    room_type: Optional[str] = None


class RoomConfig(BaseModel):
    """Administrative definition of availability and nightly price for a room."""

    available: int = Field(ge=0)
    price: float = Field(gt=0)


class HotelUpsertRequest(BaseModel):
    """Payload for creating or updating a hotel offer."""

    hotel_id: str
    name: str
    city: str
    rooms: Dict[str, RoomConfig]


class ReservationCreateRequest(BaseModel):
    """Payload for starting the booking workflow."""

    user_id: str
    hotel_id: str
    room_type: str
    nights: int = Field(default=1, ge=1)
    payment_method: str = "card"
    idempotency_key: Optional[str] = Field(default=None, min_length=1)


class ReservationCancelRequest(BaseModel):
    """Payload for cancelling an existing reservation."""

    user_id: str
    reservation_id: str


class ReservationResponse(BaseModel):
    """Common response returned by booking and cancellation operations."""

    ok: bool
    message: str
    reservation_id: Optional[str] = None
    payment_id: Optional[str] = None
    total_price: Optional[float] = None
    refund_percent: Optional[int] = None
    refund_amount: Optional[float] = None


class UserReservationsResponse(BaseModel):
    """Reservation history returned for a single user."""

    user_id: str
    reservations: List[dict]


class AuditLogEntry(BaseModel):
    """Single audit event as exposed by the administrative API."""

    event_id: str
    event_type: str
    actor_id: Optional[str] = None
    entity_id: Optional[str] = None
    details: Optional[dict] = None
    occurred_at: str


class AuditLogsResponse(BaseModel):
    """Collection response for audit log queries."""

    entries: List[AuditLogEntry]
