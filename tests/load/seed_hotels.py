"""Pre-test hotel seeding script.

Seeds both hotels with a large number of available rooms so that concurrent
booking tests don't immediately exhaust availability.  Run this once before
starting a Locust session.

Usage
-----
    python seed_hotels.py                          # defaults: localhost:8000, 200 rooms
    python seed_hotels.py --host http://localhost:8000 --rooms 500
    python seed_hotels.py --host http://api:8000 --rooms 100 --dry-run
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import json
import urllib.error


def _post(host: str, path: str, payload: dict, token: str) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{host}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SystemExit(f"HTTP {exc.code} on {path}: {body}") from exc


def _login(host: str) -> str:
    req = urllib.request.Request(
        f"{host}/auth/login",
        data=json.dumps({"username": "admin", "password": "admin"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())["access_token"]
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Login failed (HTTP {exc.code})") from exc
    except Exception as exc:
        raise SystemExit(f"Cannot reach {host}: {exc}") from exc


def seed(host: str, rooms: int, dry_run: bool = False) -> None:
    hotels = [
        {
            "hotel_id": "h-waw-1",
            "name": "Warsaw Central Hotel",
            "city": "Warszawa",
            "rooms": {
                "single":    {"available": rooms,          "price": 220.0},
                "double":    {"available": rooms // 2,     "price": 340.0},
            },
        },
        {
            "hotel_id": "h-krk-1",
            "name": "Krakow Market Suites",
            "city": "Krakow",
            "rooms": {
                "single":    {"available": rooms,          "price": 210.0},
                "double":    {"available": rooms // 2,     "price": 320.0},
                "apartment": {"available": rooms // 5,     "price": 520.0},
            },
        },
    ]

    if dry_run:
        print("[DRY RUN] would upsert:")
        for h in hotels:
            print(f"  {h['hotel_id']}  rooms={h['rooms']}")
        return

    print(f"Logging in at {host} …")
    token = _login(host)
    print("  OK\n")

    for hotel in hotels:
        print(f"Upserting {hotel['hotel_id']}  ({hotel['name']}) …")
        result = _post(host, "/admin/hotels", hotel, token)
        print(f"  → {result}\n")

    print("Seeding complete.  Hotels are ready for load testing.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed hotels with fresh room capacity.")
    p.add_argument("--host",  default="http://localhost:8000",
                   help="Base URL of the running API (default: http://localhost:8000)")
    p.add_argument("--rooms", type=int, default=200,
                   help="Number of single rooms to seed per hotel (default: 200)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be sent without actually calling the API")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    seed(host=args.host, rooms=args.rooms, dry_run=args.dry_run)
