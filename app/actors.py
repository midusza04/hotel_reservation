from collections import defaultdict
import time
from typing import Dict, Optional
from uuid import uuid4

import ray

from db import (
    load_hotels,
    load_reservations,
    save_hotel_snapshot,
    save_reservation,
    update_reservation_status,
)

HOLD_TTL_SECONDS = 300


@ray.remote
class HotelActor:
    def __init__(self, hotel_id: str, name: str, city: str, rooms: Dict[str, dict]):
        self.hotel_id = hotel_id
        self.name = name
        self.city = city
        self.rooms = rooms
        self.holds = {}
        self.reservations = {}

    def set_offer(self, name: str, city: str, rooms: Dict[str, dict]):
        self.name = name
        self.city = city
        self.rooms = rooms
        self._persist_snapshot()
        return {"ok": True}

    def _cleanup_expired_holds(self):
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
        holds_by_room = defaultdict(int)
        for hold in self.holds.values():
            holds_by_room[hold["room_type"]] += 1

        snapshot = {}
        for room_type, room in self.rooms.items():
            available = room["available"] + holds_by_room.get(room_type, 0)
            snapshot[room_type] = {"available": available, "price": room["price"]}
        return snapshot

    def _persist_snapshot(self):
        save_hotel_snapshot(
            hotel_id=self.hotel_id,
            name=self.name,
            city=self.city,
            rooms=self._snapshot_rooms(),
        )

    def get_snapshot(self) -> dict:
        return {
            "hotel_id": self.hotel_id,
            "name": self.name,
            "city": self.city,
            "rooms": self._snapshot_rooms(),
        }

    def matches(self, city: Optional[str], max_price: Optional[float], room_type: Optional[str]):
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
        self._cleanup_expired_holds()
        hold = self.holds.pop(hold_id, None)
        if not hold:
            return {"ok": False, "message": "Hold nie istnieje"}
        self.rooms[hold["room_type"]]["available"] += 1
        return {"ok": True}

    def confirm_hold(self, hold_id: str):
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
        reservation = self.reservations.get(reservation_id)
        if not reservation:
            return {"ok": False, "message": "Nie znaleziono rezerwacji"}
        if reservation["status"] == "cancelled":
            return {"ok": False, "message": "Rezerwacja juz anulowana"}

        reservation["status"] = "cancelled"
        self.rooms[reservation["room_type"]]["available"] += 1
        self._persist_snapshot()
        return {"ok": True, "reservation_id": reservation_id}


@ray.remote
class InventoryActor:
    def __init__(self):
        self.hotels = {}
        for hotel in load_hotels():
            self.hotels[hotel["hotel_id"]] = HotelActor.remote(
                hotel_id=hotel["hotel_id"],
                name=hotel["name"],
                city=hotel["city"],
                rooms=hotel["rooms"],
            )

    def snapshot_all(self):
        for hotel in self.hotels.values():
            snapshot = ray.get(hotel.get_snapshot.remote())
            save_hotel_snapshot(
                hotel_id=snapshot["hotel_id"],
                name=snapshot["name"],
                city=snapshot["city"],
                rooms=snapshot["rooms"],
            )

    def upsert_hotel(self, hotel_id: str, name: str, city: str, rooms: Dict[str, dict]):
        if hotel_id in self.hotels:
            ray.get(self.hotels[hotel_id].set_offer.remote(name=name, city=city, rooms=rooms))
            save_hotel_snapshot(hotel_id=hotel_id, name=name, city=city, rooms=rooms)
            return {"ok": True, "message": "Hotel zaktualizowany", "hotel_id": hotel_id}

        self.hotels[hotel_id] = HotelActor.remote(hotel_id=hotel_id, name=name, city=city, rooms=rooms)
        save_hotel_snapshot(hotel_id=hotel_id, name=name, city=city, rooms=rooms)
        return {"ok": True, "message": "Hotel dodany", "hotel_id": hotel_id}

    def search_hotels(self, city: Optional[str], max_price: Optional[float], room_type: Optional[str]):
        matches = []
        for hotel in self.hotels.values():
            if ray.get(hotel.matches.remote(city=city, max_price=max_price, room_type=room_type)):
                matches.append(ray.get(hotel.get_offer.remote()))
        return matches

    def hold_room(self, hotel_id: str, room_type: str, user_id: str, nights: int):
        hotel = self.hotels.get(hotel_id)
        if not hotel:
            return {"ok": False, "message": "Hotel nie istnieje"}
        return ray.get(hotel.try_hold.remote(room_type=room_type, user_id=user_id, nights=nights))

    def release_hold(self, hotel_id: str, hold_id: str):
        hotel = self.hotels.get(hotel_id)
        if not hotel:
            return {"ok": False, "message": "Hotel nie istnieje"}
        return ray.get(hotel.release_hold.remote(hold_id=hold_id))

    def confirm_hold(self, hotel_id: str, hold_id: str):
        hotel = self.hotels.get(hotel_id)
        if not hotel:
            return {"ok": False, "message": "Hotel nie istnieje"}
        return ray.get(hotel.confirm_hold.remote(hold_id=hold_id))

    def cancel_reservation(self, hotel_id: str, reservation_id: str):
        hotel = self.hotels.get(hotel_id)
        if not hotel:
            return {"ok": False, "message": "Hotel nie istnieje"}
        return ray.get(hotel.cancel_reservation.remote(reservation_id=reservation_id))


@ray.remote
class PaymentActor:
    def process_payment(self, user_id: str, amount: float, payment_method: str):
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


@ray.remote
class ReservationHistoryActor:
    def __init__(self):
        self.by_user = defaultdict(list)
        self.by_id = {}
        for reservation in load_reservations():
            self.by_user[reservation["user_id"]].append(reservation)
            self.by_id[reservation["reservation_id"]] = reservation

    def add_reservation(self, reservation: dict):
        self.by_user[reservation["user_id"]].append(reservation)
        self.by_id[reservation["reservation_id"]] = reservation
        save_reservation(reservation)
        return {"ok": True}

    def cancel_reservation(self, reservation_id: str):
        reservation = self.by_id.get(reservation_id)
        if not reservation:
            return {"ok": False, "message": "Rezerwacja nie istnieje"}
        reservation["status"] = "cancelled"
        return {"ok": True}

    def list_user_reservations(self, user_id: str):
        return self.by_user.get(user_id, [])


@ray.remote
class BookingCoordinatorActor:
    def __init__(self, inventory, payment, history):
        self.inventory = inventory
        self.payment = payment
        self.history = history
        self.reservations = {}
        self.idempotency = {}
        for reservation in load_reservations():
            self.reservations[reservation["reservation_id"]] = reservation

    def _cache_idempotent(self, user_id: str, idempotency_key: Optional[str], response: dict):
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
        if idempotency_key:
            cached = self.idempotency.get((user_id, idempotency_key))
            if cached:
                return cached

        hold = ray.get(
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
            return response

        payment = ray.get(
            self.payment.process_payment.remote(
                user_id=user_id,
                amount=hold["total_price"],
                payment_method=payment_method,
            )
        )
        if not payment["ok"]:
            ray.get(self.inventory.release_hold.remote(hotel_id=hotel_id, hold_id=hold["hold_id"]))
            response = {"ok": False, "message": payment["message"]}
            self._cache_idempotent(user_id, idempotency_key, response)
            return response

        confirmed = ray.get(self.inventory.confirm_hold.remote(hotel_id=hotel_id, hold_id=hold["hold_id"]))
        if not confirmed["ok"]:
            response = {"ok": False, "message": confirmed["message"]}
            self._cache_idempotent(user_id, idempotency_key, response)
            return response

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
        ray.get(self.history.add_reservation.remote(reservation))
        save_reservation(reservation)

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
        reservation = self.reservations.get(reservation_id)
        if not reservation:
            return {"ok": False, "message": "Nie znaleziono rezerwacji"}
        if reservation["user_id"] != user_id:
            return {"ok": False, "message": "Brak uprawnien"}
        if reservation["status"] == "cancelled":
            return {"ok": False, "message": "Rezerwacja juz anulowana"}

        cancelled = ray.get(
            self.inventory.cancel_reservation.remote(
                hotel_id=reservation["hotel_id"],
                reservation_id=reservation_id,
            )
        )
        if not cancelled["ok"]:
            return {"ok": False, "message": cancelled["message"]}

        now = time.time()
        refund_percent = 100 if now - reservation["created_at"] <= 3600 else 0
        refund_amount = round(reservation["total_price"] * (refund_percent / 100), 2)

        reservation["status"] = "cancelled"
        reservation["refund_percent"] = refund_percent
        reservation["refund_amount"] = refund_amount
        ray.get(self.history.cancel_reservation.remote(reservation_id=reservation_id))
        update_reservation_status(reservation_id, "cancelled", refund_percent, refund_amount)
        return {
            "ok": True,
            "message": "Rezerwacja anulowana",
            "reservation_id": reservation_id,
            "refund_percent": refund_percent,
            "refund_amount": refund_amount,
        }


@ray.remote
class AdminActor:
    def __init__(self, inventory):
        self.inventory = inventory

    def upsert_hotel(self, hotel_id: str, name: str, city: str, rooms: Dict[str, dict]):
        return ray.get(
            self.inventory.upsert_hotel.remote(
                hotel_id=hotel_id,
                name=name,
                city=city,
                rooms=rooms,
            )
        )
