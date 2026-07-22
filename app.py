#!/usr/bin/env python3
"""
Weryfikator Cen Schedpol / Schedline — aplikacja Streamlit
==========================================================

Sprawdza zgodność cen w sklepach WooCommerce (schedpol.pl oraz schedline.pl)
z wgranym plikiem (CSV / Excel) o DOWOLNEJ budowie.

Weryfikuje:
  - ceny netto  (wyliczane z VAT, jeśli sklep trzyma brutto)
  - ceny brutto
  - cenę omnibus (najniższa cena z 30 dni)
  - daty obowiązywania promocji (od / do)
  - dodatkowo: logiczne błędy promocji (Omnibus / wygasłe daty / wycofane w promo)

Sekrety (Streamlit → Settings → Secrets) — dwa sklepy, klucze Read-only:
    APP_HASLO = "wspolne_haslo_zespolu"

    SCHEDPOL_URL    = "https://schedpol.pl"
    SCHEDPOL_KEY    = "ck_..."
    SCHEDPOL_SECRET = "cs_..."

    SCHEDLINE_URL    = "https://schedline.pl"
    SCHEDLINE_KEY    = "ck_..."
    SCHEDLINE_SECRET = "cs_..."

Uruchomienie lokalne:
    pip install -r requirements.txt
    streamlit run app.py
"""

import io
import re
import datetime as dt

import pandas as pd
import requests
import streamlit as st


# ===========================================================================
# KONFIGURACJA STRONY
# ===========================================================================

st.set_page_config(
    page_title="Weryfikator Cen Schedpol / Schedline",
    page_icon="🔍",
    layout="wide",
)

# Definicja sklepów: nazwa -> prefiksy kluczy w Secrets
SKLEPY = {
    "schedpol.pl":  {"url": "SCHEDPOL_URL",  "key": "SCHEDPOL_KEY",  "secret": "SCHEDPOL_SECRET"},
    "schedline.pl": {"url": "SCHEDLINE_URL", "key": "SCHEDLINE_KEY", "secret": "SCHEDLINE_SECRET"},
}


# ===========================================================================
# LOGOWANIE
# ===========================================================================

def sprawdz_haslo():
    """Prosty gate hasłem. Zwraca True jeśli zalogowany."""
    if st.session_state.get("zalogowany"):
        return True

    st.title("🔒 Weryfikator Cen Schedpol / Schedline")
    st.caption("Narzędzie wewnętrzne zespołu Trade / BOK")

    haslo = st.text_input("Hasło dostępu", type="password")
    if st.button("Zaloguj"):
        prawidlowe = st.secrets.get("APP_HASLO", "")
        if haslo and haslo == prawidlowe:
            st.session_state["zalogowany"] = True
            st.rerun()
        else:
            st.error("Nieprawidłowe hasło.")
    return False


# ===========================================================================
# POBIERANIE CEN ZE SKLEPU (WooCommerce REST API)
# ===========================================================================

@st.cache_data(ttl=300, show_spinner=False)
def pobierz_ceny_ze_sklepu(sklep_nazwa):
    """
    Pobiera wszystkie produkty i warianty z jednego sklepu.
    Zwraca słownik {sku: {...ceny surowe...}}.
    Cache 5 min — nie odpytujemy API przy każdym kliknięciu.
    """
    cfg = SKLEPY[sklep_nazwa]
    url    = st.secrets[cfg["url"]].rstrip("/")
    key    = st.secrets[cfg["key"]]
    secret = st.secrets[cfg["secret"]]

    def fetch_all(endpoint):
        out, page = [], 1
        while True:
            resp = requests.get(
                f"{url}/wp-json/wc/v3/{endpoint}",
                auth=(key, secret),
                params={"per_page": 100, "page": page},
                timeout=30,
            )
            if resp.status_code == 401:
                raise RuntimeError(f"[{sklep_nazwa}] 401 — złe klucze API lub brak uprawnień.")
            if resp.status_code != 200:
                raise RuntimeError(f"[{sklep_nazwa}] API {resp.status_code}: {resp.text[:200]}")
            batch = resp.json()
            if not batch:
                break
            out.extend(batch)
            total = int(resp.headers.get("X-WP-TotalPages", page))
            if page >= total:
                break
            page += 1
        return out

    def num(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    def wyciag(obj, parent_name=""):
        omnibus = None
        for m in obj.get("meta_data", []):
            if m.get("key") == "_price-omnibus":
                omnibus = num(m.get("value"))
                break
        return {
            "sklep":     sklep_nazwa,
            "id":        obj.get("id"),
            "nazwa":     obj.get("name") or parent_name,
            "regular":   num(obj.get("regular_price")),
            "sale":      num(obj.get("sale_price")),
            "omnibus":   omnibus,
            "on_sale":   bool(obj.get("on_sale")),
            "date_from": obj.get("date_on_sale_from"),
            "date_to":   obj.get("date_on_sale_to"),
        }

    sklep, zmienne = {}, []
    for p in fetch_all("products"):
        sku = (p.get("sku") or "").strip()
        if p.get("type") == "variable":
            zmienne.append((p["id"], p.get("name", "")))
        if sku:
            sklep[sku] = wyciag(p)

    for pid, pname in zmienne:
        for v in fetch_all(f"products/{pid}/variations"):
            sku = (v.get("sku") or "").strip()
            if sku:
                sklep[sku] = wyciag(v, pname)

    return sklep


def pobierz_ze_sklepow(wybrane_sklepy):
    """Łączy dane z wybranych sklepów. Przy kolizji SKU zachowuje pierwszy
    trafiony i zapamiętuje, że SKU występuje w kilku sklepach."""
    polaczony, kolizje = {}, {}
    for nazwa in wybrane_sklepy:
        dane = pobierz_ceny_ze_sklepu(nazwa)
        for sku, rec in dane.items():
            if sku in polaczony:
                kolizje.setdefault(sku, {polaczony[sku]["sklep"]}).add(nazwa)
            else:
                polaczony[sku] = rec
    return polaczony, kolizje


# ===========================================================================
# NETTO / BRUTTO
# ===========================================================================

def netto_brutto(p, tryb, vat):
    """Z jednej ceny sklepowej wylicza parę (netto, brutto) wg trybu i VAT."""
    if p is None:
        return (None, None)
    v = 1 + vat / 100.0
    if tryb == "brutto":          # w sklepie zapisane jest brutto
        return (round(p / v, 2), round(p, 2))
    return (round(p, 2), round(p * v, 2))  # zapisane netto -> doliczamy VAT


# ===========================================================================
# WCZYTYWANIE PLIKU O DOWOLNEJ BUDOWIE
# ===========================================================================

def to_float(v):
    if v is None:
        return None
    s = str(v).strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    if s in ("", "nan", "None"):
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def wczytaj_surowo(uploaded, arkusz=None):
    """Wczytuje CSV lub Excel jako surowy DataFrame (wszystko jako tekst)."""
    nazwa = uploaded.name.lower()
    if nazwa.endswith(".csv"):
        try:
            return pd.read_csv(uploaded, dtype=str, sep=None, engine="python")
        except Exception:
            uploaded.seek(0)
            return pd.read_csv(uploaded, dtype=str)
    return pd.read_excel(uploaded, dtype=str, sheet_name=arkusz or 0)


def lista_arkuszy(uploaded):
    """Zwraca listę arkuszy pliku Excel (pusta dla CSV)."""
    if uploaded.name.lower().endswith(".csv"):
        return []
    try:
        return pd.ExcelFile(uploaded).sheet_names
    finally:
        uploaded.seek(0)


# Pola docelowe + synonimy do auto-wykrywania kolumn
POLA = {
    "SKU":            ["sku", "symbol", "indeks", "index", "kod", "kod produktu"],
    "EAN":            ["ean", "gtin", "kod kreskowy", "barcode"],
    "Regular netto":  ["regular netto", "cena regularna netto", "netto regularna", "reg netto"],
    "Regular brutto": ["regular price", "regular brutto", "cena regularna", "cena regularna brutto",
                       "regularna", "cena katalogowa"],
    "Sale netto":     ["sale netto", "promo netto", "cena promocyjna netto", "netto promo"],
    "Sale brutto":    ["sale price", "cena promocyjna", "promo", "promocja", "cena promo",
                       "sale brutto", "promocyjna brutto"],
    "Omnibus netto":  ["omnibus netto", "najnizsza netto", "cena 30 dni netto"],
    "Omnibus brutto": ["_price-omnibus", "price-omnibus", "omnibus", "najnizsza cena",
                       "najnizsza cena 30 dni", "cena 30 dni", "omnibus brutto"],
    "Data od":        ["sale price dates from", "data od", "od", "start promocji", "poczatek",
                       "date from", "obowiazuje od"],
    "Data do":        ["sale price dates to", "data do", "do", "koniec promocji", "koniec",
                       "date to", "obowiazuje do"],
}


def _uprosc(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def auto_mapowanie(kolumny):
    """Dla każdego pola docelowego proponuje najlepiej pasującą kolumnę pliku."""
    uproszczone = {k: _uprosc(k) for k in kolumny}
    wynik = {}
    for pole, synonimy in POLA.items():
        trafienie = None
        for syn in synonimy:
            su = _uprosc(syn)
            # 1) dokładne dopasowanie
            for kol, ku in uproszczone.items():
                if ku == su:
                    trafienie = kol
                    break
            if trafienie:
                break
        if not trafienie:
            for syn in synonimy:           # 2) dopasowanie "zawiera"
                su = _uprosc(syn)
                for kol, ku in uproszczone.items():
                    if su and su in ku:
                        trafienie = kol
                        break
                if trafienie:
                    break
        wynik[pole] = trafienie
    return wynik


def normalizuj(df, mapowanie):
    """Buduje ujednolicony DataFrame na podstawie mapowania kolumn."""
    out = pd.DataFrame()
    m = {p: k for p, k in mapowanie.items() if k and k != "—"}

    if "SKU" not in m:
        return None
    out["SKU"] = df[m["SKU"]].astype(str).str.strip()

    for pole in ("Regular netto", "Regular brutto", "Sale netto", "Sale brutto",
                 "Omnibus netto", "Omnibus brutto"):
        out[pole] = df[m[pole]].map(to_float) if pole in m else None

    out["Data od"] = df[m["Data od"]] if "Data od" in m else None
    out["Data do"] = df[m["Data do"]] if "Data do" in m else None

    out = out[out["SKU"].notna() & ~out["SKU"].isin(["", "nan", "None"])]
    return out.reset_index(drop=True)


# ===========================================================================
# PORÓWNANIE
# ===========================================================================

def norm_date(v):
    if v is None or str(v).strip() in ("", "nan", "None", "NaT"):
        return ""
    return re.split(r"[T ]", str(v).strip())[0]


def porownaj(plik_df, sklep, tryb_vat, vat, tol, sprawdz_daty):
    """Porównuje plik ze sklepem. Zwraca (DataFrame raportu, statystyki)."""
    wiersze = []
    stat = {"zgodne": 0, "roznica": 0, "brak": 0}
    dzis = dt.date.today()

    for _, r in plik_df.iterrows():
        sku = r["SKU"]
        if sku not in sklep:
            wiersze.append({"SKU": sku, "Sklep": "—", "Status": "❓ BRAK NA STRONIE",
                            "Pole": "-", "Na stronie": "-", "W pliku": "-"})
            stat["brak"] += 1
            continue

        s = sklep[sku]
        reg_n, reg_b = netto_brutto(s["regular"], tryb_vat, vat)
        sal_n, sal_b = netto_brutto(s["sale"],    tryb_vat, vat)
        omn_n, omn_b = netto_brutto(s["omnibus"], tryb_vat, vat)

        pary = [
            ("Regular netto",  r.get("Regular netto"),  reg_n),
            ("Regular brutto", r.get("Regular brutto"), reg_b),
            ("Sale netto",     r.get("Sale netto"),     sal_n),
            ("Sale brutto",    r.get("Sale brutto"),    sal_b),
            ("Omnibus netto",  r.get("Omnibus netto"),  omn_n),
            ("Omnibus brutto", r.get("Omnibus brutto"), omn_b),
        ]

        problemy = []
        for pole, chce, ma in pary:
            if chce is None:
                continue
            if ma is None:
                problemy.append((pole, "brak", chce))
            elif abs(chce - ma) > tol:
                problemy.append((pole, ma, chce))

        if sprawdz_daty:
            for pole, chce_raw, ma_raw in (
                ("Data od", r.get("Data od"), s["date_from"]),
                ("Data do", r.get("Data do"), s["date_to"]),
            ):
                chce, ma = norm_date(chce_raw), norm_date(ma_raw)
                if chce and chce != ma:
                    problemy.append((pole, ma or "brak", chce))

        # logiczne / prawne kontrole promocji (Omnibus)
        ostrzez = kontrola_logiki(s, dzis)

        if problemy or ostrzez:
            for pole, ma, chce in problemy:
                wiersze.append({"SKU": sku, "Sklep": s["sklep"], "Status": "🔴 RÓŻNICA",
                                "Pole": pole, "Na stronie": ma, "W pliku": chce})
            for opis in ostrzez:
                wiersze.append({"SKU": sku, "Sklep": s["sklep"], "Status": "🟠 OSTRZEŻENIE",
                                "Pole": opis, "Na stronie": "-", "W pliku": "-"})
            if problemy:
                stat["roznica"] += 1
            else:
                stat["zgodne"] += 1
        else:
            wiersze.append({"SKU": sku, "Sklep": s["sklep"], "Status": "✅ ZGODNE",
                            "Pole": "-", "Na stronie": "-", "W pliku": "-"})
            stat["zgodne"] += 1

    return pd.DataFrame(wiersze), stat


def kontrola_logiki(s, dzis):
    """Wykrywa błędy logiczne/prawne niezależnie od pliku wzorcowego."""
    ost = []
    reg, sal, omn = s["regular"], s["sale"], s["omnibus"]
    if sal is not None and reg is not None and sal >= reg:
        ost.append("promocja nie jest tańsza od ceny regularnej")
    if omn is not None and reg is not None and omn > reg + 0.01:
        ost.append("omnibus wyższy od ceny regularnej")
    if s["on_sale"] and omn is None:
        ost.append("promocja aktywna, brak ceny omnibus (wymóg prawny)")
    dt_do = norm_date(s["date_to"])
    if s["on_sale"] and dt_do:
        try:
            if dt.date.fromisoformat(dt_do) < dzis:
                ost.append(f"promocja wciąż aktywna, choć data 'do' minęła ({dt_do})")
        except ValueError:
            pass
    return ost


def sprawdz_wycofane(wycofane_df, sklep):
    """Sprawdza, czy wycofane SKU wciąż mają aktywną promocję."""
    wiersze = []
    for _, r in wycofane_df.iterrows():
        sku = r["SKU"]
        if sku in sklep and sklep[sku]["on_sale"]:
            wiersze.append({"SKU": sku, "Sklep": sklep[sku]["sklep"],
                            "Status": "⚠️ WYCOFANY W PROMOCJI",
                            "Pole": "on_sale", "Na stronie": "aktywna promocja",
                            "W pliku": "powinno być wygaszone"})
    return pd.DataFrame(wiersze)


# ===========================================================================
# KOLOROWANIE + EKSPORT
# ===========================================================================

def koloruj(row):
    status = row["Status"]
    if "ZGODNE" in status:
        kolor = "background-color: #EAF3DE"
    elif "RÓŻNICA" in status:
        kolor = "background-color: #FBE4E4"
    elif "OSTRZE" in status:
        kolor = "background-color: #FFF3CD"
    elif "WYCOFANY" in status:
        kolor = "background-color: #FAEEDA"
    else:
        kolor = "background-color: #F0F0F0"
    return [kolor] * len(row)


def do_excela(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Raport")
    return buf.getvalue()


# ===========================================================================
# GŁÓWNY WIDOK
# ===========================================================================

def main():
    st.title("🔍 Weryfikator Cen Schedpol / Schedline")
    st.caption("Porównuje ceny w sklepach z wgranym plikiem (CSV / Excel) o dowolnej budowie")

    with st.sidebar:
        st.header("Ustawienia")

        dostepne = [n for n, c in SKLEPY.items()
                    if all(k in st.secrets for k in c.values())]
        if not dostepne:
            st.error("Brak skonfigurowanych kluczy sklepów w Secrets.")
            st.stop()
        wybrane_sklepy = st.multiselect("Sklepy do sprawdzenia", dostepne, default=dostepne)

        st.divider()
        tryb_vat = st.radio("Ceny w sklepie zapisane jako", ["brutto", "netto"], index=0,
                            help="Typowo polski sklep B2C trzyma ceny brutto.")
        vat = st.number_input("Stawka VAT (%)", value=23.0, step=1.0, min_value=0.0)
        tol = st.number_input("Tolerancja różnicy (zł)", value=0.01, step=0.01,
                              min_value=0.0, format="%.2f",
                              help="Netto liczone z podziału przez VAT — drobne grosze bywają zaokrągleniem.")
        sprawdz_daty = st.checkbox("Sprawdzaj daty promocji", value=True)

        st.divider()
        if st.button("🔄 Odśwież dane ze sklepów"):
            st.cache_data.clear()
            st.success("Cache wyczyszczony.")
        if st.button("Wyloguj"):
            st.session_state["zalogowany"] = False
            st.rerun()

    if not wybrane_sklepy:
        st.warning("Zaznacz przynajmniej jeden sklep w panelu bocznym.")
        return

    col1, col2 = st.columns(2)
    with col1:
        plik_promo = st.file_uploader("📄 Plik z cenami (CSV / Excel)",
                                      type=["csv", "xlsx", "xls"])
    with col2:
        plik_wycofane = st.file_uploader("🗑️ Lista wycofanych SKU (opcjonalnie)",
                                         type=["csv", "xlsx", "xls"])

    if not plik_promo:
        st.info("⬆️ Wgraj plik z cenami, aby rozpocząć weryfikację.")
        return

    # --- wybór arkusza (Excel) ---
    arkusze = lista_arkuszy(plik_promo)
    arkusz = st.selectbox("Arkusz", arkusze) if len(arkusze) > 1 else (arkusze[0] if arkusze else None)

    surowy = wczytaj_surowo(plik_promo, arkusz)
    st.caption(f"Wczytano {len(surowy)} wierszy, kolumny: {', '.join(map(str, surowy.columns))}")

    # --- mapowanie kolumn ---
    st.subheader("🧩 Mapowanie kolumn")
    st.caption("Auto-wykryte poniżej — popraw, jeśli któraś kolumna została źle dopasowana. Myślnik (—) oznacza: pomiń pole.")
    auto = auto_mapowanie(list(surowy.columns))
    opcje = ["—"] + list(surowy.columns)
    mapowanie = {}
    kolumny_ui = st.columns(3)
    for i, pole in enumerate(POLA):
        with kolumny_ui[i % 3]:
            dom = auto.get(pole)
            idx = opcje.index(dom) if dom in opcje else 0
            mapowanie[pole] = st.selectbox(pole, opcje, index=idx, key=f"map_{pole}")

    if mapowanie.get("SKU", "—") == "—":
        st.error("Musisz wskazać kolumnę SKU — bez niej nie ma jak dopasować produktów.")
        return

    plik_df = normalizuj(surowy, mapowanie)
    st.success(f"Do weryfikacji: {len(plik_df)} produktów z SKU.")

    if st.button("▶️ Uruchom weryfikację", type="primary"):
        try:
            with st.spinner("Pobieram aktualne ceny ze sklepów..."):
                sklep, kolizje = pobierz_ze_sklepow(wybrane_sklepy)
        except Exception as e:
            st.error(f"Błąd połączenia ze sklepem: {e}")
            return

        st.caption(f"Pobrano {len(sklep)} SKU z {len(wybrane_sklepy)} sklepu/ów.")
        if kolizje:
            st.warning(f"{len(kolizje)} SKU występuje w obu sklepach — porównano do pierwszego trafionego.")

        raport, stat = porownaj(plik_df, sklep, tryb_vat, vat, tol, sprawdz_daty)

        raport_wyc = pd.DataFrame()
        if plik_wycofane:
            wsurowy = wczytaj_surowo(plik_wycofane)
            wmap = auto_mapowanie(list(wsurowy.columns))
            wdf = normalizuj(wsurowy, wmap)
            if wdf is not None and len(wdf):
                raport_wyc = sprawdz_wycofane(wdf, sklep)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("✅ Zgodne", stat["zgodne"])
        m2.metric("🔴 Różnice", stat["roznica"])
        m3.metric("❓ Brak na stronie", stat["brak"])
        m4.metric("⚠️ Wycofane w promo", len(raport_wyc))

        pelny = pd.concat([raport, raport_wyc], ignore_index=True) if not raport_wyc.empty else raport

        st.divider()
        st.subheader("Raport szczegółowy")
        statusy = pelny["Status"].unique().tolist()
        wybrane = st.multiselect("Filtruj status", statusy, default=statusy)
        widok = pelny[pelny["Status"].isin(wybrane)]

        st.dataframe(widok.style.apply(koloruj, axis=1),
                     use_container_width=True, height=500)

        c1, c2 = st.columns(2)
        buf = io.StringIO()
        pelny.to_csv(buf, index=False)
        c1.download_button("💾 Pobierz CSV", buf.getvalue(),
                           file_name="raport_weryfikacji.csv", mime="text/csv")
        c2.download_button("📊 Pobierz Excel", do_excela(pelny),
                           file_name="raport_weryfikacji.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ===========================================================================
# START
# ===========================================================================

if sprawdz_haslo():
    main()
