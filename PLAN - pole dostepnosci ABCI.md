# Plan wdrożenia — pole „Dostępność magazynowa" (A/B/C/I)

Pole bliźniacze do `_price-omnibus`: skalarna wartość **per wariant**, edytowalna w panelu,
importowalna przez WP All Import, czytelna przez REST API, walidowana przez Weryfikator Cen.

---

## 0. Rekomendacja i ustalenia

### Wybrane rozwiązanie: własne meta + natywne hooki WooCommerce

| Opcja | Per wariant | Import WP All Import | REST API | Werdykt |
|---|---|---|---|---|
| **Meta + hooki WooCommerce** | ✅ tak | ✅ jak omnibus | ✅ w `meta_data` | ✅ **rekomendacja** |
| ACF (Select) | ⚠️ wymaga dodatku do wariantów | ✅ | ✅ | ❌ zbędna zależność |
| Atrybut produktu (`pa_dostepnosc`) | ❌ atrybut definiuje wariant, nie opisuje go | ⚠️ | ⚠️ inaczej | ❌ zła semantyka |
| Stan magazynowy WooCommerce | ✅ | ✅ | ✅ | ❌ nie odda 4 stanów A/B/C/I |

**Dlaczego nie stan magazynowy WooCommerce:** natywnie masz „w magazynie / na zamówienie /
brak". Wasze A/B/C/I to **terminy realizacji**, nie stany zapasu — nie da się tego odwzorować
bez naciągania. Własne pole jest tu uzasadnione.

**Dlaczego nie ACF:** ACF nie obsługuje wariantów produktu bez dodatkowej wtyczki
(`product_variation` to niepubliczny typ wpisu). Dokładanie zależności dla jednego pola
tekstowego to zbędne ryzyko przy aktualizacjach.

### Ustalenie techniczne (zweryfikowane)

**Meta z prefiksem `_` JEST zwracane przez WooCommerce REST API v3.** WooCommerce filtruje
z `meta_data` wyłącznie: własne wewnętrzne klucze WC, `wp_*`, `attribute_*` oraz pola
oznaczone jako chronione przez wtyczkę. Prefiks podkreślenia sam w sobie nie wyklucza pola.

**Dowód z naszego wdrożenia:** aplikacja od tygodni odczytuje `_price-omnibus` z obu sklepów
przez klucz Read-only. Gdyby podkreślenie ukrywało pole, omnibus nigdy by się nie pokazał.

> ⚠️ Uwaga: to dotyczy `wc/v3` (API administracyjne). Store API (`wc/store`) ukrywa
> chronione meta — nas nie dotyczy.

### Przyjęte nazewnictwo

- **Klucz meta:** `_dostepnosc_magazynowa` (podkreślenie = ukryte w ogólnym boksie
  „Pola niestandardowe", spójne z `_price-omnibus`)
- **Wartości:** wyłącznie `A`, `B`, `C`, `I` — inne odrzucane przy zapisie
- **Legenda (jedno źródło prawdy w kodzie):**

| Kod | Znaczenie wewnętrzne (cennik) | Tekst dla klienta |
|---|---|---|
| A | SZYBKO | Wysyłka szybka |
| B | ŚREDNIO | Wysyłka standardowa |
| C | ZAMÓWIENIE INDYWIDUALNE | Produkcja na zamówienie |
| I | ZAPYTANIE INDYWIDUALNE (od 10 szt.) | Zapytanie indywidualne (od 10 szt.) |

**Decyzja UX:** na froncie pokazujemy **słowo**, nie literę. Litera `A` nic nie mówi klientowi
B2C — to oznaczenie z cennika B2B. Litera zostaje w panelu, eksporcie i walidacji.
Legenda jako osobny blok jest wtedy zbędna; potrzebna tylko, gdy zdecydujecie pokazywać litery.

---

## Etap 1 — Utworzenie pola w WooCommerce

Plik: `wp-content/mu-plugins/schedpol-dostepnosc.php`
(katalog `mu-plugins` = wtyczka zawsze aktywna, przeżywa aktualizacje motywu)

```php
<?php
/**
 * Plugin Name: Schedpol — oznaczenie dostępności magazynowej
 * Description: Pole A/B/C/I na wariancie i produkcie prostym + prezentacja na froncie.
 * Version: 1.0
 */
defined( 'ABSPATH' ) || exit;

define( 'SCHEDPOL_DOST_META', '_dostepnosc_magazynowa' );

/** Jedyne źródło prawdy dla legendy. */
function schedpol_dost_slownik() {
    return array(
        'A' => 'Wysyłka szybka',
        'B' => 'Wysyłka standardowa',
        'C' => 'Produkcja na zamówienie',
        'I' => 'Zapytanie indywidualne (od 10 szt.)',
    );
}

/** Opcje do listy w panelu: "A — Wysyłka szybka". */
function schedpol_dost_opcje() {
    $out = array( '' => '— nie ustawiono —' );
    foreach ( schedpol_dost_slownik() as $kod => $opis ) {
        $out[ $kod ] = $kod . ' — ' . $opis;
    }
    return $out;
}

/** Zapis z walidacją — odrzuca wartości spoza A/B/C/I. */
function schedpol_dost_zapisz( $post_id, $wartosc ) {
    $wartosc = strtoupper( trim( (string) $wartosc ) );
    if ( '' === $wartosc ) {
        delete_post_meta( $post_id, SCHEDPOL_DOST_META );
        return;
    }
    if ( ! array_key_exists( $wartosc, schedpol_dost_slownik() ) ) {
        return; // nieznany kod — nie zapisujemy
    }
    update_post_meta( $post_id, SCHEDPOL_DOST_META, $wartosc );
}

/* ---------- PANEL: WARIANT ---------- */
add_action( 'woocommerce_product_after_variable_attributes', 'schedpol_dost_pole_wariantu', 10, 3 );
function schedpol_dost_pole_wariantu( $loop, $variation_data, $variation ) {
    woocommerce_wp_select( array(
        'id'            => SCHEDPOL_DOST_META . '_' . $loop,
        'name'          => SCHEDPOL_DOST_META . '[' . $loop . ']',
        'value'         => get_post_meta( $variation->ID, SCHEDPOL_DOST_META, true ),
        'label'         => 'Dostępność magazynowa',
        'options'       => schedpol_dost_opcje(),
        'wrapper_class' => 'form-row form-row-full',
    ) );
}

add_action( 'woocommerce_save_product_variation', 'schedpol_dost_zapis_wariantu', 10, 2 );
function schedpol_dost_zapis_wariantu( $variation_id, $i ) {
    $val = isset( $_POST[ SCHEDPOL_DOST_META ][ $i ] )
        ? sanitize_text_field( wp_unslash( $_POST[ SCHEDPOL_DOST_META ][ $i ] ) )
        : '';
    schedpol_dost_zapisz( $variation_id, $val );
}

/* ---------- PANEL: PRODUKT PROSTY ---------- */
add_action( 'woocommerce_product_options_inventory_product_data', 'schedpol_dost_pole_proste' );
function schedpol_dost_pole_proste() {
    global $post;
    woocommerce_wp_select( array(
        'id'      => SCHEDPOL_DOST_META,
        'value'   => get_post_meta( $post->ID, SCHEDPOL_DOST_META, true ),
        'label'   => 'Dostępność magazynowa',
        'options' => schedpol_dost_opcje(),
    ) );
}

add_action( 'woocommerce_process_product_meta', 'schedpol_dost_zapis_prosty' );
function schedpol_dost_zapis_prosty( $post_id ) {
    $val = isset( $_POST[ SCHEDPOL_DOST_META ] )
        ? sanitize_text_field( wp_unslash( $_POST[ SCHEDPOL_DOST_META ] ) )
        : '';
    schedpol_dost_zapisz( $post_id, $val );
}
```

---

## Etap 2 — Prezentacja na froncie (z przełączaniem wariantu)

Dopisz do tego samego pliku:

```php
/* ---------- FRONT: wstrzyknięcie do JSON wariantu ---------- */
add_filter( 'woocommerce_available_variation', 'schedpol_dost_do_json', 10, 3 );
function schedpol_dost_do_json( $dane, $product, $variation ) {
    $kod     = get_post_meta( $variation->get_id(), SCHEDPOL_DOST_META, true );
    $slownik = schedpol_dost_slownik();
    $dane['schedpol_dostepnosc'] = ( $kod && isset( $slownik[ $kod ] ) ) ? $slownik[ $kod ] : '';
    return $dane;
}

/* ---------- FRONT: miejsce wyświetlenia ---------- */
add_action( 'woocommerce_single_product_summary', 'schedpol_dost_wyswietl', 25 );
function schedpol_dost_wyswietl() {
    global $product;
    if ( $product->is_type( 'variable' ) ) {
        echo '<p class="schedpol-dostepnosc" style="display:none"></p>';
        return;
    }
    $kod     = get_post_meta( $product->get_id(), SCHEDPOL_DOST_META, true );
    $slownik = schedpol_dost_slownik();
    if ( $kod && isset( $slownik[ $kod ] ) ) {
        echo '<p class="schedpol-dostepnosc">Dostępność: <strong>'
            . esc_html( $slownik[ $kod ] ) . '</strong></p>';
    }
}

/* ---------- FRONT: JS aktualizujący po zmianie wariantu ---------- */
add_action( 'wp_footer', 'schedpol_dost_js' );
function schedpol_dost_js() {
    if ( ! is_product() ) {
        return;
    }
    ?>
    <script>
    jQuery(function ($) {
        var $box = $('.schedpol-dostepnosc');
        $('form.variations_form')
            .on('found_variation', function (e, v) {
                if (v.schedpol_dostepnosc) {
                    $box.html('Dostępność: <strong>' + v.schedpol_dostepnosc + '</strong>').show();
                } else {
                    $box.hide().empty();
                }
            })
            .on('reset_data', function () { $box.hide().empty(); });
    });
    </script>
    <?php
}
```

**Jeśli mimo wszystko chcecie pokazywać literę + legendę**, zmień w `schedpol_dost_do_json`
wartość na `$kod . ' — ' . $slownik[$kod]` i dodaj stały blok legendy pod tabelą rozmiarów.
Rekomendacja pozostaje: sam opis słowny.

---

## Etap 3 — Import danych (WP All Import)

Konfiguracja identyczna jak dla omnibusa, plus jedna zmiana:

1. **Krok mapowania → sekcja Custom Fields → Add Custom Field**
   - **Name:** `_dostepnosc_magazynowa`
   - **Value:** `{dostepnosc[1]}` — nazwa elementu wynika z nagłówka kolumny w CSV
     (WP All Import wycina znaki specjalne; sprawdź podpowiedź w panelu)
2. **Krok 4 → Update existing posts → Metadata → Custom Fields**
   - tryb „Update only these Custom Fields" — **dopisz `_dostepnosc_magazynowa`**
     do istniejącej listy obok `_price-omnibus`
3. **Odznacz** „Skip products if data has not changed" na przebieg, w którym plik
   nie zmienia cen (inaczej import pominie rekordy)

**Kolumna w CSV:** aplikacja doda ją do pliku eksportu jako `Dostepnosc`
(bez polskich znaków — bezpieczniejsze dla mapowania).

---

## Etap 4 — Zmiany w aplikacji Weryfikator Cen

Plik: `09. Aplikacja Walidacja/app.py`

### 4.1. Naprawa `_uprosc` — polskie znaki *(konieczne, wykryty błąd)*

Obecnie `_uprosc` usuwa wszystko poza `a-z0-9`, więc **„Oznaczenie dostępności magazynowej"**
zamienia się w `oznaczenie dost pno ci magazynowej` — żaden synonim tego nie dopasuje.
To latentny błąd dotykający też innych kolumn z polskimi znakami.

```python
_PL = str.maketrans("ąćęłńóśźż", "acelnoszz")

def _uprosc(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower().translate(_PL)).strip()
```

### 4.2. Słownik `POLA` (linia ~454) — nowe pole

⚠️ Sama poprawka `_uprosc` **nie wystarczy** — w nagłówku cennika jest odmiana
„dostępno**ści**", więc synonim `dostepnosc` nie trafi. Lista poniżej jest
przetestowana na realnych wariantach nagłówka:

```python
    "Dostępność": ["oznaczenie dostepnosci", "dostepnosci", "dostepnosc",
                   "availability", "magazyn"],
```

Wynik testu dopasowania (po poprawce z pkt 4.1):

| Nagłówek w pliku | Dopasowanie |
|---|---|
| `Oznaczenie dostępności magazynowej` | ✅ `oznaczenie dostepnosci` |
| `Dostępność` / `Dostępność magazynowa` | ✅ `dostepnosc` |
| `Availability` | ✅ `availability` |

Sprawdzone też, że kandydaci **nie łapią obcych kolumn** (`Nr kat.`, `Kod EAN`,
`NOWA cena katalogowa`, `Sale Price`, `Kod CN`, `% zmian`, `Wymiar…`) — brak kolizji.

> Bez poprawki z pkt 4.1 nagłówek `Oznaczenie dostępności magazynowej` dopasowałby się
> błędnie do synonimu `magazyn` (bo diakrytyki rozbijały słowo na `dost pno ci`).

oraz etykieta w `ETYKIETY`:

```python
    "Dostępność": "Dostępność magazynowa (A/B/C/I)",
```

### 4.3. `normalizuj` (linia ~508) — kolumna tekstowa

```python
    out["Dostępność"] = (df[m["Dostępność"]].astype(str).str.strip().str.upper()
                         if "Dostępność" in m else None)
```

### 4.4. Odczyt ze sklepu — trzy funkcje

W `wyciag` (linia ~247), w `entry` w `pobierz_audyt_ze_sklepu` oraz w `wpis`
w `pobierz_do_eksportu` dodaj:

```python
    def dost(o):
        for meta in o.get("meta_data", []):
            if meta.get("key") == "_dostepnosc_magazynowa":
                return str(meta.get("value") or "").strip().upper()
        return ""
```

i do zwracanego słownika: `"dostepnosc": dost(obj),`

> `meta_data` jest już pobierane w `_fields` we wszystkich trzech funkcjach —
> nie trzeba zmieniać zapytań do API.

### 4.5. `porownaj` (linia ~543) — osobna ścieżka dla pola tekstowego

Obecna pętla cen używa `netto_brutto()` — dla tekstu trzeba porównania wprost.
Po pętli cen, przed kontrolą dat:

```python
        # dostępność — pole tekstowe, porównanie wprost (bez przeliczeń VAT)
        f_d = str(r.get("Dostępność") or "").strip().upper()
        if f_d and f_d not in ("NAN", "NONE"):
            s_d = s.get("dostepnosc", "")
            cos_porownano = True
            if f_d not in ("A", "B", "C", "I"):
                ma_roznice = True
                wiersze.append({
                    "SKU": sku, "Sklep": s["sklep"], "Status": "🔴 RÓŻNICA",
                    "Pole": "Dostępność — niedozwolona wartość w pliku",
                    "Na stronie (netto)": s_d or "brak", "W pliku (netto)": f_d,
                })
            else:
                zgodne = (f_d == s_d)
                if not zgodne:
                    ma_roznice = True
                wiersze.append({
                    "SKU": sku, "Sklep": s["sklep"],
                    "Status": "✅ ZGODNE" if zgodne else "🔴 RÓŻNICA",
                    "Pole": "Dostępność",
                    "Na stronie (netto)": s_d or "brak", "W pliku (netto)": f_d,
                })
```

### 4.6. Eksport — `KOLUMNY_EKSPORT` (linia ~945) i `zbuduj_csv_wlasny` (linia ~1126)

```python
KOLUMNY_EKSPORT = ["ID", "Title", "Parent Product ID", "Product Type", "SKU", "Price",
                   "Regular Price", "Sale Price", "_price-omnibus",
                   "Sale Price Dates From", "Sale Price Dates To", "Dostepnosc"]
```

W funkcji `wiersz(...)` dodaj parametr i klucz `"Dostepnosc": dost`, a w `prod(...)`
przekaż `vals.get("dostepnosc") or ""`.

### 4.7. `policz_zmiany` (linia ~1057) — dostępność w diffie

Analogicznie do dat: pole tekstowe, puste w pliku = zostaje wartość obecna
(nic nie kasujemy), zmiana tylko gdy wartość różna i dozwolona.

### 4.8. `audyt_kompletnosci` (linia ~776) — opcjonalna kontrola

Dodaj checkbox „Oznaczenie dostępności" i regułę:

```python
        spr(not e.get("dostepnosc"), "brak oznaczenia dostępności", "O",
            cfg.get("dostepnosc") and cena)
```

---

## Kolejność wdrożenia i punkty kontrolne

| # | Krok | Kto | Test przed pójściem dalej |
|---|---|---|---|
| 1 | Wgraj `mu-plugins/schedpol-dostepnosc.php` na **jeden sklep** | WordPress | Pole widoczne przy wariancie; zapis `A` działa |
| 2 | Sprawdź REST API | dowolny | `/wp-json/wc/v3/products/<ID>/variations/<ID>` → `_dostepnosc_magazynowa` w `meta_data` |
| 3 | Front — sprawdź przełączanie wariantu | WordPress | Zmiana rozmiaru zmienia tekst dostępności |
| 4 | Zmiany w `app.py` (pkt 4.1–4.8) | — | Mapowanie łapie kolumnę z cennika |
| 5 | Import testowy na **1 produkcie** | Trade/BOK | Log: brak „is not set to be updated"; wartość zapisana |
| 6 | Import masowy + weryfikacja aplikacją | Trade/BOK | Kolumna Dostępność ✅ dla wszystkich SKU |
| 7 | Powtórz kroki 1–3 na drugim sklepie | WordPress | jw. |

## Ryzyka

- **Krok 2 jest bramką.** Jeśli meta nie pojawi się w `meta_data`, wtyczka bezpieczeństwa
  lub motyw filtruje chronione meta. Wtedy: zmień klucz na bez podkreślenia
  (`dostepnosc_magazynowa`) — pole pojawi się dodatkowo w boksie „Pola niestandardowe",
  co jest jedyną wadą.
- **Nadpisanie przy imporcie.** Pole trzeba dopisać do listy „update only these Custom Fields",
  inaczej import je zignoruje — dokładnie tak, jak było z omnibusem.
- **Motyw bez `variations_form`.** Jeśli motyw ma własny selektor wariantów, zdarzenie
  `found_variation` może nie występować — wtedy podpiąć się pod mechanizm motywu.
- **Aktualizacja cennika bez kolumny dostępności** — puste pole nie może kasować wartości
  w sklepie (zasada już obowiązująca w aplikacji, trzeba ją zachować także tu).
