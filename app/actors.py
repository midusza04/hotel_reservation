"""Ray actors implementing the distributed hotel reservation domain.

The actors split responsibility between hotel inventory, booking orchestration,
payments, user history, metrics, audit logging and administration.  Each actor
owns its own state, which matches Ray's actor model and avoids shared memory
between distributed components.
"""

from collections import defaultdict
import logging
import time
from typing import Dict, List, Optional
from uuid import uuid4

import ray
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from db import (
    load_hotels,
    load_reservations,
    save_hotel_snapshot,
    save_reservation,
    update_reservation_status,
    write_audit_log,
    load_audit_logs,
)

logger = logging.getLogger(__name__)

HOLD_TTL_SECONDS = 300
RAY_CALL_TIMEOUT_S = 10
MAX_RETRIES = 3
BACKOFF_BASE_S = 0.3


def _ray_call(ref, *, timeout: float = RAY_CALL_TIMEOUT_S, retries: int = MAX_RETRIES):
    """Execute ray.get with timeout and exponential-backoff retry on transient errors."""
    last_exc = None
    for attempt in range(retries):
        try:
            return ray.get(ref, timeout=timeout)
        except ray.exceptions.GetTimeoutError:
            raise
        except (ray.exceptions.RayActorError, ray.exceptions.RayTaskError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                wait = BACKOFF_BASE_S * (2 ** attempt)
                logger.warning("Ray call failed (attempt %d/%d), retrying in %.1fs: %s", attempt + 1, retries, wait, exc)
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


@ray.remote(num_cpus=0.25)
class HotelActor:
    """Owns the state and concurrency boundary for a single hotel offer."""

    def __init__(self, hotel_id: str, name: str, city: str, rooms: Dict[str, dict]):
        """Create a hotel actor with room availability and in-memory holds."""
        self.hotel_id = hotel_id
        self.name = name
        self.city = city
        self.rooms = rooms
        self.holds = {}
        self.reservations = {}

    def set_offer(self, name: str, city: str, rooms: Dict[str, dict]):
        """Replace the public hotel offer and persist a fresh snapshot."""
        self.name = name
        self.city = city
        self.rooms = rooms
        self._persist_snapshot()
        return {"ok": True}

    def _cleanup_expired_holds(self):
        """Release room holds whose TTL has elapsed."""
        now = time.time()
        expired_ids = [
            hold_id for hold_id, hold in self.holds.items() if hold["expires_at"] <= now
        ]
        for hold_id in expired_ids:
            hold = self.holds.pop(hold_id)
            self.rooms[hold["room_type"]]["available"] += 1
        if expired_ids:
            self._persist_snapshot()

    def _snapshot_rooms(self) -> Dict[str, dict]:
        """Return room availability as if temporary holds were not reserved."""
        holds_by_room = defaultdict(int)
        for hold in self.holds.values():
            holds_by_room[hold["room_type"]] += 1

        snapshot = {}
        for room_type, room in self.rooms.items():
            available = room["available"] + holds_by_room.get(room_type, 0)
            snapshot[room_type] = {"available": available, "price": room["price"]}
        return snapshot

    def _persist_snapshot(self):
        """Persist the current hotel state to the database snapshot table."""
        save_hotel_snapshot(
            hotel_id=self.hotel_id,
            name=self.name,
            city=self.city,
            rooms=self._snapshot_rooms(),
        )

    def get_snapshot(self) -> dict:
        """Return a database-friendly snapshot of this hotel actor."""
        return {
            "hotel_id": self.hotel_id,
            "name": self.name,
            "city": self.city,
            "rooms": self._snapshot_rooms(),
        }

    def matches(self, city: Optional[str], max_price: Optional[float], room_type: Optional[str]):
        """Check whether this hotel matches the provided search filters."""
        self._cleanup_expired_holds()
        if city and self.city.lower() != city.lower():
            return False
        if room_type:
            room = self.rooms.get(room_type)
            if not room or room["available"] <= 0:
                return False
            if max_price is not None and room["price"] > max_price:
                return False
            return True
        if max_price is not None:
            return any(room["price"] <= max_price and room["available"] > 0 for room in self.rooms.values())
        return True

    def get_offer(self):
        """Return the public hotel offer for API search results."""
        self._cleanup_expired_holds()
        active_prices = [room["price"] for room in self.rooms.values() if room["available"] > 0]
        return {
            "hotel_id": self.hotel_id,
            "name": self.name,
            "city": self.city,
            "rooms": self.rooms,
            "min_price": min(active_prices) if active_prices else None,
        }

    def try_hold(self, room_type: str, user_id: str, nights: int):
        """Reserve one room temporarily before payment is processed."""
        self._cleanup_expired_holds()
        room = self.rooms.get(room_type)
        if not room:
            return {"ok": False, "message": "Nieznany typ pokoju"}
        if room["available"] <= 0:
            return {"ok": False, "message": "Brak dostepnych pokoi"}

        room["available"] -= 1
        hold_id = str(uuid4())
        total_price = room["price"] * nights
        self.holds[hold_id] = {
            "hold_id": hold_id,
            "user_id": user_id,
            "room_type": room_type,
            "nights": nights,
            "total_price": total_price,
            "expires_at": time.time() + HOLD_TTL_SECONDS,
        }
        return {
            "ok": True,
            "hotel_id": self.hotel_id,
            "hold_id": hold_id,
            "room_type": room_type,
            "nights": nights,
            "total_price": total_price,
        }

    def release_hold(self, hold_id: str):
        """Release a temporary hold and restore room availability."""
        self._cleanup_expired_holds()
        hold = self.holds.pop(hold_id, None)
        if not hold:
            return {"ok": False, "message": "Hold nie istnieje"}
        self.rooms[hold["room_type"]]["available"] += 1
        return {"ok": True}

    def confirm_hold(self, hold_id: str):
        """Convert a valid hold into a confirmed hotel-local reservation."""
        self._cleanup_expired_holds()
        hold = self.holds.pop(hold_id, None)
        if not hold:
            return {"ok": False, "message": "Hold wygasl lub nie istnieje"}

        reservation_id = str(uuid4())
        reservation = {
            "reservation_id": reservation_id,
            "hotel_id": self.hotel_id,
            "room_type": hold["room_type"],
            "nights": hold["nights"],
            "total_price": hold["total_price"],
            "status": "confirmed",
        }
        self.reservations[reservation_id] = reservation
        self._persist_snapshot()
        return {"ok": True, **reservation}

    def cancel_reservation(self, reservation_id: str):
        """Cancel a hotel-local reservation and return the room to inventory."""
        reservation = self.reservations.get(reservation_id)
        if not reservation:
            return {"ok": False, "message": "Nie znaleziono rezerwacji"}
        if reservation["status"] == "cancelled":
            return {"ok": False, "message": "Rezerwacja juz anulowana"}

        reservation["status"] = "cancelled"
        self.rooms[reservation["room_type"]]["available"] += 1
        self._persist_snapshot()
        return {"ok": True, "reservation_id": reservation_id}


@ray.remote(num_cpus=0.5)
class InventoryActor:
    """Registry and facade for all hotel actors in the system."""

    def __init__(self):
        """Restore hotel actors from database snapshots on startup."""
        self.hotels = {}
        for hotel in load_hotels():
            self.hotels[hotel["hotel_id"]] = HotelActor.options(
                scheduling_strategy="SPREAD",
            ).remote(
                hotel_id=hotel["hotel_id"],
                name=hotel["name"],
                city=hotel["city"],
                rooms=hotel["rooms"],
            )

    def snapshot_all(self):
        """Persist snapshots for all known hotel actors."""
        for hotel in self.hotels.values():
            snapshot = _ray_call(hotel.get_snapshot.remote())
            save_hotel_snapshot(
                hotel_id=snapshot["hotel_id"],
                name=snapshot["name"],
                city=snapshot["city"],
                rooms=snapshot["rooms"],
            )

    def upsert_hotel(self, hotel_id: str, name: str, city: str, rooms: Dict[str, dict]):
        """Create a new hotel actor or update an existing hotel offer."""
        if hotel_id in self.hotels:
            _ray_call(self.hotels[hotel_id].set_offer.remote(name=name, city=city, rooms=rooms))
            save_hotel_snapshot(hotel_id=hotel_id, name=name, city=city, rooms=rooms)
            return {"ok": True, "message": "Hotel zaktualizowany", "hotel_id": hotel_id}

        self.hotels[hotel_id] = HotelActor.options(
            scheduling_strategy="SPREAD",
        ).remote(hotel_id=hotel_id, name=name, city=city, rooms=rooms)
        save_hotel_snapshot(hotel_id=hotel_id, name=name, city=city, rooms=rooms)
        return {"ok": True, "message": "Hotel dodany", "hotel_id": hotel_id}

    def search_hotels(self, city: Optional[str], max_price: Optional[float], room_type: Optional[str]):
        """Find hotel offers matching optional search filters."""
        matches = []
        for hotel in self.hotels.values():
            if _ray_call(hotel.matches.remote(city=city, max_price=max_price, room_type=room_type)):
                matches.append(_ray_call(hotel.get_offer.remote()))
        return matches

    def hold_room(self, hotel_id: str, room_type: str, user_id: str, nights: int):
        """Delegate temporary room reservation to the selected hotel actor."""
        hotel = self.hotels.get(hotel_id)
        if not hotel:
            return {"ok": False, "message": "Hotel nie istnieje"}
        return _ray_call(hotel.try_hold.remote(room_type=room_type, user_id=user_id, nights=nights))

    def release_hold(self, hotel_id: str, hold_id: str):
        """Delegate hold release to the selected hotel actor."""
        hotel = self.hotels.get(hotel_id)
        if not hotel:
            return {"ok": False, "message": "Hotel nie istnieje"}
        return _ray_call(hotel.release_hold.remote(hold_id=hold_id))

    def confirm_hold(self, hotel_id: str, hold_id: str):
        """Delegate hold confirmation to the selected hotel actor."""
        hotel = self.hotels.get(hotel_id)
        if not hotel:
            return {"ok": False, "message": "Hotel nie istnieje"}
        return _ray_call(hotel.confirm_hold.remote(hold_id=hold_id))

    def cancel_reservation(self, hotel_id: str, reservation_id: str):
        """Delegate reservation cancellation to the selected hotel actor."""
        hotel = self.hotels.get(hotel_id)
        if not hotel:
            return {"ok": False, "message": "Hotel nie istnieje"}
        return _ray_call(hotel.cancel_reservation.remote(reservation_id=reservation_id))


@ray.remote(num_cpus=0.1)
class PaymentActor:
    """Payment gateway simulator used by the booking workflow."""

    def process_payment(self, user_id: str, amount: float, payment_method: str):
        """Approve a payment unless the request uses an invalid method or amount."""
        if amount <= 0:
            return {"ok": False, "message": "Nieprawidlowa kwota"}
        if payment_method.lower() == "reject":
            return {"ok": False, "message": "Platnosc odrzucona"}
        return {
            "ok": True,
            "payment_id": str(uuid4()),
            "user_id": user_id,
            "amount": amount,
            "payment_method": payment_method,
        }

    def refund_payment(self, payment_id: str, amount: float, reason: str = ""):
        """Issue a full refund for a previously processed payment."""
        if not payment_id:
            return {"ok": False, "message": "Brak payment_id"}
        return {
            "ok": True,
            "refund_id": str(uuid4()),
            "payment_id": payment_id,
            "refunded_amount": amount,
            "reason": reason,
        }


@ray.remote(num_cpus=0.25)
class ReservationHistoryActor:
    """Stores reservation history indexed by user and reservation id."""

    def __init__(self):
        """Load persisted reservations into actor memory."""
        self.by_user = defaultdict(list)
        self.by_id = {}
        for reservation in load_reservations():
            self.by_user[reservation["user_id"]].append(reservation)
            self.by_id[reservation["reservation_id"]] = reservation

    def add_reservation(self, reservation: dict):
        """Add a confirmed reservation to memory and durable storage."""
        self.by_user[reservation["user_id"]].append(reservation)
        self.by_id[reservation["reservation_id"]] = reservation
        save_reservation(reservation)
        return {"ok": True}

    def cancel_reservation(self, reservation_id: str):
        """Mark a reservation as cancelled in the in-memory history."""
        reservation = self.by_id.get(reservation_id)
        if not reservation:
            return {"ok": False, "message": "Rezerwacja nie istnieje"}
        reservation["status"] = "cancelled"
        return {"ok": True}

    def list_user_reservations(self, user_id: str):
        """Return all reservations known for a user."""
        return self.by_user.get(user_id, [])


@ray.remote(num_cpus=0.5)
class BookingCoordinatorActor:
    """Orchestrates the reservation saga across inventory, payment and history."""

    def __init__(self, inventory, payment, history, audit_log, metrics):
        """Wire dependent actors and restore persisted coordinator state."""
        self.inventory = inventory
        self.payment = payment
        self.history = history
        self.audit_log = audit_log
        self.metrics = metrics
        self.reservations = {}
        self.idempotency = {}
        for reservation in load_reservations():
            self.reservations[reservation["reservation_id"]] = reservation

    def _cache_idempotent(self, user_id: str, idempotency_key: Optional[str], response: dict):
        """Cache a booking response under a user-scoped idempotency key."""
        if idempotency_key:
            self.idempotency[(user_id, idempotency_key)] = response

    def book_room(
        self,
        user_id: str,
        hotel_id: str,
        room_type: str,
        nights: int,
        payment_method: str,
        idempotency_key: Optional[str] = None,
    ):
        """Run the end-to-end booking flow with rollback on payment failure."""
        if idempotency_key:
            cached = self.idempotency.get((user_id, idempotency_key))
            if cached:
                return cached

        _t0 = time.time()

        hold = _ray_call(
            self.inventory.hold_room.remote(
                hotel_id=hotel_id,
                room_type=room_type,
                user_id=user_id,
                nights=nights,
            )
        )
        if not hold["ok"]:
            response = {"ok": False, "message": hold["message"]}
            self._cache_idempotent(user_id, idempotency_key, response)
            self.metrics.inc_reservation.remote(status="failed")
            return response

        self.metrics.set_active_holds.remote(
            sum(1 for r in self.reservations.values() if r.get("status") == "confirmed") + 1
        )
        self.audit_log.log.remote(
            event_type="HOLD_CREATED",
            actor_id=user_id,
            entity_id=hold["hold_id"],
            details={"hotel_id": hotel_id, "room_type": room_type, "nights": nights, "total_price": hold["total_price"]},
        )

        payment = _ray_call(
            self.payment.process_payment.remote(
                user_id=user_id,
                amount=hold["total_price"],
                payment_method=payment_method,
            )
        )
        if not payment["ok"]:
            _ray_call(self.inventory.release_hold.remote(hotel_id=hotel_id, hold_id=hold["hold_id"]))
            self.metrics.inc_payment.remote(status="failed")
            self.metrics.inc_reservation.remote(status="failed")
            self.audit_log.log.remote(
                event_type="PAYMENT_FAILED",
                actor_id=user_id,
                entity_id=hold["hold_id"],
                details={"hotel_id": hotel_id, "amount": hold["total_price"], "reason": payment["message"]},
            )
            self.audit_log.log.remote(
                event_type="HOLD_RELEASED",
                actor_id=user_id,
                entity_id=hold["hold_id"],
                details={"hotel_id": hotel_id, "reason": "payment_failed"},
            )
            response = {"ok": False, "message": payment["message"]}
            self._cache_idempotent(user_id, idempotency_key, response)
            return response

        self.metrics.inc_payment.remote(status="success")
        self.audit_log.log.remote(
            event_type="PAYMENT_SUCCESS",
            actor_id=user_id,
            entity_id=payment["payment_id"],
            details={"amount": payment["amount"], "payment_method": payment_method},
        )

        confirmed = _ray_call(self.inventory.confirm_hold.remote(hotel_id=hotel_id, hold_id=hold["hold_id"]))
        if not confirmed["ok"]:
            # Payment already charged — issue a full refund before returning error.
            try:
                _ray_call(
                    self.payment.refund_payment.remote(
                        payment_id=payment["payment_id"],
                        amount=hold["total_price"],
                        reason="confirm_hold_failed",
                    )
                )
                self.audit_log.log.remote(
                    event_type="PAYMENT_REFUNDED",
                    actor_id=user_id,
                    entity_id=payment["payment_id"],
                    details={"amount": hold["total_price"], "reason": "confirm_hold_failed", "hold_id": hold["hold_id"]},
                )
            except Exception as refund_exc:
                logger.error(
                    "CRITICAL: payment %s charged but refund failed after confirm_hold error: %s",
                    payment["payment_id"],
                    refund_exc,
                )
                self.audit_log.log.remote(
                    event_type="REFUND_FAILED",
                    actor_id=user_id,
                    entity_id=payment["payment_id"],
                    details={"amount": hold["total_price"], "reason": str(refund_exc)},
                )
            response = {"ok": False, "message": confirmed["message"]}
            self._cache_idempotent(user_id, idempotency_key, response)
            return response

        self.audit_log.log.remote(
            event_type="HOLD_CONFIRMED",
            actor_id=user_id,
            entity_id=hold["hold_id"],
            details={"hotel_id": hotel_id, "reservation_id": confirmed["reservation_id"]},
        )

        reservation = {
            "reservation_id": confirmed["reservation_id"],
            "user_id": user_id,
            "hotel_id": hotel_id,
            "room_type": room_type,
            "nights": nights,
            "total_price": confirmed["total_price"],
            "payment_id": payment["payment_id"],
            "status": "confirmed",
            "created_at": time.time(),
        }
        self.reservations[reservation["reservation_id"]] = reservation
        _ray_call(self.history.add_reservation.remote(reservation))
        save_reservation(reservation)

        self.audit_log.log.remote(
            event_type="RESERVATION_CREATED",
            actor_id=user_id,
            entity_id=reservation["reservation_id"],
            details={
                "hotel_id": hotel_id,
                "room_type": room_type,
                "nights": nights,
                "total_price": reservation["total_price"],
                "payment_id": reservation["payment_id"],
            },
        )

        self.metrics.inc_reservation.remote(status="confirmed")
        self.metrics.observe_booking_duration.remote(time.time() - _t0)

        response = {
            "ok": True,
            "message": "Rezerwacja potwierdzona",
            "reservation_id": reservation["reservation_id"],
            "payment_id": reservation["payment_id"],
            "total_price": reservation["total_price"],
        }
        self._cache_idempotent(user_id, idempotency_key, response)
        return response

    def cancel_booking(self, user_id: str, reservation_id: str):
        """Cancel a reservation and calculate the refund policy result."""
        reservation = self.reservations.get(reservation_id)
        if not reservation:
            return {"ok": False, "message": "Nie znaleziono rezerwacji"}
        if reservation["user_id"] != user_id:
            return {"ok": False, "message": "Brak uprawnien"}
        if reservation["status"] == "cancelled":
            return {"ok": False, "message": "Rezerwacja juz anulowana"}

        _t0 = time.time()

        cancelled = _ray_call(
            self.inventory.cancel_reservation.remote(
                hotel_id=reservation["hotel_id"],
                reservation_id=reservation_id,
            )
        )
        if not cancelled["ok"]:
            self.metrics.inc_cancellation.remote(status="failed")
            return {"ok": False, "message": cancelled["message"]}

        now = time.time()
        refund_percent = 100 if now - reservation["created_at"] <= 3600 else 0
        refund_amount = round(reservation["total_price"] * (refund_percent / 100), 2)

        reservation["status"] = "cancelled"
        reservation["refund_percent"] = refund_percent
        reservation["refund_amount"] = refund_amount
        _ray_call(self.history.cancel_reservation.remote(reservation_id=reservation_id))
        update_reservation_status(reservation_id, "cancelled", refund_percent, refund_amount)

        self.metrics.inc_cancellation.remote(status="success")
        self.metrics.observe_cancellation_duration.remote(time.time() - _t0)

        self.audit_log.log.remote(
            event_type="RESERVATION_CANCELLED",
            actor_id=user_id,
            entity_id=reservation_id,
            details={
                "hotel_id": reservation["hotel_id"],
                "refund_percent": refund_percent,
                "refund_amount": refund_amount,
            },
        )

        return {
            "ok": True,
            "message": "Rezerwacja anulowana",
            "reservation_id": reservation_id,
            "refund_percent": refund_percent,
            "refund_amount": refund_amount,
        }


@ray.remote(num_cpus=0.1)
class MetricsActor:
    """Single-instance actor that owns all Prometheus metrics state."""

    def __init__(self):
        """Create an isolated Prometheus registry for application metrics."""
        self._registry = CollectorRegistry()

        self.reservations_total = Counter(
            "hotel_reservations_total",
            "Total reservation attempts",
            ["status"],  # confirmed | failed
            registry=self._registry,
        )
        self.cancellations_total = Counter(
            "hotel_cancellations_total",
            "Total cancellation attempts",
            ["status"],  # success | failed
            registry=self._registry,
        )
        self.payments_total = Counter(
            "hotel_payments_total",
            "Total payment attempts",
            ["status"],  # success | failed
            registry=self._registry,
        )
        self.active_holds = Gauge(
            "hotel_active_holds",
            "Currently active (non-expired) holds",
            registry=self._registry,
        )
        self.booking_duration = Histogram(
            "hotel_booking_duration_seconds",
            "End-to-end booking flow duration",
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self._registry,
        )
        self.cancellation_duration = Histogram(
            "hotel_cancellation_duration_seconds",
            "End-to-end cancellation flow duration",
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
            registry=self._registry,
        )
        self.http_requests_total = Counter(
            "hotel_http_requests_total",
            "Total HTTP requests handled",
            ["method", "endpoint", "status_code"],
            registry=self._registry,
        )
        self.ray_call_retries_total = Counter(
            "hotel_ray_call_retries_total",
            "Total Ray inter-actor call retries",
            registry=self._registry,
        )

    def inc_reservation(self, status: str):
        """Increment reservation attempts by status."""
        self.reservations_total.labels(status=status).inc()

    def inc_cancellation(self, status: str):
        """Increment cancellation attempts by status."""
        self.cancellations_total.labels(status=status).inc()

    def inc_payment(self, status: str):
        """Increment payment attempts by status."""
        self.payments_total.labels(status=status).inc()

    def set_active_holds(self, count: int):
        """Set the gauge for currently active room holds."""
        self.active_holds.set(count)

    def observe_booking_duration(self, seconds: float):
        """Record booking workflow duration in seconds."""
        self.booking_duration.observe(seconds)

    def observe_cancellation_duration(self, seconds: float):
        """Record cancellation workflow duration in seconds."""
        self.cancellation_duration.observe(seconds)

    def inc_http_request(self, method: str, endpoint: str, status_code: int):
        """Increment HTTP request count by method, path and status code."""
        self.http_requests_total.labels(
            method=method, endpoint=endpoint, status_code=str(status_code)
        ).inc()

    def inc_ray_retry(self):
        """Increment the counter for retried Ray calls."""
        self.ray_call_retries_total.inc()

    def get_metrics(self) -> bytes:
        """Serialize all metrics in Prometheus exposition format."""
        return generate_latest(self._registry)


@ray.remote(num_cpus=0.1)
class AuditLogActor:
    """Writes and queries durable audit events for domain operations."""

    def log(
        self,
        event_type: str,
        actor_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        details: Optional[dict] = None,
    ):
        """Persist a single audit event with optional actor and entity context."""
        entry = {
            "event_id": str(uuid4()),
            "event_type": event_type,
            "actor_id": actor_id,
            "entity_id": entity_id,
            "details": details or {},
            "occurred_at": time.time(),
        }
        write_audit_log(entry)
        return {"ok": True}

    def list_logs(
        self,
        event_type: Optional[str] = None,
        actor_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        limit: int = 100,
    ):
        """Load recent audit log entries using optional filters."""
        return load_audit_logs(
            event_type=event_type,
            actor_id=actor_id,
            entity_id=entity_id,
            limit=limit,
        )


@ray.remote(num_cpus=0.1)
class AdminActor:
    """Administrative facade for changing hotel offers."""

    def __init__(self, inventory, audit_log):
        """Store dependencies required for administrative operations."""
        self.inventory = inventory
        self.audit_log = audit_log

    def upsert_hotel(self, hotel_id: str, name: str, city: str, rooms: Dict[str, dict]):
        """Create or update a hotel and emit an audit event."""
        result = _ray_call(
            self.inventory.upsert_hotel.remote(
                hotel_id=hotel_id,
                name=name,
                city=city,
                rooms=rooms,
            )
        )
        self.audit_log.log.remote(
            event_type="HOTEL_UPSERTED",
            actor_id="admin",
            entity_id=hotel_id,
            details={"name": name, "city": city, "action": result.get("message")},
        )
        return result
