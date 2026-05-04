from contextlib import asynccontextmanager

import ray
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from actors import (
    AdminActor,
    BookingCoordinatorActor,
    InventoryActor,
    PaymentActor,
    ReservationHistoryActor,
)
from models import (
    HotelUpsertRequest,
    LoginRequest,
    LoginResponse,
    ReservationCancelRequest,
    ReservationCreateRequest,
    ReservationResponse,
    SearchRequest,
    UserReservationsResponse,
)


def _init_ray():
    if ray.is_initialized():
        return
    try:
        ray.init(address="auto", ignore_reinit_error=True)
    except Exception:
        ray.init(ignore_reinit_error=True)


def _get_or_create_named_actor(actor_cls, actor_name: str, *args):
    namespace = "ray_hotel"
    try:
        return ray.get_actor(actor_name, namespace=namespace)
    except ValueError:
        return actor_cls.options(name=actor_name, namespace=namespace, lifetime="detached").remote(*args)


def _seed_data(admin_actor):
    initial_hotels = [
        {
            "hotel_id": "h-waw-1",
            "name": "Warsaw Central Hotel",
            "city": "Warszawa",
            "rooms": {
                "single": {"available": 8, "price": 220.0},
                "double": {"available": 5, "price": 340.0},
            },
        },
        {
            "hotel_id": "h-krk-1",
            "name": "Krakow Market Suites",
            "city": "Krakow",
            "rooms": {
                "single": {"available": 6, "price": 210.0},
                "double": {"available": 4, "price": 320.0},
                "apartment": {"available": 2, "price": 520.0},
            },
        },
    ]
    for hotel in initial_hotels:
        ray.get(
            admin_actor.upsert_hotel.remote(
                hotel_id=hotel["hotel_id"],
                name=hotel["name"],
                city=hotel["city"],
                rooms=hotel["rooms"],
            )
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_ray()

    inventory = _get_or_create_named_actor(InventoryActor, "inventory")
    payment = _get_or_create_named_actor(PaymentActor, "payment")
    history = _get_or_create_named_actor(ReservationHistoryActor, "history")
    coordinator = _get_or_create_named_actor(BookingCoordinatorActor, "booking_coordinator", inventory, payment, history)
    admin = _get_or_create_named_actor(AdminActor, "admin", inventory)

    # Seed test data for a quicker local demo.
    _seed_data(admin)

    app.state.inventory = inventory
    app.state.payment = payment
    app.state.history = history
    app.state.coordinator = coordinator
    app.state.admin = admin
    yield


app = FastAPI(
    title="Ray Hotel Reservation System",
    description="MVP distributed hotel reservation system (Ray Actors + FastAPI).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok", "ray_initialized": ray.is_initialized()}


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    token = f"demo-token-{payload.username}"
    return LoginResponse(access_token=token)


@app.post("/hotels/search")
def search_hotels(payload: SearchRequest):
    return ray.get(
        app.state.inventory.search_hotels.remote(
            city=payload.city,
            max_price=payload.max_price,
            room_type=payload.room_type,
        )
    )


@app.post("/reservations", response_model=ReservationResponse)
def create_reservation(payload: ReservationCreateRequest):
    result = ray.get(
        app.state.coordinator.book_room.remote(
            user_id=payload.user_id,
            hotel_id=payload.hotel_id,
            room_type=payload.room_type,
            nights=payload.nights,
            payment_method=payload.payment_method,
        )
    )
    return ReservationResponse(**result)


@app.post("/reservations/cancel", response_model=ReservationResponse)
def cancel_reservation(payload: ReservationCancelRequest):
    result = ray.get(
        app.state.coordinator.cancel_booking.remote(
            user_id=payload.user_id,
            reservation_id=payload.reservation_id,
        )
    )
    return ReservationResponse(**result)


@app.get("/users/{user_id}/reservations", response_model=UserReservationsResponse)
def user_reservations(user_id: str):
    reservations = ray.get(app.state.history.list_user_reservations.remote(user_id=user_id))
    return UserReservationsResponse(user_id=user_id, reservations=reservations)


@app.post("/admin/hotels")
def upsert_hotel(payload: HotelUpsertRequest):
    rooms = {key: value.model_dump() for key, value in payload.rooms.items()}
    return ray.get(
        app.state.admin.upsert_hotel.remote(
            hotel_id=payload.hotel_id,
            name=payload.name,
            city=payload.city,
            rooms=rooms,
        )
    )