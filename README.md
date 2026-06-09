# Ray Hotel - MVP (Ray + FastAPI + Docker)

## 1) Co zostalo przygotowane

Aktualny szkielet projektu ma:
- backend w `app/` (FastAPI + Ray actors),
- klaster Ray: head node (FastAPI + Ray head) + skalowalne worker nodes,
- frontend demo w `frontend/` (statyczny HTML pod Nginx),
- uruchamianie przez Docker Compose,
- Dashboard Ray na porcie `8265`,
- API backendu na porcie `8000`,
- frontend demo na porcie `8080`.

## 2) Struktura projektu

- `Dockerfile` - obraz Ray head node (FastAPI + Ray head)
- `Dockerfile.worker` - obraz Ray worker node
- `docker-compose.yml` - klaster Ray (head + workers) + frontend + Postgres
- `app/main.py` - API i inicjalizacja aktorow
- `app/actors.py` - aktorzy domenowi Ray
- `app/models.py` - modele request/response
- `app/requirements.txt` - zaleznosci backendu
- `frontend/index.html` - prosty frontend testowy
- `tests/load/` - testy obciążeniowe Locust (scenariusze, seed, weryfikacja contention)
- `docs/load_tests/index.html` - dokumentacja testów obciążeniowych (HTML)
- `docs/pydoc/index.html` - dokumentacja modułów aplikacji (HTML)

## 3) Jak uruchomic krok po kroku

### Krok 1: Wejdz do folderu projektu

```powershell
cd c:\Users\dusza\Documents\Studia\7semestr\SystemyRozproszone\ray-hotel
```

### Krok 2: Zbuduj obrazy

```powershell
docker compose build
```

### Krok 3: Uruchom kontenery

```powershell
docker compose up -d
```

### Krok 4: Sprawdz czy wszystko dziala

```powershell
docker compose ps
```

Powinny dzialac: `ray-head`, 2x `ray-worker`, `ray-hotel-frontend`, `ray-hotel-db`.

### Krok 5: Otworz uslugi w przegladarce

- Backend API docs: http://localhost:8000/docs
- Ray Dashboard: http://localhost:8265
- Frontend demo: http://localhost:8080
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin / admin)

### Krok 6: Podglad logow

```powershell
docker compose logs -f ray-head
docker compose logs -f ray-worker
```

### Krok 7: Zatrzymanie systemu

```powershell
docker compose down
```

## 4) Co robi backend (MVP)

### Aktorzy Ray

- `HotelActor`
  - zarzadza stanem pojedynczego hotelu,
  - trzyma dostepnosc pokoi, holdy i rezerwacje,
  - gwarantuje brak overbookingu dla swojego hotelu.

- `InventoryActor`
  - agreguje hotele,
  - obsluguje wyszukiwanie i deleguje hold/confirm/cancel.

- `PaymentActor`
  - symulacja platnosci.

- `ReservationHistoryActor`
  - historia rezerwacji per user.

- `BookingCoordinatorActor`
  - orchestracja procesu rezerwacji:
    1. hold,
    2. platnosc,
    3. confirm,
    4. zapis do historii,
    5. rollback hold przy bledzie platnosci.

- `AdminActor`
  - dodawanie/aktualizacja hoteli.

- `AuditLogActor`
  - asynchroniczny zapis zdarzen domenowych do bazy.

### Endpointy API

- `GET /health`
- `GET /metrics`
- `POST /auth/login`
- `POST /hotels/search`
- `POST /reservations`
- `POST /reservations/cancel`
- `GET /users/{user_id}/reservations`
- `POST /admin/hotels`
- `GET /admin/audit-logs`

### Zaimplementowane funkcjonalnosci

#### A1 - Autoryzacja JWT + role

- Logowanie: `POST /auth/login` zwraca JWT (Bearer).
- Dostep:
  - `POST /reservations`, `POST /reservations/cancel`, `GET /users/{user_id}/reservations` wymagaja roli `user` i zgodnosci `user_id`.
  - `POST /admin/hotels`, `GET /admin/audit-logs` wymagaja roli `admin`.
- Konta demo (in-memory): `user1/pass` (user), `admin/admin` (admin).

#### A2/A3 - TTL hold i idempotencja

- Hold ma TTL (domyslnie 300s). Po wygasnieciu hold zwalnia pokoj automatycznie przy kolejnych akcjach na hotelu.
- `POST /reservations` akceptuje `idempotency_key` (string). Ponowne wyslanie zadania z tym samym kluczem i `user_id`
  zwraca ten sam rezultat bez tworzenia duplikatu rezerwacji.

#### A4 - Polityka anulacji i zwroty

- Anulacja do 1h od utworzenia rezerwacji: zwrot 100%.
- Po 1h: zwrot 0%.
- W odpowiedzi z `POST /reservations/cancel` zwracane sa pola `refund_percent` i `refund_amount`.

#### B1/B2 - Postgres + snapshot/restore aktorow

- Backend zapisuje dane do Postgresa (tabele `hotels`, `reservations`).
- Stan hoteli i rezerwacji jest odtwarzany przy starcie (restore z bazy).
- Holdy nie sa zapisywane - po restarcie nie istnieja.
- Snapshot hoteli jest wykonywany cyklicznie (co 60s).

#### B3 - Audit log

- Kazda istotna operacja domenowa zapisuje wpis do tabeli `audit_logs` w Postgresie.
- Zdarzenia sa logowane asynchronicznie przez dedykowanego aktora Ray (`AuditLogActor`) - nie blokuja glownego przeplywu.
- Typy zdarzen: `LOGIN`, `HOTEL_UPSERTED`, `HOLD_CREATED`, `HOLD_RELEASED`, `HOLD_CONFIRMED`, `PAYMENT_SUCCESS`, `PAYMENT_FAILED`, `RESERVATION_CREATED`, `RESERVATION_CANCELLED`.
- Kazdy wpis zawiera: `event_id`, `event_type`, `actor_id` (uzytkownik), `entity_id` (np. `reservation_id`), `details` (JSON), `occurred_at`.
- Endpoint `GET /admin/audit-logs` (wymaga roli `admin`) zwraca wpisy z opcjonalnym filtrowaniem po `event_type`, `actor_id`, `entity_id` i `limit`.

#### C1 - Klaster Ray (head + workers)

- Architektura rozdzielona na head node i worker nodes.
- Head node (`ray-head`): uruchamia Ray head process, GCS (port 6379), Dashboard (port 8265) i serwer FastAPI (port 8000). Ma 1 CPU zaalokowane.
- Worker nodes (`ray-worker`): dolaczaja do klastra przez `ray-head:6379`. Kazdy worker ma 2 CPU. Domyslnie uruchamiane sa 2 repliki.
- Aktorzy domenowi (z `lifetime="detached"`) sa tworzone na head node i moga byc schedulowane na workerach.
- Skalowanie workerow: `docker compose up -d --scale ray-worker=N`.
- Dockerfile rozdzielony: `Dockerfile` (head), `Dockerfile.worker` (worker).
- Status klastra widoczny w Ray Dashboard: http://localhost:8265.

#### C2 - Limity zasobow aktorow i placement

- Kazdy aktor ma zdefiniowany limit CPU (`num_cpus`):
  - `HotelActor`: 0.25 CPU
  - `InventoryActor`: 0.5 CPU
  - `BookingCoordinatorActor`: 0.5 CPU
  - `ReservationHistoryActor`: 0.25 CPU
  - `PaymentActor`, `AuditLogActor`, `AdminActor`: 0.1 CPU
- Strategia schedulowania `SPREAD`: named actors i `HotelActor` sa rozmieszczane rownomiernie na dostepnych nodach (head + workers).
- Dzieki temu Ray nie alokuje calego CPU per aktor i moze umiescic wiecej aktorow na jednym workerze.

#### C3 - Retry, backoff i timeouty

- Funkcja `_ray_call(ref, timeout, retries)` zastepuje bezposrednie `ray.get()` w komunikacji miedzy aktorami.
- Timeout domyslny: 10s (aktorzy) / 15s (endpointy HTTP).
- Retry: do 3 prob z exponential backoff (0.3s, 0.6s, 1.2s) przy `RayActorError` / `RayTaskError`.
- `GetTimeoutError` nie jest powtarzany - blad jest propagowany natychmiast.
- Endpointy FastAPI zwracaja HTTP 504 (Gateway Timeout) gdy Ray nie odpowie w czasie.

#### C4 - Monitoring (Prometheus + Grafana)

- Dedykowany `MetricsActor` (Ray) trzyma wszystkie liczniki i histogramy jako Prometheus metrics.
- Endpoint `GET /metrics` eksportuje dane w formacie Prometheus text.
- Middleware HTTP liczy kazde zadanie (`method`, `endpoint`, `status_code`).
- Mierzone metryki:
  - `hotel_reservations_total{status}` - liczba rezerwacji (confirmed / failed)
  - `hotel_cancellations_total{status}` - liczba anulacji (success / failed)
  - `hotel_payments_total{status}` - liczba platnosci (success / failed)
  - `hotel_active_holds` - aktualnie aktywne holdy
  - `hotel_booking_duration_seconds` - histogram czasu trwania rezerwacji
  - `hotel_cancellation_duration_seconds` - histogram czasu trwania anulacji
  - `hotel_http_requests_total{method,endpoint,status_code}` - licznik HTTP
- Prometheus scrape co 15s: http://localhost:9090
- Grafana z gotowym dashboardem `Ray Hotel - Overview`: http://localhost:3000 (login: admin/admin)
- Skonfigurowane alerty: wysoki timeout rate (>5%), wysoki failure rate rezerwacji (>20%) i platnosci (>10%), wolne rezerwacje (p95 > 5s).
- Pliki konfiguracyjne: `monitoring/prometheus/`, `monitoring/grafana/provisioning/`.

## 5) Jak testowac szybko

1. Wejdz na `http://localhost:8000/docs`.
2. Wykonaj `POST /hotels/search` (np. city=`Warszawa`).
3. Wykonaj `POST /reservations` dla hotelu `h-waw-1` i pokoju `single`.
4. Sprawdz `GET /users/{user_id}/reservations`.
5. Anuluj przez `POST /reservations/cancel`.

### Przyklady requestow (PowerShell)

**Login (user)**
```powershell
$login = Invoke-RestMethod -Method POST http://localhost:8000/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"user1","password":"pass"}'
$token = $login.access_token
```

**Wyszukiwanie hoteli**
```powershell
Invoke-RestMethod -Method POST http://localhost:8000/hotels/search `
  -ContentType "application/json" `
  -Body '{"city":"Warszawa"}'
```

**Rezerwacja z idempotency_key**
```powershell
Invoke-RestMethod -Method POST http://localhost:8000/reservations `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body '{"user_id":"user1","hotel_id":"h-waw-1","room_type":"single","nights":1,"payment_method":"card","idempotency_key":"demo-1"}'
```
Powtorz to samo wywolanie z tym samym `idempotency_key` - wynik powinien byc identyczny.

**Lista rezerwacji uzytkownika**
```powershell
Invoke-RestMethod -Method GET http://localhost:8000/users/user1/reservations `
  -Headers @{ Authorization = "Bearer $token" }
```

**Anulacja i zwrot**
```powershell
Invoke-RestMethod -Method POST http://localhost:8000/reservations/cancel `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body '{"user_id":"user1","reservation_id":"<reservation_id>"}'
```
Odpowiedz zawiera `refund_percent` i `refund_amount`.

**Login (admin) + dodanie hotelu**
```powershell
$adminLogin = Invoke-RestMethod -Method POST http://localhost:8000/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"admin"}'
$adminToken = $adminLogin.access_token

Invoke-RestMethod -Method POST http://localhost:8000/admin/hotels `
  -Headers @{ Authorization = "Bearer $adminToken" } `
  -ContentType "application/json" `
  -Body '{"hotel_id":"h-gdn-1","name":"Gdansk Bay Hotel","city":"Gdansk","rooms":{"single":{"available":3,"price":250.0}}}'
```

**Audit log (admin)**
```powershell
Invoke-RestMethod -Method GET "http://localhost:8000/admin/audit-logs?limit=20" `
  -Headers @{ Authorization = "Bearer $adminToken" }

# filtrowanie po typie zdarzenia
Invoke-RestMethod -Method GET "http://localhost:8000/admin/audit-logs?event_type=RESERVATION_CREATED" `
  -Headers @{ Authorization = "Bearer $adminToken" }
```

## 5.1) Testy obciążeniowe (Locust)

Testy obciążeniowe symulują wielu równoczesnych użytkowników HTTP i mierzą latencję,
throughput oraz poprawność pod obciążeniem (w tym brak overbookingu).

**Dokumentacja szczegółowa:**
- `tests/load/README.md` — instrukcja uruchomienia i typy testów
- `tests/load/TEORIA_TESTOW.md` — teoria, metryki, interpretacja wyników
- `docs/load_tests/index.html` — wersja HTML (otwórz w przeglądarce)

### Wymagania

```powershell
pip install locust
docker compose up -d   # API musi działać na http://localhost:8000
```

### Pliki testów

| Plik | Opis |
|------|------|
| `tests/load/locustfile.py` | Główny scenariusz: `HotelUser` + `AdminUser` (restock wyłączony domyślnie) |
| `tests/load/locustfile_contention.py` | Mixed contention: book + cancel + search |
| `tests/load/locustfile_pure_contention.py` | Pure contention: tylko book, cel `h-waw-1/single` |
| `tests/load/seed_hotels.py` | Dosiewanie pojemności hoteli przed testem |
| `tests/load/verify_contention.py` | Weryfikacja wyników contention (audit log + dostępność) |
| `tests/load/run_contention.ps1` | Automatyzacja: kill Locust → seed → baseline → test → verify |

### Szybki start

```powershell
mkdir tests/load/results -ErrorAction SilentlyContinue
python tests/load/seed_hotels.py --rooms 500
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 30 -r 5 -t 60s --csv tests/load/results/run1
```

### Typy testów

| Typ | Cel | Polecenie skrócone |
|-----|-----|-------------------|
| Smoke | Czy API działa | `-u 5 -t 30s` |
| Load | Normalne obciążenie | `-u 100 -t 120s` + seed 500 |
| Stress | Punkt nasycenia | `-u 100 -r 10 -t 180s` |
| Spike | Nagły skok ruchu | `-u 1000 -r 1000 -t 60s` |
| Soak | Wytrzymałość / wycieki | `-u 20 -t 10m` |
| Idempotency | Duplikaty requestów | wbudowane w `locustfile.py` |
| Contention (pure) | Wyścig o 1 pokój | `.\tests\load\run_contention.ps1` |
| Contention (mixed) | Book + cancel pod obciążeniem | `.\tests\load\run_contention.ps1 -Mixed` |

### Contention test — wyścig o ostatni pokój

**Pure contention** (zalecany do weryfikacji braku double-bookingu):

```powershell
.\tests\load\run_contention.ps1
```

Oczekiwany wynik przy `--rooms 1`:
- dokładnie **1 aktywna** rezerwacja na `h-waw-1/single`
- reszta requestów → `NO_AVAILABILITY`
- **0× HOTEL_UPSERTED** w oknie testu
- `available >= 0` (brak ujemnej dostępności)

**Ważne:** przed testem zatrzymaj stare procesy Locust — `AdminUser` z wcześniejszego
`locustfile.py` mógł dokładać pokoje (`HOTEL_UPSERTED`). Skrypt `run_contention.ps1`
robi to automatycznie.

Opcjonalny restock hoteli w zwykłym load teście (domyślnie **wyłączony**):

```powershell
$env:LOCUST_RESTOCK = "1"
locust -f tests/load/locustfile.py ...
```

### Interpretacja CSV

Po teście headless powstają pliki `*_stats.csv`, `*_stats_history.csv`, `*_failures.csv`.
Kluczowe kolumny: `95%` (latencja), `Failure Count`, `Requests/s`.
Szczegóły w `tests/load/README.md` i `tests/load/TEORIA_TESTOW.md`.


