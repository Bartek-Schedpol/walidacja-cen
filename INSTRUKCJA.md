# Weryfikator Cen Schedline — instrukcja wdrożenia

Aplikacja Streamlit do porównywania cen w sklepie WooCommerce z plikiem promo.

---

## Co robi aplikacja

1. Zespół loguje się wspólnym hasłem
2. Wgrywasz plik CSV z cenami promo (ten wygenerowany wcześniej)
3. Aplikacja pobiera aktualne ceny ze sklepu przez API (tylko odczyt)
4. Pokazuje raport: co zgodne, co się różni, co wycofane wciąż w promocji
5. Raport można pobrać jako CSV

---

## Wdrożenie krok po kroku (streamlit.app — za darmo)

### 1. Załóż konto GitHub i Streamlit
- Konto na https://github.com (jeśli nie masz)
- Konto na https://streamlit.io/cloud — zaloguj się przez GitHub

### 2. Utwórz repozytorium na GitHub
- Nowe repo (może być **prywatne**)
- Wgraj do niego dwa pliki: `app.py` oraz `requirements.txt`
- **NIE wgrywaj** żadnych kluczy API ani haseł — te idą do Secrets (krok 4)

### 3. Odwołaj stary klucz API i wygeneruj nowy
- WooCommerce → Ustawienia → Zaawansowane → REST API
- Odwołaj wcześniejszy klucz (ten wklejony w rozmowie)
- Dodaj nowy klucz: uprawnienia **Read** (tylko odczyt), skopiuj `ck_...` i `cs_...`

### 4. Wdróż aplikację na Streamlit Cloud
- W Streamlit Cloud: **New app** → wybierz repozytorium → plik główny: `app.py`
- Przed uruchomieniem otwórz **Advanced settings → Secrets** i wklej:

```toml
APP_HASLO = "wspólne_hasło_dla_zespołu"

SCHEDPOL_URL = "https://schedpol.pl"
SCHEDPOL_KEY = "ck_klucz_schedpol_readonly"
SCHEDPOL_SECRET = "cs_secret_schedpol"

SCHEDLINE_URL = "https://schedline.pl"
SCHEDLINE_KEY = "ck_klucz_schedline_readonly"
SCHEDLINE_SECRET = "cs_secret_schedline"
```

> Klucz Read-only generujesz **osobno w każdym sklepie** (WooCommerce → Ustawienia → Zaawansowane → REST API). Jeśli podasz sekrety tylko jednego sklepu — aplikacja pokaże tylko ten sklep.

- Kliknij **Deploy**

### 5. Gotowe
- Dostaniesz link typu `https://twoja-app.streamlit.app`
- Podaj zespołowi link + wspólne hasło
- Klucze API są w Secrets — zespół ich nie widzi, nie ma ich w kodzie

---

## Bezpieczeństwo

- **Klucz API tylko Read-only** — nawet gdyby wyciekł, nikt nie zmieni cen w sklepie
- **Klucze w Secrets, nie w kodzie** — nie trafiają do repozytorium GitHub
- **Hasło aplikacji** chroni przed przypadkowym dostępem z publicznego linku
- Uwaga: darmowy Streamlit Cloud daje publiczny link. Hasło to podstawowa
  ochrona — wystarczająca do danych cenowych, ale nie traktuj jej jak sejfu.
  Nie wrzucaj tam danych klientów czy zamówień.

---

## Tryb „📤 Przygotuj zmiany" — masowa zmiana cen przez import WooCommerce

Aplikacja **nie zapisuje sama** na sklep (zostaje Read-only). Generuje plik CSV, który
wgrywasz przez wbudowany import WooCommerce. Kroki:

1. Wybierz tryb **📤 Przygotuj zmiany** i sklep docelowy.
2. Wgraj plik z **nowymi** cenami, zmapuj kolumny (SKU, Regular/Sale/Omnibus, daty, EAN).
3. Kliknij **Pokaż zmiany** — zobaczysz podgląd (dry-run): co się zmieni (obecne vs nowe),
   ile SKU bez zmian, które SKU nie istnieją na stronie (pomijane).
4. Pobierz **plik importu** oraz **backup obecnych wartości** (do cofnięcia w razie czego).
5. W WooCommerce: **Products → Import** → wskaż plik → zaznacz **„Update existing products"**
   → na ekranie mapowania potwierdź kolumny (zwłaszcza **omnibus** i **EAN**) → uruchom.

**Zasady bezpieczeństwa:**
- Eksport zawiera **tylko zmienione produkty**; pozostałe pola są wypełniane obecnymi
  wartościami, więc import niczego nie kasuje przypadkiem.
- **Najpierw testuj na 1 produkcie** i trzymaj backup.
- **Omnibus:** jeśli sklep ma wtyczkę liczącą omnibus automatycznie, ręcznie wypchnięty
  `_price-omnibus` może zostać nadpisany — sprawdź na 1 produkcie.
- **EAN:** kolumna to `meta:<klucz>` wykryty ze sklepu (albo `global_unique_id`). Jeśli
  import nie zaktualizuje EAN, przypisz kolumnę ręcznie na ekranie mapowania importera.

---

## Uruchomienie lokalne (do testów, opcjonalne)

```bash
pip install -r requirements.txt
# utwórz plik .streamlit/secrets.toml z zawartością jak w kroku 4
streamlit run app.py
```

---

## Uwaga techniczna — pole omnibus

Aplikacja zakłada, że pole omnibus w WooCommerce nazywa się `_price-omnibus`.
Jeśli raport pokaże wszystkie omnibusy jako różnicę "brak", pole ma inną nazwę.
Sprawdź nazwę w bazie i popraw w `app.py` w funkcji `wyciag()` (linia z `_price-omnibus`).
