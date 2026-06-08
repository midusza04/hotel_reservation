# Testy obciążeniowe – Ray Hotel

## Wymagania

```bash
pip install locust
```

Locust wymaga Pythona ≥ 3.8 i **nie potrzebuje** instalacji Ray ani pozostałych
zależności projektu — komunikuje się z działającym API przez HTTP.

---

## Ważne – PowerShell vs bash

W **PowerShell** znak `\` **nie jest** kontynuacją linii (to składnia bash/Linux).
Używaj albo jednej długiej linii, albo znaku `` ` `` (backtick):

```powershell
# BŁĘDNIE (bash-style – nie działa w PowerShell):
locust -f tests/load/locustfile.py --host http://localhost:8000 \
       --headless -u 30 -r 5

# POPRAWNIE – jedna linia:
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 30 -r 5 -t 60s --csv tests/load/results/run1

# POPRAWNIE – wieloliniowo z backtick (PowerShell):
locust -f tests/load/locustfile.py --host http://localhost:8000 `
       --headless -u 30 -r 5 -t 60s `
       --csv tests/load/results/run1
```

---

## Szybki start

### 1. Uruchom system

```powershell
# w katalogu hotel_reservation/
docker compose up -d
```

Odczekaj ~30 s. Sprawdź gotowość:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

### 2. Dosiej pojemność hoteli (zalecane przed każdym testem)

Domyślnie hotele mają kilkanaście pokoi — szybko się wyczerpią przy wielu userach.

```powershell
python tests/load/seed_hotels.py --rooms 500
```

### 3. Uruchom Locust

**Tryb z UI** (najłatwiejszy do eksperymentowania):

```powershell
locust -f tests/load/locustfile.py --host http://localhost:8000
# Otwórz http://localhost:8089 i wpisz parametry w formularzu
```

**Tryb headless** – jeden przykład na jednej linii:

```powershell
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 30 -r 5 -t 60s --csv tests/load/results/run1
```

---

## Katalog typów testów

### 1. Smoke test – „czy w ogóle działa"

**Cel:** Weryfikacja, że system odpowiada poprawnie przy minimalnym obciążeniu.
Uruchamiany po każdym wdrożeniu jako pierwszy test.

```powershell
mkdir tests/load/results -ErrorAction SilentlyContinue
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 5 -r 2 -t 30s --csv tests/load/results/smoke
```

| Parametr | Wartość |
|----------|---------|
| Użytkownicy | 5 |
| Czas trwania | 30 s |
| Oczekiwany wynik | 0 % błędów, p95 < 500 ms |

**Co weryfikuje:**
- Czy wszystkie endpointy zwracają HTTP 200
- Czy JWT login działa
- Czy Ray actors odpowiadają
- Czy seeding danych zadziałał (są pokoje do rezerwacji)

---

### 2. Load test – „ile to wytrzymuje"

**Cel:** Realistyczne obciążenie — tyle użytkowników, ilu spodziewamy się jednocześnie.
Główny test przed oddaniem systemu.

```powershell
python tests/load/seed_hotels.py --rooms 500
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 30 -r 5 -t 120s --csv tests/load/results/load
```

| Parametr | Wartość |
|----------|---------|
| Użytkownicy | 30 |
| Ramp-up | 5 użytkowników/s (30 s na pełne obciążenie) |
| Czas trwania | 2 min |
| Oczekiwany wynik | failure rate < 1 %, p95 < 1 000 ms |

**Co weryfikuje:**
- Throughput przy 30 jednoczesnych sesjach
- Stabilność Ray actors pod stałym obciążeniem
- Czy PostgreSQL nadąża z zapisami (audit\_logs, reservations)
- Rozkład latencji dla każdego endpointu

---

### 3. Stress test – „gdzie jest granica"

**Cel:** Stopniowe zwiększanie liczby użytkowników aż do degradacji.
Pokazuje punkt nasycenia systemu.

```powershell
python tests/load/seed_hotels.py --rooms 2000
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 100 -r 10 -t 180s --csv tests/load/results/stress
```

| Parametr | Wartość |
|----------|---------|
| Użytkownicy | 100 |
| Ramp-up | 10/s (10 s pełne obciążenie) |
| Czas trwania | 3 min |

**Na co patrzeć w wynikach:**
- Przy ilu userach p95 przekracza 2 000 ms → to punkt nasycenia
- Czy pojawia się HTTP 504 (timeout Ray call) — oznacza przeciążenie aktorów
- Czy failure rate gwałtownie rośnie (> 5 %) — system nie radzi sobie

---

### 4. Spike test – „nagły skok ruchu"

**Cel:** Symulacja chwilowego gwałtownego wzrostu (np. promocja, flash sale).
Testuje, czy system przeżyje nagły skok bez crashu.

```powershell
python tests/load/seed_hotels.py --rooms 1000
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 80 -r 80 -t 60s --csv tests/load/results/spike
```

| Parametr | Wartość |
|----------|---------|
| Użytkownicy | 80 |
| Ramp-up | 80/s (wszyscy od razu!) |
| Czas trwania | 60 s |

**Co weryfikuje:**
- Czy system nie crashuje przy nagłym skoku
- Czas powrotu do normalnej latencji po szczycie
- Czy hold TTL (300 s) działa — zablokowane pokoje przez nie-ukończone sesje

---

### 5. Soak test – „czy nie ma wycieków"

**Cel:** Długi test przy umiarkowanym obciążeniu. Wyłapuje wycieki pamięci,
narastające opóźnienia, zapełnianie kolejek Ray.

```powershell
python tests/load/seed_hotels.py --rooms 3000
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 20 -r 3 -t 10m --csv tests/load/results/soak
```

| Parametr | Wartość |
|----------|---------|
| Użytkownicy | 20 |
| Czas trwania | 10 minut |

**Na co patrzeć:**
- Czy p95 latencji rośnie w czasie (→ wyciek/nakładanie się requestów)
- Czy liczba błędów jest stała vs rośnie
- Zużycie RAM przez Ray workers (`docker stats`)

---

### 6. Idempotency test – „czy duplikaty są bezpieczne"

**Cel:** Weryfikacja klucza idempotentności w `BookingCoordinatorActor`.
Ten test jest zawarty w `locustfile.py` jako zadanie `idempotent_double_book`.

Uruchom load test i sprawdź w CSV kolumnę **Failure Count** dla
endpointu `/reservations [idempotent]` — powinna wynosić 0.

```powershell
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 20 -r 5 -t 60s --csv tests/load/results/idem
```

**Co weryfikuje:** Ten sam `idempotency_key` wysłany dwa razy musi zwrócić
identyczny `reservation_id`. Locust zgłosi błąd, jeśli system zwróci różne ID.

---

### 7. Contention test – „wyścig o ostatni pokój"

**Cel:** Wielu użytkowników próbuje zarezerwować ten sam typ pokoju jednocześnie.
Testuje saga hold→payment→confirm i ochronę przed double-booking.

Najpierw ogranicz pojemność do 1 pokoju:

```powershell
python tests/load/seed_hotels.py --rooms 1
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 20 -r 20 -t 30s --csv tests/load/results/contention
```

**Oczekiwany wynik:** Dokładnie 1 rezerwacja powinna się udać (ok=true),
pozostałe dostaną ok=false z `"Brak dostepnych pokoi"`. Brak double-booking.

---

## Interpretacja plików CSV

Po każdym teście headless Locust tworzy 3 pliki:

```
results/run1_stats.csv           ← zagregowane statystyki per endpoint
results/run1_stats_history.csv   ← szereg czasowy (co 10 s)
results/run1_failures.csv        ← lista błędów z treścią
```

### Kolumny w `_stats.csv`

| Kolumna | Znaczenie |
|---------|-----------|
| `Name` | nazwa endpointu (lub `Aggregated` = suma) |
| `Request Count` | łączna liczba requestów |
| `Failure Count` | liczba błędów (HTTP 5xx + logiczne) |
| `Median Response Time` | mediana latencji [ms] |
| `95%` | 95-ty percentyl latencji [ms] |
| `99%` | 99-ty percentyl latencji [ms] |
| `Average Response Time` | średnia (mniej miarodajna niż percentyle) |
| `Max Response Time` | maksymalna latencja [ms] |
| `Requests/s` | throughput |

### Szybka analiza wyniku (PowerShell)

```powershell
# Pokaż top 5 najwolniejszych endpointów (p95)
Import-Csv tests/load/results/run1_stats.csv |
  Sort-Object { [int]$_.'95%' } -Descending |
  Select-Object -First 5 Name, '95%', 'Failure Count' |
  Format-Table -AutoSize

# Sprawdź failure rate całościowy
$row = Import-Csv tests/load/results/run1_stats.csv | Where-Object { $_.Name -eq 'Aggregated' }
"Failure rate: $([math]::Round(([int]$row.'Failure Count' / [int]$row.'Request Count') * 100, 2)) %"
```

---

## Typowe wyniki i ich znaczenie

| Wynik | Interpretacja | Akcja |
|-------|--------------|-------|
| failure rate = 0 %, p95 < 300 ms | System stabilny, duży zapas wydajności | OK |
| failure rate = 0 %, p95 500–1000 ms | System pod presją, ale stabilny | Monitor |
| failure rate < 1 %, p95 > 1000 ms | Zbliżenie do limitu | Sprawdź zasoby |
| HTTP 504 GatewayTimeout | Ray actor nie odpowiada w 15 s | Zwiększ zasoby / zmniejsz load |
| `ok: false` w body | Brak pokoi (poprawne zachowanie) | Uruchom seed\_hotels.py |
| Idempotency failures > 0 | Bug w aktorze | Sprawdź BookingCoordinatorActor |

---

## Przydatne komendy pomocnicze

```powershell
# Stan klastra Ray (jeśli działa Docker)
docker exec hotel_reservation-ray_head-1 ray status

# Bieżące metryki aplikacji (Prometheus format)
Invoke-RestMethod http://localhost:8000/metrics

# Ostatnie 20 logów audytowych
Invoke-RestMethod "http://localhost:8000/admin/audit-logs?limit=20" `
  -Headers @{Authorization="Bearer $($(Invoke-RestMethod http://localhost:8000/auth/login -Method Post -Body '{"username":"admin","password":"admin"}' -ContentType 'application/json').access_token)"}

# Zużycie zasobów kontenerów w czasie testu
docker stats --no-stream
```
