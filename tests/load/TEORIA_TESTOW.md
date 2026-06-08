# Teoria i interpretacja testów obciążeniowych – Ray Hotel

## 1. Czym są testy obciążeniowe i po co je robimy

Test obciążeniowy (ang. *load test*) to technika, w której sztucznie generujemy
ruch sieciowy zbliżony do prawdziwego użycia systemu, a następnie mierzymy jak
system się zachowuje: jak szybko odpowiada, kiedy zaczyna popełniać błędy
i gdzie leży jego fizyczna granica wydajności.

W przypadku **Ray Hotel** mamy do czynienia z systemem rozproszonym, gdzie:
- żądania HTTP trafiają do **FastAPI**,
- FastAPI synchronicznie wywołuje **Ray actors** (`ray.get()`),
- aktorzy zarządzają stanem w pamięci i czasem zapisują do **PostgreSQL**.

Każda z tych warstw może być wąskim gardłem. Testy obciążeniowe pokazują,
która z nich pierwsze się nasyci.

---

## 2. Jak działa Locust – mechanizm generowania ruchu

Locust uruchamia N wirtualnych użytkowników (*User*), z których każdy
działa w osobnym wątku (tzw. *greenlet* — lekki wątek współpracujący).
Każdy użytkownik wykonuje zadania (*tasks*) wybrane losowo według wag (*weight*).

```
┌─────────────────────────────────────────────────────────────┐
│  Locust master process                                       │
│                                                             │
│   User 1 ──task──► POST /reservations                       │
│   User 2 ──task──► POST /hotels/search                      │
│   User 3 ──task──► GET  /users/user3/reservations           │
│   ...                                                       │
│   User N ──task──► POST /auth/login                         │
│                                                             │
│   Każdy user czeka 0.3–1.5 s między taskami (wait_time)     │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP
                         ▼
                ┌────────────────┐
                │   FastAPI      │
                └───────┬────────┘
                        │ ray.get()
                        ▼
                ┌────────────────┐
                │  Ray Actors    │
                └───────┬────────┘
                        │ SQL
                        ▼
                ┌────────────────┐
                │  PostgreSQL    │
                └────────────────┘
```

**Ważne:** Locust mierzy czas od wysłania requestu do otrzymania pełnej odpowiedzi
(tzw. *round-trip time*). Wlicza w to sieć, czas przetwarzania FastAPI,
czas oczekiwania na aktorów Ray i czas zapisu do bazy.

---

## 3. Scenariusze zaimplementowane w locustfile.py

### 3.1 HotelUser — gość rezerwujący pokój

Klasa symuluje typowego użytkownika aplikacji. Każdy wirtualny user losuje sobie
numer od 1 do 50 (np. `user17`) i loguje się na starcie.

#### Zadania i ich wagi

| Zadanie | Waga | % czasu | Co robi |
|---------|------|---------|---------|
| `search_hotels` | 8 | ~38 % | POST /hotels/search z losowymi filtrami |
| `book_room` | 6 | ~29 % | POST /reservations — pełna saga rezerwacji |
| `idempotent_double_book` | 2 | ~10 % | Ta sama rezerwacja wysłana dwa razy |
| `check_history` | 3 | ~14 % | GET /users/{id}/reservations |
| `cancel_reservation` | 3 | ~14 % | POST /reservations/cancel |
| `health_check` | 1 | ~5 %  | GET /health |

Suma wag = 23, więc np. `search_hotels` ma szansę 8/23 ≈ 35 % na wywołanie.

#### Dlaczego taki rozkład?

Jest celowo ustawiony tak, żeby więcej operacji **read** niż **write** — 
tak wygląda realistyczny ruch webowy (zasada 80/20: 80 % czytania, 20 % pisania).
Booking jest droższy obliczeniowo, więc nasyci system szybciej niż wyszukiwanie.

#### Co robi `book_room` krok po kroku

```
1. User wysyła POST /reservations z:
   - user_id, hotel_id, room_type, nights, payment_method
   - unikalnym idempotency_key (uuid4)

2. FastAPI waliduje JWT → sprawdza user_id == token.sub

3. FastAPI wywołuje ray.get(coordinator.book_room.remote(...))
   → blokuje wątek FastAPI na max 15 s (RAY_CALL_TIMEOUT_S)

4. BookingCoordinatorActor:
   a) sprawdza idempotency_key — jeśli już był, zwraca poprzedni wynik
   b) wywołuje InventoryActor.hold_room()
      → InventoryActor woła HotelActor.try_hold()
      → HotelActor: available -= 1, zapisuje hold z TTL 300 s
   c) wywołuje PaymentActor.process_payment()
      → symuluje bramkę płatniczą
   d) wywołuje InventoryActor.confirm_hold()
      → HotelActor: usuwa hold, tworzy rezerwację
   e) wywołuje ReservationHistoryActor.add_reservation()
   f) wywołuje AuditLogActor.log(RESERVATION_CREATED)
   g) wywołuje MetricsActor.inc_reservation()

5. Wynik wraca do FastAPI → odpowiedź HTTP 200
   ok=true  → rezerwacja udana, Locust odnotowuje sukces + czas
   ok=false → brak pokoi, ale HTTP 200 — też sukces w sensie technicznym
```

#### Co robi `idempotent_double_book`

Locust wysyła **ten sam JSON dwa razy** (identyczny `idempotency_key`).
Oczekiwanie: oba razy `reservation_id` musi być identyczny.

Jeśli system zwróci dwa różne ID → Locust zgłosi **failure**.
Jest to test poprawności, nie tylko wydajności.

### 3.2 AdminUser — operator

Loguje się jako `admin` i wykonuje:
- **`read_audit_logs`** (waga 4) — GET /admin/audit-logs z losowym filtrem `event_type`
- **`bump_hotel_capacity`** (waga 1) — POST /admin/hotels, przywraca dużą liczbę pokoi
- **`get_metrics`** (waga 2) — GET /metrics w formacie Prometheus

`AdminUser` ma ustawione `weight = 1`, co przy `-u 30` da ~1–2 adminów.
Admin celowo wywołuje `/admin/hotels` w trakcie testu — weryfikuje,
że `InventoryActor.upsert_hotel()` działa bezpiecznie współbieżnie z bookingami.

---

## 4. Kluczowe metryki i jak je czytać

### 4.1 Latencja (czas odpowiedzi)

Latencja to czas od wysłania requestu do otrzymania pełnej odpowiedzi.
Mierzona jest w milisekundach [ms].

**Dlaczego nie patrzeć na średnią:**
Średnia jest zniekształcana przez outliers. Jeśli 95 % requestów trwa 100 ms,
ale 5 % trwa 10 000 ms, średnia wyjdzie ~600 ms — co jest mylące.

**Percentyle są miarodajne:**

```
p50 (mediana) = wartość, poniżej której mieści się 50 % requestów
p95           = wartość, poniżej której mieści się 95 % requestów
p99           = wartość, poniżej której mieści się 99 % requestów
```

**Przykład interpretacji:**

```
p50 = 180 ms  →  "typowy user czeka 180 ms"
p95 = 850 ms  →  "1 na 20 requestów czeka prawie sekundę"
p99 = 3200 ms →  "1 na 100 requestów czeka ponad 3 sekundy"
```

**Granice dla Ray Hotel:**

| Percentyl | Dobry | Akceptowalny | Alarm |
|-----------|-------|--------------|-------|
| p50 | < 200 ms | < 500 ms | > 1 000 ms |
| p95 | < 800 ms | < 2 000 ms | > 5 000 ms |
| p99 | < 2 000 ms | < 5 000 ms | > 10 000 ms |

### 4.2 Failure rate (wskaźnik błędów)

Procent requestów, które zakończyły się błędem. Locust liczy jako błąd:
- HTTP 5xx (błąd serwera)
- HTTP 4xx dla requestów, które powinny działać (np. 401 po wygaśnięciu tokenu)
- Logiczne błędy zgłoszone przez `resp.failure(...)` w scenariuszach (np. złamana idempotentność)

**Nie** jest błędem: `ok=false` w body — to poprawna odpowiedź biznesowa.

| Failure rate | Interpretacja |
|-------------|--------------|
| 0 % | Idealnie |
| < 0.5 % | Bardzo dobrze |
| 0.5–2 % | Akceptowalne, wymaga uwagi |
| > 2 % | Problem — szukaj w logach |
| > 10 % | Krytyczne — system przeciążony |

### 4.3 RPS (Requests Per Second) — przepustowość

Ile requestów system obsługuje na sekundę. W Locust jest to wartość chwilowa
(aktualizowana co sekundę) i zagregowana (za cały test).

**Dla Ray Hotel:**
Przy 30 userach i wait\_time 0.3–1.5 s spodziewamy się ~20–60 RPS.
Przy 100 userach — potencjalnie 100–200 RPS (zależy od bottleneck).

**Saturacja:** kiedy dodajesz więcej userów, ale RPS przestaje rosnąć —
to jest punkt saturacji systemu. Latencja zaczyna rosnąć,
bo requesty czekają w kolejce Ray.

### 4.4 Max Response Time

Absolutne maksimum zaobserwowane w całym teście. Jeśli jest drastycznie
wyższe niż p99 (np. p99 = 1 s, max = 30 s), oznacza to, że raz zdarzyło się
coś wyjątkowego — np. GC pause, chwilowe przeciążenie schedulera Ray,
timeout bazy danych.

---

## 5. Co mówią wyniki o poszczególnych warstwach systemu

### 5.1 Wysoka latencja `/hotels/search`

Endpoint ten tylko czyta stan z `InventoryActor` — powinien być szybki.

| Obserwacja | Prawdopodobna przyczyna |
|-----------|------------------------|
| p95 > 500 ms przy małym load | InventoryActor serializes requests; jeden call na raz |
| p95 rośnie liniowo z userami | Actor jest wąskim gardłem (single-threaded model) |
| Stabilna latencja niezależna od load | OK — actor nadąża |

Ray actors są **single-threaded** — obsługują jeden request na raz.
Wiele równoległych wyszukiwań będzie kolejkowanych.

### 5.2 Wysoka latencja lub błędy `/reservations [book]`

```
Saga = hold + payment + confirm + history + audit + metrics
     = 6 synchronicznych wywołań Ray actor-to-actor
```

Każde wywołanie aktor→aktor to przesyłanie przez sieć Ray (nawet lokalnie
to IPC/serialization overhead). Przy 30 jednoczesnych bookingach
`BookingCoordinatorActor` staje się wąskim gardłem — obsługuje jeden call na raz.

| Obserwacja | Przyczyna |
|-----------|-----------|
| p95 > 2 s przy 20+ userach | Kolejka w BookingCoordinator |
| HTTP 504 | `ray.get()` przekroczył 15 s (RAY_CALL_TIMEOUT_S) |
| `ok=false` w odpowiedziach | Brak dostępnych pokoi — uruchom seed_hotels.py |

### 5.3 Błędy `/reservations [idempotent]`

Każdy błąd tutaj to **bug** — nie problem wydajnościowy.
Oznacza, że dwa identyczne requesty z tym samym `idempotency_key`
zwróciły różne `reservation_id`. Szukaj przyczyny w `BookingCoordinatorActor`.

### 5.4 Rosnąca latencja w czasie (soak test)

Jeśli p95 po 5 minutach jest wyższe niż po 1 minucie przy tej samej liczbie userów:

| Wzorzec wzrostu | Prawdopodobna przyczyna |
|----------------|------------------------|
| Liniowy wzrost | Nakładanie się stanów w aktorach (np. lista rezerwacji rośnie) |
| Schodkowy wzrost | GC pause lub snapshot do DB co N sekund |
| Stabilny plateau | OK — system działa normalnie |

---

## 6. Jak Ray actors wpływają na wyniki testów

### Model wykonania Ray actor

```python
@ray.remote
class BookingCoordinatorActor:
    def book_room(self, ...):
        # JEDEN CALL NA RAZ — actor jest single-threaded
        ...
```

Gdy 10 userów jednocześnie woła `book_room()`, requesty ustawiają się w kolejce.
Jeśli każde `book_room()` trwa 200 ms:
- User 1: czeka 0 ms (pierwszy w kolejce)
- User 2: czeka ~200 ms
- User 5: czeka ~800 ms
- User 10: czeka ~1 800 ms

To tzw. **head-of-line blocking** — charakterystyczne dla actor model.
Locust pokazuje to jako rosnące p99 przy zwiększaniu liczby userów.

### Hold TTL i jego wpływ na testy

Podczas `try_hold()` pokój jest tymczasowo zablokowany na **300 sekund**.
Przy teście spike (80 userów naraz) wiele requestów nie dojdzie do `confirm_hold()` —
zostawi hold wiszący przez 5 minut. To może zafałszować wyniki testu
jeśli nie dosiejemy dużo pokoi.

**Dlatego `seed_hotels.py` jest ważny przed każdym testem.**

---

## 7. Porównanie typów testów — zestawienie

| Typ testu | Czas | Userzy | Cel | Główna obserwacja |
|-----------|------|--------|-----|-------------------|
| Smoke | 30 s | 5 | "Czy działa?" | 0 % błędów |
| Load | 2 min | 30 | "Normalne użycie" | p95, failure rate |
| Stress | 3 min | 100 | "Gdzie jest limit?" | Punkt saturacji |
| Spike | 60 s | 80 (ramp 80/s) | "Nagły skok" | Czy przeżyje? |
| Soak | 10 min | 20 | "Wytrzymałość" | Czy latencja rośnie? |
| Contention | 30 s | 20 (ramp 20/s) | "Wyścig o pokój" | Brak double-booking |
| Idempotency | 60 s | 20 | "Duplikaty" | 0 błędów idempotentności |

---

## 8. Przykładowy przebieg analizy wyników

Załóżmy, że po load teście mamy plik `results/load_stats.csv`:

```
Name,Request Count,Failure Count,Median Response Time,95%,99%,Max,Requests/s
/auth/login,150,0,45,120,210,850,2.5
/hotels/search,480,0,90,280,650,2100,8.0
/reservations [book],360,2,380,1850,4200,14200,6.0
/reservations [idempotent],120,0,410,1950,4500,12800,2.0
/reservations/cancel,180,0,290,1100,2800,8500,3.0
/users/{user_id}/reservations,360,0,95,310,700,1800,6.0
/health,60,0,12,25,45,90,1.0
Aggregated,1710,2,180,820,2100,14200,28.5
```

**Krok 1 – Failure rate:**
```
2 / 1710 = 0.12 % → bardzo dobrze (< 1 %)
```

**Krok 2 – Latencja ogólna:**
```
p50 = 180 ms → dobra mediana
p95 = 820 ms → akceptowalne (< 1 000 ms)
p99 = 2100 ms → ok, ale na granicy
Max = 14200 ms → jeden outlier (prawdopodobnie timeout lub GC pause)
```

**Krok 3 – Najwolniejszy endpoint:**
```
/reservations [book] p95 = 1850 ms → to wąskie gardło
```
Przyczyna: BookingCoordinator robi 6 wywołań actor-to-actor szeregowo.
Każde ~100–200 ms → razem ~600–1200 ms + kolejkowanie.

**Krok 4 – Porównanie search vs book:**
```
search p95 = 280 ms (szybkie, read-only)
book   p95 = 1850 ms (wolne, saga 6 kroków)
```
To oczekiwane zachowanie — saga jest z natury wolniejsza.

**Krok 5 – Wniosek:**
System obsługuje 30 userów przy ~28 RPS bez błędów.
Główne wąskie gardło: `BookingCoordinatorActor` (single-threaded saga).
Możliwa optymalizacja: uruchomić wiele instancji koordynatora
(Ray pool pattern) lub zrównoleglić niezależne kroki sagi.

---

## 9. Słownik pojęć

| Pojęcie | Definicja |
|---------|-----------|
| **Latencja** | Czas oczekiwania na odpowiedź od momentu wysłania requestu |
| **Throughput / RPS** | Liczba requestów obsłużonych na sekundę |
| **Percentyl (p95)** | 95 % requestów było szybszych niż ta wartość |
| **Failure rate** | Procent requestów zakończonych błędem |
| **Saturacja** | Stan, w którym dodanie userów nie zwiększa RPS, ale rośnie latencja |
| **Wąskie gardło** | Komponent, który pierwszy się nasyci i spowalnia cały system |
| **Idempotentność** | Wielokrotne wysłanie tego samego requestu daje ten sam wynik |
| **Hold TTL** | Czas, po którym nie-potwierdzone blokady pokoi są automatycznie zwalniane |
| **Saga** | Wzorzec: sekwencja kroków z mechanizmem rollback przy błędzie |
| **Actor model** | Model obliczeniowy: izolowane obiekty komunikują się przez wiadomości |
| **Head-of-line blocking** | Kolejne requesty czekają, bo actor obsługuje jeden request na raz |
| **Greenlet** | Lekki wątek współpracujący używany przez Locust do symulacji userów |
