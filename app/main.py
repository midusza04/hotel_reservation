"""FastAPI entrypoint for the Ray-based hotel reservation system.

The module owns HTTP routing, demo authentication, application lifecycle
initialization and the bridge between synchronous API handlers and Ray actors.
Ray actors keep domain state and execute distributed reservation workflows,
while FastAPI exposes a small JSON API consumed by the demo frontend.
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Optional

import ray
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from actors import (
    AdminActor,
    AuditLogActor,
    BookingCoordinatorActor,
    InventoryActor,
    MetricsActor,
    PaymentActor,
    ReservationHistoryActor,
)
from db import init_db
from models import (
    AuditLogsResponse,
    HotelUpsertRequest,
    LoginRequest,
    LoginResponse,
    ReservationCancelRequest,
    ReservationCreateRequest,
    ReservationResponse,
    SearchRequest,
    UserReservationsResponse,
)


JWT_SECRET = "dev-secret"
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_MINUTES = 60
SNAPSHOT_INTERVAL_SECONDS = 60
RAY_CALL_TIMEOUT_S = 15

_FAKE_USERS = {
    "user1": {"password": "pass", "role": "user"},
    "admin": {"password": "admin", "role": "admin"},
}

_http_bearer = HTTPBearer()


def _init_ray():
    """Initialize Ray by joining a cluster when possible or starting local Ray."""
    if ray.is_initialized():
        return
    try:
        ray.init(address="auto", ignore_reinit_error=True)
    except Exception:
        ray.init(ignore_reinit_error=True)


def _get_or_create_named_actor(actor_cls, actor_name: str, *args, spread: bool = False):
    """Return a detached named actor, creating it when it does not exist yet."""
    namespace = "ray_hotel"
    try:
        return ray.get_actor(actor_name, namespace=namespace)
    except ValueError:
        opts = {"name": actor_name, "namespace": namespace, "lifetime": "detached"}
        if spread:
            opts["scheduling_strategy"] = "SPREAD"
        return actor_cls.options(**opts).remote(*args)


def _seed_data(admin_actor):
    """Seed demo hotels through the admin actor for a useful local startup."""
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
            ),
            timeout=RAY_CALL_TIMEOUT_S,
        )


def _create_access_token(username: str, role: str) -> str:
    """Create a short-lived JWT carrying the username and role."""
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRES_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_http_bearer)) -> dict:
    """Decode the bearer token and return the authenticated user context."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nieprawidlowy token")

    username = payload.get("sub")
    role = payload.get("role")
    if not username or not role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nieprawidlowy token")

    return {"username": username, "role": role}


def _require_role(required_role: str):
    """Build a FastAPI dependency that allows only the requested role."""
    def _guard(user: dict = Depends(_get_current_user)) -> dict:
        """Validate a decoded user against the required role."""
        if user["role"] != required_role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnien")
        return user

    return _guard


async def _snapshot_loop(app: FastAPI):
    """Periodically persist hotel inventory snapshots from Ray actors."""
    while True:
        try:
            ray.get(app.state.inventory.snapshot_all.remote(), timeout=RAY_CALL_TIMEOUT_S)
        except Exception:
            pass
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create infrastructure resources and Ray actors for the API lifespan."""
    init_db()
    _init_ray()

    inventory = _get_or_create_named_actor(InventoryActor, "inventory", spread=True)
    payment = _get_or_create_named_actor(PaymentActor, "payment", spread=True)
    history = _get_or_create_named_actor(ReservationHistoryActor, "history", spread=True)
    audit_log = _get_or_create_named_actor(AuditLogActor, "audit_log", spread=True)
    metrics = _get_or_create_named_actor(MetricsActor, "metrics", spread=True)
    coordinator = _get_or_create_named_actor(BookingCoordinatorActor, "booking_coordinator", inventory, payment, history, audit_log, metrics, spread=True)
    admin = _get_or_create_named_actor(AdminActor, "admin", inventory, audit_log, spread=True)

    # Seed test data for a quicker local demo.
    _seed_data(admin)

    app.state.inventory = inventory
    app.state.payment = payment
    app.state.history = history
    app.state.coordinator = coordinator
    app.state.admin = admin
    app.state.audit_log = audit_log
    app.state.metrics = metrics
    snapshot_task = asyncio.create_task(_snapshot_loop(app))
    yield
    snapshot_task.cancel()
    with suppress(asyncio.CancelledError):
        await snapshot_task


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


@app.middleware("http")
async def _track_http_metrics(request: Request, call_next):
    """Record every HTTP response in the Prometheus metrics actor."""
    response = await call_next(request)
    try:
        app.state.metrics.inc_http_request.remote(
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code,
        )
    except Exception:
        pass
    return response


@app.get("/health")
def health_check():
    """Return process and Ray cluster health information for probes."""
    cluster_info = {}
    try:
        nodes = ray.nodes()
        cluster_info = {
            "total_nodes": len(nodes),
            "alive_nodes": sum(1 for n in nodes if n.get("Alive")),
        }
    except Exception:
        pass
    return {
        "status": "ok",
        "ray_initialized": ray.is_initialized(),
        "cluster": cluster_info,
        "total_nodes": cluster_info.get("total_nodes", 0),
        "alive_nodes": cluster_info.get("alive_nodes", 0),
    }


@app.get("/metrics")
def metrics_endpoint():
    """Expose application metrics in Prometheus text format."""
    try:
        data = ray.get(app.state.metrics.get_metrics.remote(), timeout=RAY_CALL_TIMEOUT_S)
    except Exception:
        data = b""
    return Response(content=data, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    """Authenticate a demo user and return a bearer token."""
    user = _FAKE_USERS.get(payload.username)
    if not user or user["password"] != payload.password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bledne dane logowania")
    token = _create_access_token(payload.username, user["role"])
    app.state.audit_log.log.remote(
        event_type="LOGIN",
        actor_id=payload.username,
        entity_id=payload.username,
        details={"role": user["role"]},
    )
    return LoginResponse(access_token=token)


@app.post("/hotels/search")
def search_hotels(payload: SearchRequest):
    """Search hotels using optional city, price and room type filters."""
    try:
        return ray.get(
            app.state.inventory.search_hotels.remote(
                city=payload.city,
                max_price=payload.max_price,
                room_type=payload.room_type,
            ),
            timeout=RAY_CALL_TIMEOUT_S,
        )
    except ray.exceptions.GetTimeoutError:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timeout wyszukiwania hoteli")


@app.post("/reservations", response_model=ReservationResponse)
def create_reservation(payload: ReservationCreateRequest, user: dict = Depends(_get_current_user)):
    """Create a reservation through the booking coordinator actor."""
    if payload.user_id != user["username"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnien")
    try:
        result = ray.get(
            app.state.coordinator.book_room.remote(
                user_id=payload.user_id,
                hotel_id=payload.hotel_id,
                room_type=payload.room_type,
                nights=payload.nights,
                payment_method=payload.payment_method,
                idempotency_key=payload.idempotency_key,
            ),
            timeout=RAY_CALL_TIMEOUT_S,
        )
    except ray.exceptions.GetTimeoutError:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timeout rezerwacji")
    return ReservationResponse(**result)


@app.post("/reservations/cancel", response_model=ReservationResponse)
def cancel_reservation(payload: ReservationCancelRequest, user: dict = Depends(_get_current_user)):
    """Cancel a confirmed reservation owned by the authenticated user."""
    if payload.user_id != user["username"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnien")
    try:
        result = ray.get(
            app.state.coordinator.cancel_booking.remote(
                user_id=payload.user_id,
                reservation_id=payload.reservation_id,
            ),
            timeout=RAY_CALL_TIMEOUT_S,
        )
    except ray.exceptions.GetTimeoutError:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timeout anulacji")
    return ReservationResponse(**result)


@app.get("/users/{user_id}/reservations", response_model=UserReservationsResponse)
def user_reservations(user_id: str, user: dict = Depends(_get_current_user)):
    """Return the authenticated user's reservation history."""
    if user_id != user["username"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnien")
    try:
        reservations = ray.get(app.state.history.list_user_reservations.remote(user_id=user_id), timeout=RAY_CALL_TIMEOUT_S)
    except ray.exceptions.GetTimeoutError:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timeout pobierania rezerwacji")
    return UserReservationsResponse(user_id=user_id, reservations=reservations)


@app.post("/admin/hotels")
def upsert_hotel(payload: HotelUpsertRequest, user: dict = Depends(_require_role("admin"))):
    """Create or update a hotel offer; available only to administrators."""
    rooms = {key: value.model_dump() for key, value in payload.rooms.items()}
    try:
        return ray.get(
            app.state.admin.upsert_hotel.remote(
                hotel_id=payload.hotel_id,
                name=payload.name,
                city=payload.city,
                rooms=rooms,
            ),
            timeout=RAY_CALL_TIMEOUT_S,
        )
    except ray.exceptions.GetTimeoutError:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timeout operacji na hotelu")


@app.get("/admin/audit-logs", response_model=AuditLogsResponse)
def get_audit_logs(
    event_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 100,
    user: dict = Depends(_require_role("admin")),
):
    """List audit log entries with optional filters for administrators."""
    try:
        entries = ray.get(
            app.state.audit_log.list_logs.remote(
                event_type=event_type,
                actor_id=actor_id,
                entity_id=entity_id,
                limit=limit,
            ),
            timeout=RAY_CALL_TIMEOUT_S,
        )
    except ray.exceptions.GetTimeoutError:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timeout pobierania audit logow")
    return AuditLogsResponse(entries=entries)