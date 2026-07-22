#!/usr/bin/env python3
"""
Weryfikator Cen Schedline — aplikacja Streamlit
================================================

Sprawdza zgodność cen w sklepie WooCommerce (schedline.pl) z wgranym
plikiem cennika / listy promocyjnej.

Funkcje:
  - logowanie hasłem (wspólne dla zespołu)
  - upload pliku CSV (WooCommerce) lub XLSX (cennik Schedline / lista promo)
  - pobranie aktualnych cen ze sklepu przez WooCommerce REST API (tylko odczyt)
  - porównanie: ceny (Regular / Sale / omnibus), daty promocji, wycofane
  - raport w tabeli z kolorami + eksport do CSV

Sekrety (Streamlit → Settings → Secrets):
    WC_URL    = "https://schedline.pl"
    WC_KEY    = "ck_..."
    WC_SECRET = "cs_..."
    APP_HASLO = "wspolne_haslo_zespolu"

Uruchomienie lokalne:
    pip install streamlit requests pandas openpyxl
    streamlit run app.py
"""

import io
import re

import pandas as pd
import requests
import streamlit as st


# ===========================================================================
# KONFIGURACJA STRONY
# ===========================================================================

st.set_page_config(
    page_title="Weryfikator Cen Schedline",
    page_icon="🔍",
    layout="wide",
)


# ===========================================================================
# LOGOWANIE
# ===========================================================================

def sprawdz_haslo():
    """Prosty gate hasłem. Zwraca True jeśli zalogowany."""
    if st.session_state.get("zalogowany"):
        return True

    st.title("🔒 Weryfikator Cen Schedline")
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
def pobierz_ceny_ze_sklepu():
    """
    Pobiera wszystkie produkty i warianty ze sklepu.
    Zwraca słownik {sku: {...ceny...}}.
    Wynik cache'owany na 5 minut, by nie odpytywać API przy każdym kliknięciu.
    """
    url    = st.secrets["WC_URL"].rstrip("/")
    key    = st.secrets["WC_KEY"]
    secret = st.secrets["WC_SECRET"]

    def fetch_all(endpoint):
        out = []
        page = 1
        while True:
            resp = requests.get(
                f"{url}/wp-json/wc/v3/{endpoint}",
                auth=(key, secret),
                params={"per_page": 100, "page": page},
                timeout=30,
            )
            if resp.status_code == 401:
                raise RuntimeError("401 — nieprawidłowe klucze API lub brak uprawnień.")
            if resp.status_code != 200:
                raise RuntimeError(f"API {resp.status_code}: {resp.text[:200]}")
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
            "id":      obj.get("id"),
            "nazwa":   obj.get("name") or parent_name,
            "regular": num(obj.get("regular_price")),
            "sale":    num(obj.get("sale_price")),
            "omnibus": omnibus,
            "on_sale": bool(obj.get("on_sale")),
            "date_from": obj.get("date_on_sale_from"),
            "date_to":   obj.get("date_on_sale_to"),
        }

    sklep = {}
    produkty = fetch_all("products")
    zmienne = []
    for p in produkty:
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


# ===========================================================================
# WCZYTYWANIE WGRANEGO PLIKU
# ===========================================================================

def to_float(v):
    try:
        return round(float(str(v).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def wczytaj_plik(uploaded):
    """
    Rozpoznaje typ wgranego pliku i zwraca DataFrame z ujednoliconymi
    kolumnami: SKU, Regular, Sale, Omnibus, DateFrom, DateTo.
    Obsługuje CSV (format WooCommerce) — dla XLSX zwraca surowy podgląd.
    """
    nazwa = uploaded.name.lower()

    if nazwa.endswith(".csv"):
        df = pd.read_csv(uploaded, dtype=str)
        return _normalizuj_woo_csv(df), df

    elif nazwa.endswith((".xlsx", ".xls")):
        # Cennik XLSX — złożona struktura, wymaga mapowania per-arkusz.
        # Tu wczytujemy pierwszy arkusz jako podgląd; pełne mapowanie
        # cenników odbywa się osobnym skryptem generującym CSV.
        df = pd.read_excel(uploaded, dtype=str)
        return None, df

    else:
        return None, None


def _normalizuj_woo_csv(df):
    """Mapuje kolumny WooCommerce CSV na ujednolicony format."""
    kol = {c.lower().strip(): c for c in df.columns}

    def znajdz(*warianty):
        for w in warianty:
            if w in kol:
                return kol[w]
        return None

    c_sku     = znajdz("sku")
    c_regular = znajdz("regular price", "regular_price")
    c_sale    = znajdz("sale price", "sale_price")
    c_omnibus = znajdz("_price-omnibus", "price-omnibus")
    c_from    = znajdz("sale price dates from", "date_on_sale_from")
    c_to      = znajdz("sale price dates to", "date_on_sale_to")

    if not c_sku:
        return None

    out = pd.DataFrame()
    out["SKU"]     = df[c_sku].astype(str).str.strip()
    out["Regular"] = df[c_regular].map(to_float) if c_regular else None
    out["Sale"]    = df[c_sale].map(to_float)    if c_sale    else None
    out["Omnibus"] = df[c_omnibus].map(to_float) if c_omnibus else None
    out["DateFrom"] = df[c_from] if c_from else None
    out["DateTo"]   = df[c_to]   if c_to   else None

    # tylko wiersze z SKU
    out = out[out["SKU"].notna() & (out["SKU"] != "") & (out["SKU"] != "nan")]
    return out.reset_index(drop=True)


# ===========================================================================
# PORÓWNANIE
# ===========================================================================

def porownaj(plik_df, sklep, sprawdz_daty=True):
    """Porównuje plik ze sklepem. Zwraca (DataFrame raportu, statystyki)."""
    wiersze = []
    stat = {"zgodne": 0, "roznica": 0, "brak": 0}

    def norm_date(v):
        if not v or str(v) in ("nan", "None", "NaT"):
            return ""
        # ucinamy część czasową i strefę: 2026-08-31T00:00:00 -> 2026-08-31
        return re.split(r"[T ]", str(v))[0]

    for _, r in plik_df.iterrows():
        sku = r["SKU"]

        if sku not in sklep:
            wiersze.append({
                "SKU": sku, "Status": "❓ BRAK NA STRONIE",
                "Pole": "-", "Sklep": "-", "Plik": "-",
            })
            stat["brak"] += 1
            continue

        s = sklep[sku]
        problemy = []

        pary = [
            ("Regular", r.get("Regular"), s["regular"]),
            ("Sale",    r.get("Sale"),    s["sale"]),
            ("Omnibus", r.get("Omnibus"), s["omnibus"]),
        ]
        if sprawdz_daty:
            pary += [
                ("Data od", norm_date(r.get("DateFrom")), norm_date(s["date_from"])),
                ("Data do", norm_date(r.get("DateTo")),   norm_date(s["date_to"])),
            ]

        for pole, chce, ma in pary:
            if pole.startswith("Data"):
                if chce and ma and chce != ma:
                    problemy.append((pole, ma, chce))
            else:
                if chce is not None and ma is not None and abs(chce - ma) > 0.01:
                    problemy.append((pole, ma, chce))
                elif chce is not None and ma is None:
                    problemy.append((pole, "brak", chce))

        if problemy:
            for pole, ma, chce in problemy:
                wiersze.append({
                    "SKU": sku, "Status": "🔴 RÓŻNICA",
                    "Pole": pole, "Sklep": ma, "Plik": chce,
                })
            stat["roznica"] += 1
        else:
            wiersze.append({
                "SKU": sku, "Status": "✅ ZGODNE",
                "Pole": "-", "Sklep": "-", "Plik": "-",
            })
            stat["zgodne"] += 1

    return pd.DataFrame(wiersze), stat


def sprawdz_wycofane(wycofane_df, sklep):
    """Sprawdza, czy wycofane produkty wciąż mają aktywną promocję."""
    wiersze = []
    for _, r in wycofane_df.iterrows():
        sku = r["SKU"]
        if sku in sklep and sklep[sku]["on_sale"]:
            wiersze.append({
                "SKU": sku, "Status": "⚠️ WYCOFANY W PROMOCJI",
                "Pole": "on_sale", "Sklep": "aktywna promocja",
                "Plik": "powinno być wygaszone",
            })
    return pd.DataFrame(wiersze)


# ===========================================================================
# KOLOROWANIE TABELI
# ===========================================================================

def koloruj(row):
    status = row["Status"]
    if "ZGODNE" in status:
        kolor = "background-color: #EAF3DE"
    elif "RÓŻNICA" in status:
        kolor = "background-color: #FBE4E4"
    elif "WYCOFANY" in status:
        kolor = "background-color: #FAEEDA"
    else:
        kolor = "background-color: #F0F0F0"
    return [kolor] * len(row)


# ===========================================================================
# GŁÓWNY WIDOK
# ===========================================================================

def main():
    st.title("🔍 Weryfikator Cen Schedline")
    st.caption("Porównuje ceny w sklepie z wgranym plikiem promo / cennikiem")

    with st.sidebar:
        st.header("Ustawienia")
        sprawdz_daty = st.checkbox("Sprawdzaj daty promocji", value=True)
        st.divider()
        if st.button("🔄 Odśwież dane ze sklepu"):
            st.cache_data.clear()
            st.success("Cache wyczyszczony — dane pobiorą się na nowo.")
        st.divider()
        if st.button("Wyloguj"):
            st.session_state["zalogowany"] = False
            st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        plik_promo = st.file_uploader(
            "📄 Plik z cenami promo (CSV WooCommerce)",
            type=["csv", "xlsx", "xls"],
        )
    with col2:
        plik_wycofane = st.file_uploader(
            "🗑️ Lista wycofanych SKU (opcjonalnie, CSV)",
            type=["csv"],
        )

    if not plik_promo:
        st.info("⬆️ Wgraj plik z cenami, aby rozpocząć weryfikację.")
        return

    plik_df, surowy = wczytaj_plik(plik_promo)

    if plik_df is None:
        st.warning(
            "Ten plik to XLSX o złożonej strukturze (cennik). "
            "Aplikacja porównuje pliki w formacie CSV WooCommerce. "
            "Najpierw wygeneruj CSV z cennika, a potem wgraj go tutaj."
        )
        st.dataframe(surowy.head(20))
        return

    st.success(f"Wczytano {len(plik_df)} produktów z pliku.")

    if st.button("▶️ Uruchom weryfikację", type="primary"):
        try:
            with st.spinner("Pobieram aktualne ceny ze sklepu..."):
                sklep = pobierz_ceny_ze_sklepu()
        except Exception as e:
            st.error(f"Błąd połączenia ze sklepem: {e}")
            return

        st.caption(f"Pobrano {len(sklep)} SKU ze sklepu.")

        raport, stat = porownaj(plik_df, sklep, sprawdz_daty)

        # Wycofane
        raport_wyc = pd.DataFrame()
        if plik_wycofane:
            wdf, _ = wczytaj_plik(plik_wycofane)
            if wdf is not None:
                raport_wyc = sprawdz_wycofane(wdf, sklep)

        # Podsumowanie
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("✅ Zgodne", stat["zgodne"])
        m2.metric("🔴 Różnice", stat["roznica"])
        m3.metric("❓ Brak na stronie", stat["brak"])
        m4.metric("⚠️ Wycofane w promo", len(raport_wyc))

        # Pełny raport
        pelny = pd.concat([raport, raport_wyc], ignore_index=True) \
            if not raport_wyc.empty else raport

        st.divider()
        st.subheader("Raport szczegółowy")

        # filtr statusu
        statusy = pelny["Status"].unique().tolist()
        wybrane = st.multiselect("Filtruj status", statusy, default=statusy)
        widok = pelny[pelny["Status"].isin(wybrane)]

        st.dataframe(
            widok.style.apply(koloruj, axis=1),
            use_container_width=True,
            height=500,
        )

        # Eksport
        buf = io.StringIO()
        pelny.to_csv(buf, index=False)
        st.download_button(
            "💾 Pobierz raport CSV",
            buf.getvalue(),
            file_name="raport_weryfikacji.csv",
            mime="text/csv",
        )


# ===========================================================================
# START
# ===========================================================================

if sprawdz_haslo():
    main()
