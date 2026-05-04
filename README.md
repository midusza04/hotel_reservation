# Ray Hotel - MVP (Ray + FastAPI + Docker)

## 1) Co zostalo przygotowane

Aktualny szkielet projektu ma:
- backend w `app/` (FastAPI + Ray actors),
- frontend demo w `frontend/` (statyczny HTML pod Nginx),
- uruchamianie przez Docker Compose,
- Dashboard Ray na porcie `8265`,
- API backendu na porcie `8000`,
- frontend demo na porcie `8080`.

## 2) Struktura projektu

- `Dockerfile` - obraz backendu Ray + FastAPI
- `docker-compose.yml` - backend + frontend
- `app/main.py` - API i inicjalizacja aktorow
- `app/actors.py` - aktorzy domenowi Ray
- `app/models.py` - modele request/response
- `app/requirements.txt` - zaleznosci backendu
- `frontend/index.html` - prosty frontend testowy

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

### Krok 5: Otworz uslugi w przegladarce

- Backend API docs: http://localhost:8000/docs
- Ray Dashboard: http://localhost:8265
- Frontend demo: http://localhost:8080

### Krok 6: Podglad logow

```powershell
docker compose logs -f backend
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

### Endpointy API

- `GET /health`
- `POST /auth/login`
- `POST /hotels/search`
- `POST /reservations`
- `POST /reservations/cancel`
- `GET /users/{user_id}/reservations`
- `POST /admin/hotels`

## 5) Jak testowac szybko

1. Wejdz na `http://localhost:8000/docs`.
2. Wykonaj `POST /hotels/search` (np. city=`Warszawa`).
3. Wykonaj `POST /reservations` dla hotelu `h-waw-1` i pokoju `single`.
4. Sprawdz `GET /users/{user_id}/reservations`.
5. Anuluj przez `POST /reservations/cancel`.

## 6) Plan dalszego rozwoju

### Etap A - domena i logika

1. Dodac prawdziwa autoryzacje (JWT + role: user/admin).
2. Dodac TTL dla hold (wygasanie rezerwacji tymczasowych).
3. Dodac idempotencje operacji rezerwacji i platnosci.
4. Dodac polityke anulacji i zwroty.

### Etap B - dane i trwalosc

1. Dodac Event Store lub baze (np. Postgres) dla trwalego zapisu rezerwacji.
2. Snapshot stanu aktorow albo restore po restarcie.
3. Wydzielic audit log.

### Etap C - rozproszenie i niezawodnosc

1. Uruchomic klaster Ray (head + workers).
2. Dodac limity zasobow aktorow (`num_cpus`, placement).
3. Dodac retry/backoff i timeouty miedzy aktorami.
4. Dodac monitoring (Prometheus/Grafana + log aggregation).

### Etap D - frontend

1. Zmienic placeholder na SPA (np. React/Vite).
2. Widoki: logowanie, lista hoteli, szczegoly, checkout, historia.
3. Integracja z backend API + obsluga bledow i loading states.

### Etap E - testy i CI/CD

1. Testy jednostkowe aktorow.
2. Testy integracyjne przeplywu rezerwacji.
3. Testy obciazeniowe (konkurencyjne rezerwacje).
4. Pipeline CI (lint, test, build image, deploy).

## 7) Proponowany najblizszy krok

Najlepiej teraz zrobic etap `A2` i `A3`:
- wygasanie hold,
- idempotentny booking request.

To od razu zwiekszy realizm systemu rozproszonego i ograniczy race conditions.
