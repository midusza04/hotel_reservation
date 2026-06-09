"""Weryfikacja wyników contention testu.

Przepływ pracy
--------------
1. Zatrzymaj stare procesy Locust (run_contention.ps1 robi to automatycznie).

2. Dosiej pokoje, POTEM zapisz baseline (timestamp – odcina seed + test):
       python tests/load/seed_hotels.py --rooms 1
       python tests/load/verify_contention.py --save-baseline

3. Uruchom test (TYLKO locustfile_contention.py):
       locust -f tests/load/locustfile_contention.py ...

4. Zweryfikuj:
       python tests/load/verify_contention.py --seeded-rooms 1

Albo jednym poleceniem:
       .\\tests\\load\\run_contention.ps1
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.request
from collections import Counter
from datetime import datetime


BASELINE_FILE = pathlib.Path(__file__).parent / "results" / ".baseline.json"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _request(host: str, method: str, path: str,
             token: str = "", payload: dict | None = None) -> dict | list:
    data = json.dumps(payload).encode() if payload is not None else None
    headers: dict = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{host}{path}", data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _login(host: str, username: str, password: str) -> str:
    return _request(host, "POST", "/auth/login",
                    payload={"username": username, "password": password})["access_token"]


def _fetch_logs(host: str, token: str, event_type: str | None = None) -> list:
    path = "/admin/audit-logs?limit=1000"
    if event_type:
        path += f"&event_type={event_type}"
    data = _request(host, "GET", path, token)
    return data.get("entries", [])


def _parse_ts(entry: dict) -> float:
    raw = entry.get("occurred_at", "")
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).replace("Z", "+00:00")
    return datetime.fromisoformat(text).timestamp()


def _filter_since(entries: list, since_ts: float) -> list:
    return [e for e in entries if _parse_ts(e) > since_ts]


def _active_per_room(entries: list) -> tuple[Counter, Counter]:
    """Return (created_per_room, active_per_room) from audit events in order."""
    created_per_room: Counter = Counter()
    active: dict[str, tuple[str, str]] = {}

    for e in sorted(entries, key=_parse_ts):
        ev = e.get("event_type")
        res_id = e.get("entity_id") or ""
        details = e.get("details") or {}
        hotel = details.get("hotel_id", "")
        room = details.get("room_type", "")

        if ev == "RESERVATION_CREATED" and res_id and hotel and room:
            created_per_room[f"{hotel}/{room}"] += 1
            active[res_id] = (hotel, room)
        elif ev == "RESERVATION_CANCELLED" and res_id:
            active.pop(res_id, None)

    active_per_room: Counter = Counter()
    for hotel, room in active.values():
        active_per_room[f"{hotel}/{room}"] += 1
    return created_per_room, active_per_room


# ─────────────────────────────────────────────────────────────────────────────
# Baseline
# ─────────────────────────────────────────────────────────────────────────────

def save_baseline(host: str) -> None:
    """Record current time – only events AFTER this moment count."""
    print(f"\nŁączenie z {host} …")
    try:
        _login(host, "admin", "admin")
    except Exception as exc:
        print(f"  BŁĄD: {exc}\n")
        return

    since_ts = time.time()
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps({"since_ts": since_ts}))
    print(f"  Zapisano baseline: {datetime.fromtimestamp(since_ts).isoformat()}")
    print(f"  Plik: {BASELINE_FILE}")
    print("\n  Teraz uruchom test Locust, a następnie:")
    print("  python tests/load/verify_contention.py --seeded-rooms 1\n")


# ─────────────────────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────────────────────

def verify(
    host: str,
    seeded_rooms: int | None,
    hotel: str | None = None,
    room_type: str | None = None,
) -> None:
    print(f"\nŁączenie z {host} …")
    try:
        token = _login(host, "admin", "admin")
    except Exception as exc:
        print(f"  BŁĄD logowania: {exc}\n")
        return
    print("  Zalogowano jako admin.\n")

    since_ts = 0.0
    if BASELINE_FILE.exists():
        try:
            since_ts = float(json.loads(BASELINE_FILE.read_text())["since_ts"])
        except (ValueError, KeyError, json.JSONDecodeError):
            since_ts = 0.0

    try:
        all_entries = _fetch_logs(host, token)
    except Exception as exc:
        print(f"  BŁĄD pobierania logów: {exc}\n")
        return

    if since_ts > 0:
        entries = _filter_since(all_entries, since_ts)
        skip_info = (
            f"od {datetime.fromtimestamp(since_ts).strftime('%H:%M:%S')} "
            f"(z {len(all_entries)} wpisów w API)"
        )
    else:
        entries = all_entries
        skip_info = "brak baseline – uruchom --save-baseline PO seed, PRZED testem"

    print("=" * 62)
    print("  WYNIKI CONTENTION TESTU")
    print("=" * 62)
    print(f"\n  Nowe wpisy: {len(entries)}   ({skip_info})\n")

    event_counts   = Counter(e["event_type"] for e in entries)
    created        = [e for e in entries if e["event_type"] == "RESERVATION_CREATED"]
    cancelled      = [e for e in entries if e["event_type"] == "RESERVATION_CANCELLED"]
    upserted       = [e for e in entries if e["event_type"] == "HOTEL_UPSERTED"]
    upserted_count = len(upserted)

    print("── Zdarzenia (tylko w oknie testu) ────────────────────────")
    for ev, cnt in sorted(event_counts.items(), key=lambda x: -x[1]):
        print(f"  {ev:<40s}  {cnt:>5d}×")

    if upserted_count > 0:
        print(f"\n  ⚠  {upserted_count}× HOTEL_UPSERTED w trakcie testu!")
        print("     Ktoś dokładał pokoje (stary Locust z locustfile.py?).")
        for actor, cnt in Counter(e.get("actor_id", "?") for e in upserted).most_common(5):
            print(f"       actor={actor}  {cnt}×")
        print("\n     Rozwiązanie:")
        print("       Get-Process locust -EA SilentlyContinue | Stop-Process -Force")
        print("       .\\tests\\load\\run_contention.ps1")

    active = len(created) - len(cancelled)
    print(f"\n── Rezerwacje ─────────────────────────────────────────────")
    print(f"  RESERVATION_CREATED:    {len(created):>5d}")
    print(f"  RESERVATION_CANCELLED:  {len(cancelled):>5d}")
    print(f"  Aktywne:                {active:>5d}")

    created_per_room, active_per_room = _active_per_room(entries)

    if created_per_room:
        print(f"\n── Utworzone vs aktywne (per hotel/typ) ───────────────────")
        keys = sorted(set(created_per_room) | set(active_per_room))
        for key in keys:
            c = created_per_room.get(key, 0)
            a = active_per_room.get(key, 0)
            print(f"  {key:<30s}  utworzone={c:>3d}  aktywne={a:>3d}")

    print(f"\n── Bieżąca dostępność (z /hotels/search) ──────────────────")
    negative_found = False
    try:
        hotels = _request(host, "POST", "/hotels/search", payload={})
        if isinstance(hotels, list):
            for hotel in hotels:
                hid = hotel.get("hotel_id", "?")
                for rtype, info in hotel.get("rooms", {}).items():
                    avail = info.get("available", 0)
                    flag  = "  ✗  DOUBLE-BOOKING!" if avail < 0 else ""
                    if avail < 0:
                        negative_found = True
                    print(f"  {hid}/{rtype:<18s}  available={avail:>5d}{flag}")
    except Exception as exc:
        print(f"  (błąd: {exc})")

    focus_key = f"{hotel}/{room_type}" if hotel and room_type else None

    if seeded_rooms is not None and upserted_count == 0:
        print(f"\n── Weryfikacja (seed --rooms {seeded_rooms}) ─────────────────────")
        check_keys = [focus_key] if focus_key else list(active_per_room.keys())
        if not check_keys and focus_key:
            check_keys = [focus_key]

        over_active: list[tuple[str, int]] = []
        for key in check_keys:
            a = active_per_room.get(key, 0)
            if a > seeded_rooms:
                over_active.append((key, a))

        if over_active:
            for key, cnt in over_active:
                print(f"  ✗  {key}: {cnt} aktywnych przy max {seeded_rooms} pokoju!")
        elif negative_found:
            print("  ✗  Ujemna dostępność – double-booking.")
        elif focus_key:
            a = active_per_room.get(focus_key, 0)
            c = created_per_room.get(focus_key, 0)
            if a == seeded_rooms:
                print(f"  ✓  {focus_key}: {a} aktywna rezerwacja – poprawnie!")
            elif a < seeded_rooms:
                print(f"  ✓  {focus_key}: {a}/{seeded_rooms} aktywnych (brak double-bookingu).")
            print(f"     (łącznie utworzonych w teście: {c})")
        else:
            print("  ✓  Brak nadmiarowych aktywnych rezerwacji względem seed.")

    print(f"\n── Werdykt ────────────────────────────────────────────────")
    if upserted_count > 0:
        print("  ⚠  Test zakłócony – pokoje dokładane w trakcie (HOTEL_UPSERTED).")
        print("     Zatrzymaj stare Locusty i użyj run_contention.ps1.")
    elif negative_found:
        print("  ✗  DOUBLE-BOOKING wykryty (available < 0)!")
    elif len(created) == 0:
        print("  ⚠  Brak nowych rezerwacji. Czy test został uruchomiony?")
    elif focus_key and active_per_room.get(focus_key, 0) > (seeded_rooms or 1):
        print(f"  ✗  Za dużo aktywnych rezerwacji na {focus_key}!")
    elif focus_key and seeded_rooms and active_per_room.get(focus_key, 0) == seeded_rooms:
        print(f"  ✓  Wyścig OK: dokładnie {seeded_rooms} aktywna rezerwacja na {focus_key}.")
    else:
        print("  ✓  Brak double-bookingu. Brak restocku w trakcie testu.")

    if created:
        print(f"\n── Rezerwacje per użytkownik (top 10) ─────────────────────")
        for user, cnt in Counter(
            e.get("actor_id") for e in created if e.get("actor_id")
        ).most_common(10):
            print(f"  {user:<15s}  {cnt}×")

    print("\n" + "=" * 62 + "\n")
    if BASELINE_FILE.exists():
        BASELINE_FILE.unlink()
        print("  (baseline usunięty – uruchom --save-baseline przed kolejnym testem)\n")


# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Weryfikacja contention testu Ray Hotel.")
    p.add_argument("--host", default="http://localhost:8000")
    p.add_argument("--save-baseline", action="store_true",
                   help="Zapisz timestamp – tylko zdarzenia PO tym momencie liczą się")
    p.add_argument("--seeded-rooms", type=int, default=None)
    p.add_argument("--hotel", default=None, help="np. h-waw-1 (pure contention)")
    p.add_argument("--room-type", default=None, help="np. single")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.save_baseline:
        save_baseline(host=args.host)
    else:
        verify(
            host=args.host,
            seeded_rooms=args.seeded_rooms,
            hotel=args.hotel,
            room_type=args.room_type,
        )
