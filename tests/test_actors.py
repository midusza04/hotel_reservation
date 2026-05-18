"""
Unit tests for individual Ray actors.

Each test creates a fresh (non-named) remote actor and exercises its
methods directly via ray.get().  No FastAPI / DB / cluster required.
"""

import time
import pytest
import ray


# ---------------------------------------------------------------------------
# HotelActor
# ---------------------------------------------------------------------------
class TestHotelActor:
    @pytest.fixture
    def hotel(self):
        from actors import HotelActor
        rooms = {
            "single": {"available": 3, "price": 100.0},
            "double": {"available": 1, "price": 200.0},
        }
        actor = HotelActor.remote("h-test", "Test Hotel", "Warszawa", rooms)
        yield actor
        ray.kill(actor)

    def test_get_offer_returns_hotel_data(self, hotel):
        offer = ray.get(hotel.get_offer.remote())
        assert offer["hotel_id"] == "h-test"
        assert offer["name"] == "Test Hotel"
        assert offer["city"] == "Warszawa"
        assert "single" in offer["rooms"]

    def test_matches_by_city(self, hotel):
        assert ray.get(hotel.matches.remote(city="Warszawa", max_price=None, room_type=None))
        assert not ray.get(hotel.matches.remote(city="Gdansk", max_price=None, room_type=None))

    def test_matches_by_max_price(self, hotel):
        assert ray.get(hotel.matches.remote(city=None, max_price=150.0, room_type=None))
        assert not ray.get(hotel.matches.remote(city=None, max_price=50.0, room_type=None))

    def test_matches_by_room_type(self, hotel):
        assert ray.get(hotel.matches.remote(city=None, max_price=None, room_type="single"))
        assert not ray.get(hotel.matches.remote(city=None, max_price=None, room_type="suite"))

    def test_try_hold_success(self, hotel):
        result = ray.get(hotel.try_hold.remote(room_type="single", user_id="u1", nights=2))
        assert result["ok"]
        assert "hold_id" in result
        assert result["total_price"] == 200.0   # 2 nights × $100

    def test_try_hold_decrements_availability(self, hotel):
        ray.get(hotel.try_hold.remote(room_type="double", user_id="u1", nights=1))
        result = ray.get(hotel.try_hold.remote(room_type="double", user_id="u2", nights=1))
        assert not result["ok"]  # only 1 double room

    def test_try_hold_unknown_room_type(self, hotel):
        result = ray.get(hotel.try_hold.remote(room_type="suite", user_id="u1", nights=1))
        assert not result["ok"]

    def test_release_hold_restores_availability(self, hotel):
        hold = ray.get(hotel.try_hold.remote(room_type="double", user_id="u1", nights=1))
        assert hold["ok"]
        rel = ray.get(hotel.release_hold.remote(hold_id=hold["hold_id"]))
        assert rel["ok"]
        # should be bookable again
        hold2 = ray.get(hotel.try_hold.remote(room_type="double", user_id="u2", nights=1))
        assert hold2["ok"]

    def test_confirm_hold_creates_reservation(self, hotel):
        hold = ray.get(hotel.try_hold.remote(room_type="single", user_id="u1", nights=1))
        confirm = ray.get(hotel.confirm_hold.remote(hold_id=hold["hold_id"]))
        assert confirm["ok"]
        assert "reservation_id" in confirm

    def test_cancel_reservation(self, hotel):
        hold = ray.get(hotel.try_hold.remote(room_type="single", user_id="u1", nights=1))
        confirm = ray.get(hotel.confirm_hold.remote(hold_id=hold["hold_id"]))
        cancel = ray.get(hotel.cancel_reservation.remote(reservation_id=confirm["reservation_id"]))
        assert cancel["ok"]


# ---------------------------------------------------------------------------
# PaymentActor
# ---------------------------------------------------------------------------
class TestPaymentActor:
    @pytest.fixture
    def payment(self):
        from actors import PaymentActor
        actor = PaymentActor.remote()
        yield actor
        ray.kill(actor)

    def test_process_payment_success(self, payment):
        result = ray.get(payment.process_payment.remote(user_id="u1", amount=300.0, payment_method="card"))
        assert result["ok"]
        assert "payment_id" in result
        assert result["amount"] == 300.0

    def test_process_payment_rejected_method(self, payment):
        result = ray.get(payment.process_payment.remote(user_id="u1", amount=100.0, payment_method="reject"))
        assert not result["ok"]

    def test_process_payment_invalid_amount(self, payment):
        result = ray.get(payment.process_payment.remote(user_id="u1", amount=0, payment_method="card"))
        assert not result["ok"]

    def test_refund_payment_success(self, payment):
        pay = ray.get(payment.process_payment.remote(user_id="u1", amount=150.0, payment_method="card"))
        refund = ray.get(payment.refund_payment.remote(
            payment_id=pay["payment_id"], amount=150.0, reason="test"
        ))
        assert refund["ok"]
        assert refund["refunded_amount"] == 150.0

    def test_refund_missing_payment_id(self, payment):
        result = ray.get(payment.refund_payment.remote(payment_id="", amount=100.0))
        assert not result["ok"]


# ---------------------------------------------------------------------------
# ReservationHistoryActor
# ---------------------------------------------------------------------------
class TestReservationHistoryActor:
    @pytest.fixture
    def history(self):
        from actors import ReservationHistoryActor
        actor = ReservationHistoryActor.remote()
        yield actor
        ray.kill(actor)

    def _reservation(self, rid="r-1", user="u1"):
        return {
            "reservation_id": rid,
            "user_id": user,
            "hotel_id": "h-1",
            "room_type": "single",
            "nights": 2,
            "total_price": 200.0,
            "payment_id": "p-1",
            "status": "confirmed",
            "created_at": time.time(),
        }

    def test_add_and_list(self, history):
        ray.get(history.add_reservation.remote(self._reservation()))
        result = ray.get(history.list_user_reservations.remote(user_id="u1"))
        assert len(result) == 1
        assert result[0]["reservation_id"] == "r-1"

    def test_list_empty_user(self, history):
        result = ray.get(history.list_user_reservations.remote(user_id="nobody"))
        assert result == []

    def test_cancel_reservation(self, history):
        ray.get(history.add_reservation.remote(self._reservation()))
        result = ray.get(history.cancel_reservation.remote(reservation_id="r-1"))
        assert result["ok"]
        reservations = ray.get(history.list_user_reservations.remote(user_id="u1"))
        assert reservations[0]["status"] == "cancelled"

    def test_cancel_nonexistent(self, history):
        result = ray.get(history.cancel_reservation.remote(reservation_id="nonexistent"))
        assert not result["ok"]

    def test_multiple_users_isolated(self, history):
        ray.get(history.add_reservation.remote(self._reservation("r-1", "u1")))
        ray.get(history.add_reservation.remote(self._reservation("r-2", "u2")))
        assert len(ray.get(history.list_user_reservations.remote(user_id="u1"))) == 1
        assert len(ray.get(history.list_user_reservations.remote(user_id="u2"))) == 1


# ---------------------------------------------------------------------------
# AuditLogActor
# ---------------------------------------------------------------------------
class TestAuditLogActor:
    @pytest.fixture
    def audit(self):
        from actors import AuditLogActor
        actor = AuditLogActor.remote()
        yield actor
        ray.kill(actor)

    def test_log_returns_ok(self, audit):
        result = ray.get(audit.log.remote(
            event_type="TEST_EVENT", actor_id="u1", entity_id="e1", details={"x": 1}
        ))
        assert result["ok"]

    def test_list_logs_empty(self, audit):
        # db mock returns [] always
        logs = ray.get(audit.list_logs.remote())
        assert isinstance(logs, list)
