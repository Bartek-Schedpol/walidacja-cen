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
import os
import re
import hmac
import calendar
import hashlib
import datetime as dt
import concurrent.futures as cf

import pandas as pd
import requests
import streamlit as st


# ===========================================================================
# KONFIGURACJA STRONY
# ===========================================================================

WERSJA = "1.3"
AUTOR = "B. Kokoszanek · Schedpol"
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")

st.set_page_config(
    page_title="Weryfikator Cen Schedpol / Schedline",
    page_icon=LOGO if os.path.exists(LOGO) else "🔍",
    layout="wide",
)
try:
    st.logo(LOGO)          # logo marki u góry aplikacji (Streamlit >= 1.35)
except Exception:
    pass

# Definicja sklepów: nazwa -> prefiksy kluczy w Secrets
SKLEPY = {
    "schedpol.pl":  {"url": "SCHEDPOL_URL",  "key": "SCHEDPOL_KEY",  "secret": "SCHEDPOL_SECRET"},
    "schedline.pl": {"url": "SCHEDLINE_URL", "key": "SCHEDLINE_KEY", "secret": "SCHEDLINE_SECRET"},
}


# ===========================================================================
# STYL (wygląd — nie zmienia logiki)
# ===========================================================================

def wstrzyknij_styl():
    """CSS 'modern dashboard' z niebieskim akcentem — obsługuje jasny i ciemny
    motyw (tokeny przełączane przez prefers-color-scheme, zgodnie z systemem/
    przeglądarką użytkownika). Wyłącznie warstwa wizualna — logiki nie zmienia."""
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    :root {
        --akcent:#2F6FED; --akcent-hover:#1F57C9; --akcent-tekst:#FFFFFF;
        --tlo:#F6F7F9; --karta:#FFFFFF; --linia:#E4E7EC;
        --tekst:#101828; --muted:#667085;
        --cien:0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.06);
    }
    @media (prefers-color-scheme: dark) {
        :root {
            --akcent:#4C8DFF; --akcent-hover:#6BA1FF; --akcent-tekst:#0B0F17;
            --tlo:#0B0F17; --karta:#121826; --linia:#232B3A;
            --tekst:#F0F2F5; --muted:#94A0B4;
            --cien:0 1px 3px rgba(0,0,0,.5);
        }
    }
    html, body, .stApp, [class*="css"] {
        font-family:'Inter',system-ui,-apple-system,sans-serif !important; color:var(--tekst);
    }
    .stApp { background:var(--tlo); }
    .block-container { padding-top:2rem; }
    h1,h2,h3 { font-weight:700 !important; letter-spacing:-.01em; color:var(--tekst); }
    [data-testid="stCaptionContainer"], small { color:var(--muted) !important; }

    /* ---- SIDEBAR: zlewa się z tłem, nie jest już osobnym blokiem koloru ---- */
    [data-testid="stSidebar"] { background:var(--karta); border-right:1px solid var(--linia); }
    [data-testid="stSidebar"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] input {
        background:var(--karta) !important; color:var(--tekst) !important; border-color:var(--linia) !important;
    }
    [data-testid="stSidebar"] .stButton>button { background:var(--karta); color:var(--tekst); border:1px solid var(--linia); }
    [data-testid="stSidebar"] .stButton>button:hover { border-color:var(--akcent); color:var(--akcent); }

    /* ---- KARTY / KAFLE ---- */
    [data-testid="stMetric"] {
        background:var(--karta); border-radius:14px; padding:18px 20px;
        box-shadow:var(--cien); border:1px solid var(--linia); border-top:3px solid var(--akcent);
    }
    [data-testid="stMetricValue"] { color:var(--tekst); font-weight:700; }
    [data-testid="stFileUploader"], [data-testid="stExpander"] {
        background:var(--karta); border-radius:14px; border:1px solid var(--linia); box-shadow:var(--cien);
    }
    [data-testid="stFileUploader"] { padding:12px 16px; }
    .stDataFrame { border-radius:14px; border:1px solid var(--linia); overflow:hidden; }

    /* ---- PRZYCISKI ---- */
    .stButton>button, .stDownloadButton>button, .stFormSubmitButton>button {
        border-radius:10px; font-weight:600; border:1px solid var(--linia);
        padding:.5rem 1.1rem; background:var(--karta); color:var(--tekst);
    }
    .stButton>button[kind="primary"], .stFormSubmitButton>button[kind="primary"],
    .stDownloadButton>button[kind="primary"] {
        background:var(--akcent); border:none; color:var(--akcent-tekst);
        box-shadow:0 2px 6px rgba(47,111,237,.30);
    }
    .stButton>button[kind="primary"]:hover, .stFormSubmitButton>button[kind="primary"]:hover,
    .stDownloadButton>button[kind="primary"]:hover { background:var(--akcent-hover); }

    /* ---- INPUTY / TAGI ---- */
    input, textarea, [data-baseweb="input"], [data-baseweb="select"]>div { border-radius:10px !important; }
    [data-baseweb="input"], [data-baseweb="select"]>div { background:var(--karta) !important; border-color:var(--linia) !important; }
    /* odsuń zawartość multiselecta od zaokrąglonego rogu (żeby tag nie był przycięty) */
    [data-baseweb="select"] > div { padding-left:6px !important; }
    [data-baseweb="tag"] {
        background:var(--akcent) !important; border-radius:7px !important; margin:2px 3px !important;
    }
    [data-baseweb="tag"] span, [data-baseweb="tag"] div { color:var(--akcent-tekst) !important; }
    [data-testid="stHeader"] { background:transparent; }
    </style>
    """, unsafe_allow_html=True)


# ===========================================================================
# LOGOWANIE
# ===========================================================================

def sprawdz_haslo():
    """Prosty gate hasłem. Zwraca True jeśli zalogowany.
    Formularz → Enter w polu hasła również loguje."""
    if st.session_state.get("zalogowany"):
        return True

    st.title("🔒 Weryfikator Cen Schedpol / Schedline")
    st.caption("Narzędzie wewnętrzne zespołu Trade / BOK")

    with st.form("logowanie"):
        haslo = st.text_input("Hasło dostępu", type="password")
        wyslij = st.form_submit_button("Zaloguj", type="primary")
    if wyslij:
        prawidlowe = st.secrets.get("APP_HASLO", "")
        # porównanie odporne na timing-attack
        if haslo and prawidlowe and hmac.compare_digest(str(haslo), str(prawidlowe)):
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

    # tylko potrzebne pola — mniejszy payload, szybsza odpowiedź
    POLA_PROD = ("id,sku,name,type,regular_price,sale_price,on_sale,"
                 "date_on_sale_from,date_on_sale_to,meta_data,global_unique_id")
    POLA_VAR  = ("id,sku,regular_price,sale_price,on_sale,"
                 "date_on_sale_from,date_on_sale_to,meta_data,global_unique_id")

    sess = requests.Session()
    sess.auth = (key, secret)

    def _get(endpoint, page, fields):
        r = sess.get(f"{url}/wp-json/wc/v3/{endpoint}",
                     params={"per_page": 100, "page": page, "_fields": fields,
                             "status": "any"}, timeout=30)
        if r.status_code == 401:
            raise RuntimeError(f"[{sklep_nazwa}] 401 — złe klucze API lub brak uprawnień.")
        if r.status_code != 200:
            raise RuntimeError(f"[{sklep_nazwa}] API {r.status_code}: {r.text[:200]}")
        return r

    def fetch_all(endpoint, fields):
        """Pobiera wszystkie strony. Jeśli nagłówek X-WP-TotalPages jest wiarygodny —
        reszta stron równolegle. Jeśli go brak/niepełny (hosting bywa go obcina) —
        stronicuje sekwencyjnie aż do krótkiej strony (odporne na brak nagłówka)."""
        r = _get(endpoint, 1, fields)
        out = list(r.json())
        try:
            total = int(r.headers.get("X-WP-TotalPages", 0) or 0)
        except ValueError:
            total = 0
        if total > 1:
            with cf.ThreadPoolExecutor(max_workers=12) as ex:
                for batch in ex.map(lambda p: _get(endpoint, p, fields).json(),
                                    range(2, total + 1)):
                    out.extend(batch)
        elif len(out) >= 100:                      # nagłówek niewiarygodny — stronicuj do skutku
            page = 2
            while True:
                batch = _get(endpoint, page, fields).json()
                if not batch:
                    break
                out.extend(batch)
                if len(batch) < 100:
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
        ean, ean_klucz = _wyciag_ean_para(obj)
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
            "ean":       ean,
            "ean_klucz": ean_klucz,
        }

    sklep, zmienne = {}, []
    for p in fetch_all("products", POLA_PROD):
        sku = (p.get("sku") or "").strip()
        if p.get("type") == "variable":
            zmienne.append((p["id"], p.get("name", "")))
        if sku:
            sklep[sku] = wyciag(p)

    # warianty pobierane równolegle — to był główny hamulec
    def pobierz_warianty(pid_pname):
        pid, pname = pid_pname
        return pname, fetch_all(f"products/{pid}/variations", POLA_VAR)

    if zmienne:
        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            for pname, warianty in ex.map(pobierz_warianty, zmienne):
                for v in warianty:
                    sku = (v.get("sku") or "").strip()
                    if sku:
                        sklep[sku] = wyciag(v, pname)

    return sklep


@st.cache_data(ttl=300, show_spinner=False)
def pobierz_konfig_vat(sklep_nazwa):
    """
    Odczytuje z API sklepu: czy ceny są zapisane brutto/netto oraz stawkę VAT.
    Zwraca {"tryb": "brutto"/"netto"/None, "vat": float/None, "info": str}.
    Przy braku dostępu (klucz bez uprawnień do ustawień) zwraca None-y —
    aplikacja użyje wtedy wartości ręcznych.
    """
    cfg = SKLEPY[sklep_nazwa]
    url    = st.secrets[cfg["url"]].rstrip("/")
    key    = st.secrets[cfg["key"]]
    secret = st.secrets[cfg["secret"]]
    tryb, vat, info = None, None, ""

    try:
        r = requests.get(f"{url}/wp-json/wc/v3/settings/tax",
                         auth=(key, secret), params={"per_page": 100}, timeout=30)
        if r.status_code == 200:
            for s in r.json():
                if s.get("id") == "woocommerce_prices_include_tax":
                    tryb = "brutto" if s.get("value") == "yes" else "netto"
        else:
            info = f"settings/tax: HTTP {r.status_code}"
    except Exception as e:
        info = f"settings/tax: {e}"

    try:
        r = requests.get(f"{url}/wp-json/wc/v3/taxes",
                         auth=(key, secret), params={"per_page": 100}, timeout=30)
        if r.status_code == 200:
            rates = r.json()
            std = [t for t in rates if (t.get("class") or "standard") == "standard"]
            pick = std or rates
            if pick:
                try:
                    vat = round(float(pick[0].get("rate")), 2)
                except (TypeError, ValueError):
                    pass
        else:
            info = (info + f"; taxes: HTTP {r.status_code}").strip("; ")
    except Exception as e:
        info = (info + f"; taxes: {e}").strip("; ")

    return {"tryb": tryb, "vat": vat, "info": info}


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
    if p is None or pd.isna(p):     # None oraz NaN (pandas robi NaN z pustych komórek)
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


def wczytaj_surowo(uploaded, arkusz=None, header_row=0):
    """Wczytuje CSV lub Excel jako surowy DataFrame (wszystko jako tekst).
    header_row: indeks (0-based) wiersza z nazwami kolumn."""
    nazwa = uploaded.name.lower()
    if nazwa.endswith(".csv"):
        try:
            return pd.read_csv(uploaded, dtype=str, sep=None, engine="python", header=header_row)
        except Exception:
            uploaded.seek(0)
            return pd.read_csv(uploaded, dtype=str, header=header_row)
    return pd.read_excel(uploaded, dtype=str, sheet_name=arkusz or 0, header=header_row)


def wykryj_wiersz_naglowka(uploaded, arkusz=None, limit=20):
    """Zgaduje, który wiersz zawiera nazwy kolumn — szuka wiersza z największą
    liczbą wypełnionych komórek i słowami kluczowymi (sku/ean/cena/nr kat...)."""
    nazwa = uploaded.name.lower()
    try:
        if nazwa.endswith(".csv"):
            raw = pd.read_csv(uploaded, dtype=str, sep=None, engine="python",
                              header=None, nrows=limit)
        else:
            raw = pd.read_excel(uploaded, dtype=str, sheet_name=arkusz or 0,
                                header=None, nrows=limit)
    except Exception:
        return 0
    finally:
        try:
            uploaded.seek(0)
        except Exception:
            pass

    klucze = ("sku", "ean", "cena", "price", "nr kat", "symbol", "kod", "nazwa", "wymiar")
    best, best_score = 0, -1
    for i, row in raw.iterrows():
        komorki = [_uprosc(v) for v in row.tolist() if not pd.isna(v) and str(v).strip()]
        if not komorki:
            continue
        kw = sum(any(k in c for k in klucze) for c in komorki)
        score = len(komorki) + kw * 4
        if score > best_score:
            best_score, best = score, i
    return int(best)


def lista_arkuszy(uploaded):
    """Zwraca listę arkuszy pliku Excel (pusta dla CSV)."""
    if uploaded.name.lower().endswith(".csv"):
        return []
    try:
        return pd.ExcelFile(uploaded).sheet_names
    finally:
        uploaded.seek(0)


def sygnatura_kolumn(df):
    """Krótki, stabilny podpis zestawu kolumn — do kluczy widgetów mapowania,
    żeby nowy plik/układ kolumn wymusił świeże auto-mapowanie (a nie stan z poprzedniego)."""
    return hashlib.md5("|".join(map(str, df.columns)).encode("utf-8")).hexdigest()[:8]


def sygnatura_pliku(uploaded):
    """Podpis pliku (nazwa + rozmiar) — do kluczy widgetów dostępnych przed odczytem."""
    try:
        return hashlib.md5(f"{uploaded.name}|{uploaded.size}".encode("utf-8")).hexdigest()[:8]
    except Exception:
        return hashlib.md5(str(uploaded.name).encode("utf-8")).hexdigest()[:8]


# Pola docelowe + synonimy do auto-wykrywania kolumn.
# JEDNA kolumna na typ ceny — bazę (netto/brutto) ustawia się osobnym
# przełącznikiem „ceny w pliku podane jako", ta sama dla wszystkich cen.
POLA = {
    "SKU":     ["sku", "nr kat", "nr katalogowy", "symbol", "indeks", "index", "kod produktu"],
    "EAN":     ["ean", "gtin", "kod kreskowy", "barcode"],
    "Regular": ["regular price", "cena regularna", "regularna", "cena katalogowa"],
    "Sale":    ["sale price", "cena promocyjna", "cena promo", "promocyjna", "promocja"],
    "Omnibus": ["_price-omnibus", "price-omnibus", "omnibus", "najnizsza cena 30 dni",
                "najnizsza cena", "cena 30 dni"],
    "Data od": ["sale price dates from", "data od", "obowiazuje od", "start promocji",
                "poczatek promocji", "date from"],
    "Data do": ["sale price dates to", "data do", "obowiazuje do", "koniec promocji",
                "date to"],
}


# Przyjazne etykiety pól w UI (klucze POLA zostają wewnętrzne — nie zmieniać!)
ETYKIETY = {
    "Regular": "Regular (katalogowa netto)",
    "Sale":    "Sale (promocyjna netto)",
    "Omnibus": "Omnibus (netto)",
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

    for pole in ("Regular", "Sale", "Omnibus"):
        out[pole] = df[m[pole]].map(to_float) if pole in m else None

    out["Data od"] = df[m["Data od"]] if "Data od" in m else None
    out["Data do"] = df[m["Data do"]] if "Data do" in m else None
    out["EAN"] = df[m["EAN"]].astype(str).str.strip() if "EAN" in m else None

    # prawdziwy kod produktu: niepusty, bez spacji, rozsądnej długości.
    # Odsiewa wiersze-nagłówki/legendy (np. "ORIENTACYJNA DOSTĘPNOŚĆ...").
    sku = out["SKU"]
    prawidlowe = (sku.notna() & ~sku.isin(["", "nan", "None"])
                  & ~sku.str.contains(r"\s", regex=True, na=False)
                  & (sku.str.len() <= 50))
    return out[prawidlowe].reset_index(drop=True)


# ===========================================================================
# PORÓWNANIE
# ===========================================================================

def norm_date(v):
    if v is None or str(v).strip() in ("", "nan", "None", "NaT"):
        return ""
    return re.split(r"[T ]", str(v).strip())[0]


def porownaj(plik_df, sklep, vat_map, plik_basis, tol, sprawdz_daty,
             pokaz_promo=True, oczek_data_do=None):
    """Porównuje plik ze sklepem — WYŁĄCZNIE w wartościach netto.
    pokaz_promo: dla produktów w promocji dodaje wiersz z datą końca promocji.
    oczek_data_do: jeśli podana, wyróżnia produkty w promocji z inną datą końca.
    Zwraca (DataFrame raportu, statystyki). Jeden wiersz = jedno porównane pole."""
    wiersze = []
    stat = {"zgodne": 0, "roznica": 0, "brak": 0, "promo": 0, "promo_zla": 0}
    dzis = dt.date.today()

    def wart(v):
        return f"{v:.2f}" if v is not None else "brak"

    for _, r in plik_df.iterrows():
        sku = r["SKU"]
        if sku not in sklep:
            wiersze.append({"SKU": sku, "Sklep": "—", "Status": "❓ BRAK NA STRONIE",
                            "Pole": "-", "Na stronie (netto)": "-", "W pliku (netto)": "-"})
            stat["brak"] += 1
            continue

        s = sklep[sku]
        tryb_vat, vat = vat_map.get(s["sklep"], ("netto", 23.0))
        ma_roznice, cos_porownano = False, False

        # ceny — bierzemy netto (indeks 0 z pary netto/brutto)
        for pole, klucz in (("Regular", "regular"), ("Sale", "sale"), ("Omnibus", "omnibus")):
            f_n = netto_brutto(r.get(pole), plik_basis, vat)[0]
            if f_n is None:                       # pola nie ma w pliku — pomijamy
                continue
            if pole == "Omnibus" and not s["on_sale"]:
                continue                          # omnibus tylko dla produktów w aktywnej promocji
            s_n = netto_brutto(s[klucz], tryb_vat, vat)[0]
            cos_porownano = True
            zgodne = s_n is not None and abs(f_n - s_n) <= tol
            if not zgodne:
                ma_roznice = True
            wiersze.append({
                "SKU": sku, "Sklep": s["sklep"],
                "Status": "✅ ZGODNE" if zgodne else "🔴 RÓŻNICA",
                "Pole": pole, "Na stronie (netto)": wart(s_n), "W pliku (netto)": wart(f_n),
            })

        if sprawdz_daty:
            for pole, fv, sv in (("Data od", r.get("Data od"), s["date_from"]),
                                 ("Data do", r.get("Data do"), s["date_to"])):
                fd = norm_date(fv)
                if not fd:
                    continue
                sd = norm_date(sv)
                cos_porownano = True
                zgodne = (fd == sd)
                if not zgodne:
                    ma_roznice = True
                wiersze.append({
                    "SKU": sku, "Sklep": s["sklep"],
                    "Status": "✅ ZGODNE" if zgodne else "🔴 RÓŻNICA",
                    "Pole": pole, "Na stronie (netto)": sd or "brak", "W pliku (netto)": fd,
                })

        # logiczne / prawne kontrole promocji (Omnibus) — jako osobne ostrzeżenia
        for opis in kontrola_logiki(s, dzis):
            wiersze.append({"SKU": sku, "Sklep": s["sklep"], "Status": "🟠 OSTRZEŻENIE",
                            "Pole": opis, "Na stronie (netto)": "-", "W pliku (netto)": "-"})

        # data końca promocji — dla każdego produktu w aktywnej promocji
        if pokaz_promo and s["on_sale"]:
            stat["promo"] += 1
            sd = norm_date(s["date_to"]) or "brak"
            if oczek_data_do:
                if sd == oczek_data_do:
                    st_promo = "✅ PROMOCJA — data OK"
                else:
                    st_promo = "🔴 PROMOCJA — inna data"
                    stat["promo_zla"] += 1
                wiersze.append({"SKU": sku, "Sklep": s["sklep"], "Status": st_promo,
                                "Pole": "Data końca promocji",
                                "Na stronie (netto)": sd, "W pliku (netto)": oczek_data_do})
            else:
                wiersze.append({"SKU": sku, "Sklep": s["sklep"],
                                "Status": "🔵 PROMOCJA — data końca", "Pole": "Data końca promocji",
                                "Na stronie (netto)": sd, "W pliku (netto)": "-"})

        if ma_roznice:
            stat["roznica"] += 1
        elif cos_porownano:
            stat["zgodne"] += 1
        else:                                     # SKU jest na stronie, ale plik nie miał co porównać
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


# ===========================================================================
# AUDYT KOMPLETNOŚCI DANYCH PRODUKTOWYCH
# ===========================================================================

def _strip_html(s):
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def _wyciag_ean_para(obj):
    """EAN/GTIN → (wartość, klucz źródła). Klucz = 'global_unique_id' lub nazwa meta."""
    g = obj.get("global_unique_id")
    if g and str(g).strip():
        return str(g).strip(), "global_unique_id"
    klucze = ("_ean", "ean", "_gtin", "gtin", "_alg_wc_ean", "_wpm_gtin_code",
              "_global_unique_id", "barcode", "_barcode")
    for m in obj.get("meta_data", []):
        if m.get("key") in klucze and str(m.get("value") or "").strip():
            return str(m.get("value")).strip(), m.get("key")
    return "", ""


def _wyciag_ean(obj):
    """EAN/GTIN — tylko wartość (zgodność wsteczna)."""
    return _wyciag_ean_para(obj)[0]


@st.cache_data(ttl=300, show_spinner=False)
def pobierz_audyt_ze_sklepu(sklep_nazwa):
    """Pobiera rozszerzone dane produktów (i wariantów) do audytu kompletności."""
    cfg = SKLEPY[sklep_nazwa]
    url    = st.secrets[cfg["url"]].rstrip("/")
    key    = st.secrets[cfg["key"]]
    secret = st.secrets[cfg["secret"]]

    POLA = ("id,sku,name,type,status,regular_price,sale_price,on_sale,description,"
            "short_description,images,categories,attributes,weight,dimensions,"
            "meta_data,global_unique_id")
    POLA_V = "id,sku,regular_price,on_sale,meta_data,global_unique_id"

    sess = requests.Session()
    sess.auth = (key, secret)

    def _get(endpoint, page, fields):
        r = sess.get(f"{url}/wp-json/wc/v3/{endpoint}",
                     params={"per_page": 100, "page": page, "_fields": fields,
                             "status": "any"}, timeout=30)
        if r.status_code == 401:
            raise RuntimeError(f"[{sklep_nazwa}] 401 — złe klucze API lub brak uprawnień.")
        if r.status_code != 200:
            raise RuntimeError(f"[{sklep_nazwa}] API {r.status_code}: {r.text[:200]}")
        return r

    def fetch_all(endpoint, fields):
        r = _get(endpoint, 1, fields)
        out = list(r.json())
        try:
            total = int(r.headers.get("X-WP-TotalPages", 0) or 0)
        except ValueError:
            total = 0
        if total > 1:
            with cf.ThreadPoolExecutor(max_workers=12) as ex:
                for b in ex.map(lambda p: _get(endpoint, p, fields).json(), range(2, total + 1)):
                    out.extend(b)
        elif len(out) >= 100:                      # nagłówek niewiarygodny — stronicuj do skutku
            page = 2
            while True:
                b = _get(endpoint, page, fields).json()
                if not b:
                    break
                out.extend(b)
                if len(b) < 100:
                    break
                page += 1
        return out

    def num(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    def omni(o):
        for m in o.get("meta_data", []):
            if m.get("key") == "_price-omnibus":
                return num(m.get("value"))
        return None

    def entry(o, typ_woo, parent=""):
        return {
            "sklep": sklep_nazwa, "sku": (o.get("sku") or "").strip(),
            "nazwa": o.get("name") or parent, "typ": typ_woo,
            "status": o.get("status", ""),
            "regular": num(o.get("regular_price")), "on_sale": bool(o.get("on_sale")),
            "omnibus": omni(o), "ean": _wyciag_ean(o),
            "img": len(o.get("images") or []),
            "opis": len(_strip_html(o.get("description"))),
            "opis_short": len(_strip_html(o.get("short_description"))),
            "kat": len(o.get("categories") or []),
            "attrs": len(o.get("attributes") or []),
            "weight": str(o.get("weight") or "").strip(),
            "dims": o.get("dimensions") or {},
        }

    wpisy, zmienne = [], []
    for p in fetch_all("products", POLA):
        wpisy.append(entry(p, p.get("type", "simple")))
        if p.get("type") == "variable":
            zmienne.append((p["id"], p.get("name", "")))

    if zmienne:
        def war(pp):
            pid, pname = pp
            return pname, fetch_all(f"products/{pid}/variations", POLA_V)
        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            for pname, ws in ex.map(war, zmienne):
                for v in ws:
                    wpisy.append(entry(v, "variation", pname))
    return wpisy


def audyt_kompletnosci(wpisy, cfg):
    """cfg: {check_name: bool, 'prog_opis': int}. Zwraca (DataFrame, statystyki)."""
    wiersze, krytyczne = [], 0
    for e in wpisy:
        if not e["sku"]:
            continue
        tresc = e["typ"] in ("simple", "variable")   # treść żyje na produkcie
        cena  = e["typ"] in ("simple", "variation")   # cena/EAN na simple i wariancie

        braki = []  # (opis, "K"/"O")
        run = 0

        def spr(warunek_braku, opis, waga, aktywne):
            nonlocal run
            if not aktywne:
                return
            run += 1
            if warunek_braku:
                braki.append((opis, waga))

        spr(e["img"] == 0,                 "brak zdjęcia",            "K", cfg["zdjecia"] and tresc)
        spr(e["opis"] < cfg["prog_opis"],  f"opis <{cfg['prog_opis']} zn.", "K", cfg["opis"] and tresc)
        spr(e["opis_short"] == 0,          "brak opisu skróconego",  "O", cfg["opis_short"] and tresc)
        spr(not e["ean"],                  "brak EAN",               "K", cfg["ean"] and cena)
        spr(e["kat"] == 0,                 "brak kategorii",         "O", cfg["kategorie"] and tresc)
        spr(not e["weight"] or not all(str(e["dims"].get(k, "")).strip()
                                       for k in ("length", "width", "height")),
            "brak wymiarów/wagi", "O", cfg["wymiary"] and tresc)
        spr(e["regular"] is None or e["regular"] == 0, "brak ceny regularnej", "K",
            cfg["cena"] and cena)
        spr(e["on_sale"] and e["omnibus"] is None, "promocja bez omnibusa", "K",
            cfg["omnibus"] and cena)
        spr(e["attrs"] == 0,               "brak atrybutów",         "O", cfg["atrybuty"] and tresc)
        spr(e["status"] != "publish",      f"status: {e['status']}", "K",
            cfg["status"] and tresc)

        ok = run - len(braki)
        proc = round(100 * ok / run) if run else 100
        ma_kryt = any(w == "K" for _, w in braki)
        if ma_kryt:
            krytyczne += 1
        status = ("🔴 KRYTYCZNE" if ma_kryt else
                  "🟠 BRAKI" if braki else "✅ KOMPLETNE")
        wiersze.append({
            "SKU": e["sku"], "Nazwa": e["nazwa"][:60], "Sklep": e["sklep"],
            "Typ": e["typ"], "Status": status, "Kompletność %": proc,
            "Braki": ", ".join(f"{'🔴' if w == 'K' else '🟠'} {o}" for o, w in braki) or "—",
        })

    df = pd.DataFrame(wiersze).sort_values("Kompletność %").reset_index(drop=True) \
        if wiersze else pd.DataFrame()
    stat = {"produktow": len(wiersze), "krytyczne": krytyczne,
            "srednia": round(df["Kompletność %"].mean(), 1) if len(df) else 0}
    return df, stat


# ===========================================================================
# KOLOROWANIE + EKSPORT
# ===========================================================================

def _kolor_statusu(status):
    """Pastelowe tła + wymuszony ciemny tekst — czytelne niezależnie od tego,
    czy strona jest w jasnym czy ciemnym motywie (kolor komórki jest zawsze
    jasny, więc tekst musi być zawsze ciemny, bez dziedziczenia z motywu)."""
    status = str(status)
    if "ZGODNE" in status or "KOMPLETNE" in status or "data OK" in status:
        return "background-color: #EAF3DE; color: #234D1F"      # zielony
    if "RÓŻNICA" in status or "KRYTYCZNE" in status or "inna data" in status:
        return "background-color: #FBE4E4; color: #7A1F1F"      # czerwony
    if "OSTRZE" in status or "BRAKI" in status or "ZMIANA" in status:
        return "background-color: #FFF3CD; color: #6B5300"      # żółty
    if "PROMOCJA" in status:
        return "background-color: #E7F0FA; color: #1F3A5C"      # niebieski
    if "WYCOFANY" in status:
        return "background-color: #FAEEDA; color: #6B4A1F"
    return "background-color: #F0F0F0; color: #1a1a1a"


def koloruj(df):
    """Wektoryzowane kolorowanie (axis=None): kolor liczony raz na wartość statusu
    i rozprowadzony na wszystkie kolumny — dużo szybsze niż per-wiersz."""
    css = df["Status"].map(_kolor_statusu)
    return pd.DataFrame({kol: css.values for kol in df.columns}, index=df.index)


def do_excela(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Raport")
    return buf.getvalue()


# ===========================================================================
# WIDOK: AUDYT SKLEPU
# ===========================================================================

def tryb_audyt(wybrane_sklepy):
    st.subheader("🔎 Audyt kompletności danych produktowych")
    st.caption("Sprawdza produkty w sklepie — bez wgrywania pliku. Zaznacz, co kontrolować.")

    c = st.columns(4)
    cfg = {}
    cfg["zdjecia"]    = c[0].checkbox("Zdjęcia", True)
    cfg["opis"]       = c[0].checkbox("Opis", True)
    cfg["opis_short"] = c[0].checkbox("Opis skrócony", False)
    cfg["ean"]        = c[1].checkbox("EAN / GTIN", True)
    cfg["kategorie"]  = c[1].checkbox("Kategorie", True)
    cfg["wymiary"]    = c[1].checkbox("Wymiary / waga", False)
    cfg["cena"]       = c[2].checkbox("Cena regularna", True)
    cfg["omnibus"]    = c[2].checkbox("Omnibus w promocji", True)
    cfg["atrybuty"]   = c[2].checkbox("Atrybuty", False)
    cfg["status"]     = c[3].checkbox("Status publikacji", True)
    cfg["prog_opis"]  = c[3].number_input("Min. długość opisu (zn.)", value=200, step=50, min_value=0)

    if st.button("▶️ Uruchom audyt", type="primary"):
        try:
            with st.spinner("Pobieram pełne dane produktów..."):
                wpisy = []
                for nazwa in wybrane_sklepy:
                    wpisy.extend(pobierz_audyt_ze_sklepu(nazwa))
        except Exception as e:
            st.error(f"Błąd połączenia ze sklepem: {e}")
            st.stop()
        df, stat = audyt_kompletnosci(wpisy, cfg)
        st.session_state["audyt"] = {"df": df, "stat": stat}

    wynik = st.session_state.get("audyt")
    if not wynik:
        return
    df, stat = wynik["df"], wynik["stat"]
    if df.empty:
        st.info("Brak produktów do audytu (albo wszystkie kontrole wyłączone).")
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("📦 Produktów", stat["produktow"])
    m2.metric("🔴 Z błędami krytycznymi", stat["krytyczne"])
    m3.metric("📊 Średnia kompletność", f"{stat['srednia']}%")

    st.divider()
    KANON = ["🔴 KRYTYCZNE", "🟠 BRAKI", "✅ KOMPLETNE"]
    obecne = df["Status"].unique().tolist()
    opcje = KANON + [s for s in obecne if s not in KANON]
    wyb = st.multiselect("Filtruj status", opcje, default=obecne)
    widok = df[df["Status"].isin(wyb)]

    st.dataframe(widok.style.apply(koloruj, axis=None), use_container_width=True, height=520)

    st.caption(f"Pobierane pliki zawierają aktualnie przefiltrowane wiersze ({len(widok)}).")
    a, b = st.columns(2)
    buf = io.StringIO()
    widok.to_csv(buf, index=False)
    a.download_button("💾 Pobierz CSV", buf.getvalue(),
                      file_name=f"audyt_kompletnosci_{dt.date.today():%Y-%m-%d}.csv",
                      mime="text/csv")
    b.download_button("📊 Pobierz Excel", do_excela(widok),
                      file_name=f"audyt_kompletnosci_{dt.date.today():%Y-%m-%d}.xlsx",
                      mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ===========================================================================
# WIDOK: PRZYGOTUJ ZMIANY (plik importu WooCommerce)
# ===========================================================================

def _fmt_cena(v):
    return f"{v:.2f}" if v is not None else ""


# Kolumny pliku wynikowego — dokładnie format cennika klienta (kolejność ma znaczenie)
KOLUMNY_EKSPORT = ["ID", "Title", "Parent Product ID", "Product Type", "SKU", "Price",
                   "Regular Price", "Sale Price", "_price-omnibus",
                   "Sale Price Dates From", "Sale Price Dates To"]


@st.cache_data(ttl=300, show_spinner=False)
def pobierz_do_eksportu(sklep_nazwa):
    """Pełne dane do formatu eksportu: produkty (proste + rodzice) oraz warianty,
    z parent_id, price, nazwą i typem. Zwraca listę wpisów."""
    cfg = SKLEPY[sklep_nazwa]
    url    = st.secrets[cfg["url"]].rstrip("/")
    key    = st.secrets[cfg["key"]]
    secret = st.secrets[cfg["secret"]]
    POLA = ("id,sku,name,type,parent_id,price,regular_price,sale_price,on_sale,"
            "date_on_sale_from,date_on_sale_to,meta_data,global_unique_id")
    POLA_V = ("id,sku,price,regular_price,sale_price,on_sale,"
              "date_on_sale_from,date_on_sale_to,meta_data,global_unique_id")
    sess = requests.Session()
    sess.auth = (key, secret)

    def _get(endpoint, page, fields):
        r = sess.get(f"{url}/wp-json/wc/v3/{endpoint}",
                     params={"per_page": 100, "page": page, "_fields": fields,
                             "status": "any"}, timeout=30)
        if r.status_code == 401:
            raise RuntimeError(f"[{sklep_nazwa}] 401 — złe klucze API lub brak uprawnień.")
        if r.status_code != 200:
            raise RuntimeError(f"[{sklep_nazwa}] API {r.status_code}: {r.text[:200]}")
        return r

    def fetch_all(endpoint, fields):
        r = _get(endpoint, 1, fields)
        out = list(r.json())
        try:
            total = int(r.headers.get("X-WP-TotalPages", 0) or 0)
        except ValueError:
            total = 0
        if total > 1:
            with cf.ThreadPoolExecutor(max_workers=12) as ex:
                for b in ex.map(lambda p: _get(endpoint, p, fields).json(), range(2, total + 1)):
                    out.extend(b)
        elif len(out) >= 100:                      # nagłówek niewiarygodny — stronicuj do skutku
            page = 2
            while True:
                b = _get(endpoint, page, fields).json()
                if not b:
                    break
                out.extend(b)
                if len(b) < 100:
                    break
                page += 1
        return out

    def num(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    def omni(o):
        for m in o.get("meta_data", []):
            if m.get("key") == "_price-omnibus":
                return num(m.get("value"))
        return None

    def wpis(o, typ, parent_id, parent_name=""):
        return {"id": o.get("id"), "sku": (o.get("sku") or "").strip(),
                "name": o.get("name") or parent_name, "type": typ, "parent_id": parent_id,
                "price": num(o.get("price")), "regular": num(o.get("regular_price")),
                "sale": num(o.get("sale_price")), "omnibus": omni(o),
                "on_sale": bool(o.get("on_sale")), "date_from": o.get("date_on_sale_from"),
                "date_to": o.get("date_on_sale_to"), "ean": _wyciag_ean_para(o)[0]}

    wpisy, zmienne = [], []
    for p in fetch_all("products", POLA):
        typ = p.get("type", "simple")
        wpisy.append(wpis(p, typ, 0))
        if typ == "variable":
            zmienne.append((p["id"], p.get("name", "")))
    if zmienne:
        def war(pp):
            pid, pname = pp
            return pid, pname, fetch_all(f"products/{pid}/variations", POLA_V)
        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            for pid, pname, ws in ex.map(war, zmienne):
                for v in ws:
                    wpisy.append(wpis(v, "variation", pid, pname))
    return wpisy


NAZWY_MIES = ["Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
              "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"]


def wybor_daty(label, key, domyslna):
    """Wybór daty przez 3 listy: Rok / Miesiąc / Dzień — natychmiastowy skok
    na dowolną datę w całym roku, bez klikania strzałkami. Zwraca datetime.date."""
    st.caption(label)
    rok_bazowy = dt.date.today().year
    lata = list(range(rok_bazowy - 1, rok_bazowy + 4))
    if domyslna.year not in lata:
        lata = sorted(set(lata) | {domyslna.year})
    c1, c2, c3 = st.columns(3)
    rok = c1.selectbox("Rok", lata, index=lata.index(domyslna.year), key=f"{key}_r")
    mies = c2.selectbox("Miesiąc", list(range(1, 13)), index=domyslna.month - 1,
                        key=f"{key}_m", format_func=lambda m: f"{m:02d} – {NAZWY_MIES[m-1]}")
    dni_max = calendar.monthrange(rok, mies)[1]
    dzien = c3.selectbox("Dzień", list(range(1, dni_max + 1)),
                         index=min(domyslna.day, dni_max) - 1, key=f"{key}_d")
    return dt.date(rok, mies, dzien)


def policz_zmiany(plik_df, sku_map, plik_basis, tryb, vat):
    """Liczy zmiany (porównanie w netto) dla SKU obecnych w sklepie. Zwraca podgląd
    oraz mapy wartości docelowych i obecnych (w bazie sklepu) dla zmienionych SKU.
    Puste pole w pliku => zostaje wartość obecna (nic nie kasujemy)."""
    display = []
    final_map, backup_map = {}, {}
    stat = {"zmiany": 0, "bez_zmian": 0, "brak": 0}
    ceny = [("Regular", "regular"), ("Sale", "sale"), ("Omnibus", "omnibus")]
    daty = [("Data od", "date_from"), ("Data do", "date_to")]
    dost_cen = {p for p, _ in ceny if p in plik_df and plik_df[p].notna().any()}
    dost_dat = {p for p, _ in daty if p in plik_df and plik_df[p].notna().any()}

    for _, r in plik_df.iterrows():
        sku = r["SKU"]
        if sku not in sku_map:
            display.append({"SKU": sku, "Pole": "-", "Obecna (netto)": "-",
                            "Nowa (netto)": "-", "Status": "❓ BRAK SKU"})
            stat["brak"] += 1
            continue

        s = sku_map[sku]
        final = {"regular": s["regular"], "sale": s["sale"], "omnibus": s["omnibus"],
                 "date_from": norm_date(s["date_from"]), "date_to": norm_date(s["date_to"])}
        backup = dict(final)
        zmiana = False

        for pole, klucz in ceny:
            if pole not in dost_cen:
                continue
            nn = netto_brutto(r.get(pole), plik_basis, vat)[0]
            if nn is None:
                continue
            cur = netto_brutto(s[klucz], tryb, vat)[0]
            final[klucz] = nn if tryb == "netto" else round(nn * (1 + vat / 100.0), 2)
            if cur is None or abs(nn - cur) > 0.01:
                zmiana = True
                display.append({"SKU": sku, "Pole": pole, "Obecna (netto)": _fmt_cena(cur) or "brak",
                                "Nowa (netto)": _fmt_cena(nn), "Status": "🟠 ZMIANA"})

        # daty promocji tylko dla produktów z ceną Sale (w promocji).
        # Bez Sale => w eksporcie daty PUSTE (czyścimy też odziedziczone ze sklepu).
        ma_sale = final.get("sale") is not None
        if ma_sale:
            for pole, klucz in daty:
                if pole not in dost_dat:
                    continue
                nd = norm_date(r.get(pole))
                if not nd:
                    continue
                cur = norm_date(s[klucz])
                final[klucz] = nd
                if nd != cur:
                    zmiana = True
                    display.append({"SKU": sku, "Pole": pole, "Obecna (netto)": cur or "brak",
                                    "Nowa (netto)": nd, "Status": "🟠 ZMIANA"})
        else:
            final["date_from"] = ""
            final["date_to"] = ""

        if zmiana:
            final_map[sku] = final
            backup_map[sku] = backup
            stat["zmiany"] += 1
        else:
            stat["bez_zmian"] += 1

    return pd.DataFrame(display), final_map, backup_map, stat


def zbuduj_csv_wlasny(values_map, wpisy):
    """Buduje DataFrame w formacie cennika klienta (11 kolumn), z wierszem Parent
    przed wariantami każdej serii. values_map: {sku: {regular,sale,omnibus,date_from,date_to}}."""
    from collections import defaultdict
    sku_e = {e["sku"]: e for e in wpisy if e["sku"]}
    by_id = {e["id"]: e for e in wpisy}
    grupy, proste = defaultdict(list), []
    for sku, vals in values_map.items():
        e = sku_e.get(sku)
        if not e:
            continue
        if e["type"] == "variation" and e["parent_id"]:
            grupy[e["parent_id"]].append((e, vals))
        else:
            proste.append((e, vals))

    def wiersz(idv, title, parent, sku, price, reg, sale, omn, d1, d2):
        return {"ID": idv, "Title": title, "Parent Product ID": parent, "Product Type": idv,
                "SKU": sku, "Price": price, "Regular Price": reg, "Sale Price": sale,
                "_price-omnibus": omn, "Sale Price Dates From": d1, "Sale Price Dates To": d2}

    def prod(e, vals, title):
        aktywna = vals.get("sale") if vals.get("sale") is not None else vals.get("regular")
        return wiersz(e["id"], title, e["parent_id"] or 0, e["sku"], _fmt_cena(aktywna),
                      _fmt_cena(vals.get("regular")), _fmt_cena(vals.get("sale")),
                      _fmt_cena(vals.get("omnibus")), vals.get("date_from") or "",
                      vals.get("date_to") or "")

    rows = []
    for pid in sorted(grupy, key=lambda x: int(x)):
        p = by_id.get(pid)
        if p:                                         # wiersz Parent (seria) przed wariantami
            rows.append(wiersz(p["id"], p["name"], 0, "", _fmt_cena(p.get("price")),
                               "", "", "", "", ""))
        for e, vals in sorted(grupy[pid], key=lambda t: int(t[0]["id"])):
            rows.append(prod(e, vals, p["name"] if p else e["name"]))
    for e, vals in sorted(proste, key=lambda t: int(t[0]["id"])):
        rows.append(prod(e, vals, e["name"]))
    return pd.DataFrame(rows, columns=KOLUMNY_EKSPORT)


def tryb_eksport_zmian(dostepne_sklepy):
    st.subheader("📤 Przygotuj zmiany cen (plik importu WooCommerce)")
    st.caption("Apka NIE zapisuje sama. Tworzy plik CSV, który wgrywasz przez natywny import "
               "WooCommerce (Products → Import → zaznacz 'Update existing products').")

    sklep_nazwa = st.selectbox("Sklep docelowy", dostepne_sklepy)
    plik = st.file_uploader("📄 Plik z NOWYMI cenami (CSV / Excel)",
                            type=["csv", "xlsx", "xls"], key="imp_up")
    if not plik:
        st.info("⬆️ Wgraj plik z nowymi cenami.")
        return

    arkusze = lista_arkuszy(plik)
    arkusz = st.selectbox("Arkusz", arkusze, key="imp_ark") if len(arkusze) > 1 \
        else (arkusze[0] if arkusze else None)
    fsig = sygnatura_pliku(plik)
    auto_hdr = wykryj_wiersz_naglowka(plik, arkusz)
    hdr = st.number_input("Wiersz z nazwami kolumn (nr, od 1)", min_value=1,
                          value=auto_hdr + 1, step=1, key=f"imp_hdr_{fsig}") - 1
    surowy = wczytaj_surowo(plik, arkusz, header_row=hdr)
    st.caption(f"Wczytano {len(surowy)} wierszy.")

    st.subheader("🧩 Mapowanie kolumn")
    sig = sygnatura_kolumn(surowy)   # klucz zależny od pliku -> nowy plik = świeże auto-mapowanie
    auto = auto_mapowanie(list(surowy.columns))
    opcje = ["—"] + list(surowy.columns)
    mapowanie = {}
    kol_ui = st.columns(3)
    for i, pole in enumerate(POLA):
        with kol_ui[i % 3]:
            dom = auto.get(pole)
            idx = opcje.index(dom) if dom in opcje else 0
            mapowanie[pole] = st.selectbox(ETYKIETY.get(pole, pole), opcje, index=idx,
                                           key=f"impmap_{pole}_{sig}")
    if mapowanie.get("SKU", "—") == "—":
        st.error("Wskaż kolumnę SKU — bez niej nie ma jak dopasować produktów.")
        return

    plik_df = normalizuj(surowy, mapowanie)
    plik_basis = st.radio("Ceny w pliku podane jako", ["netto", "brutto"], index=0,
                          horizontal=True, key="imp_basis")

    # --- daty promocji: z pliku / ręcznie z kalendarza / nie ustawiaj ---
    st.markdown("**Daty promocji (Data od / Data do)**")
    zrodlo_dat = st.radio("Źródło dat", ["Z pliku (kolumny)", "Ręcznie (kalendarz)", "Nie ustawiaj"],
                          horizontal=True, key="imp_daty", label_visibility="collapsed")
    if zrodlo_dat.startswith("Ręcznie"):
        dzis = dt.date.today()
        data_od = wybor_daty("📅 Data od", "imp_od", dzis)
        data_do = wybor_daty("📅 Data do", "imp_do", dzis + dt.timedelta(days=30))
        plik_df["Data od"] = data_od.strftime("%Y-%m-%d")
        plik_df["Data do"] = data_do.strftime("%Y-%m-%d")
        st.caption(f"Daty ustawione ręcznie dla wszystkich zmienianych produktów: "
                   f"{data_od:%d.%m.%Y} – {data_do:%d.%m.%Y}")
        if data_do < data_od:
            st.warning("'Data do' jest wcześniejsza niż 'Data od' — sprawdź zakres.")
    elif zrodlo_dat.startswith("Nie"):
        plik_df["Data od"] = None
        plik_df["Data do"] = None
    # „Z pliku" → zostawiamy wartości z mapowania bez zmian

    # --- które daty faktycznie zapisać w pliku eksportu ---
    if not zrodlo_dat.startswith("Nie"):
        zakres_dat = st.radio("Które daty zapisać w eksporcie",
                              ["Obie (od i do)", "Tylko Data od", "Tylko Data do"],
                              horizontal=True, key="imp_zakres_dat",
                              help="Niewybrana data nie trafia do pliku — zostaje wartość obecna w sklepie.")
        if zakres_dat == "Tylko Data od":
            plik_df["Data do"] = None
        elif zakres_dat == "Tylko Data do":
            plik_df["Data od"] = None

    if st.button("🔍 Pokaż zmiany (podgląd)", type="primary"):
        try:
            with st.spinner("Pobieram bieżące dane ze sklepu..."):
                wpisy = pobierz_do_eksportu(sklep_nazwa)
        except Exception as e:
            st.error(f"Błąd połączenia ze sklepem: {e}")
            st.stop()
        kv = pobierz_konfig_vat(sklep_nazwa)
        tryb = kv["tryb"] or "netto"
        vat = kv["vat"] if kv["vat"] is not None else 23.0
        sku_map = {e["sku"]: e for e in wpisy if e["sku"]}
        disp, final_map, backup_map, stat = policz_zmiany(plik_df, sku_map, plik_basis, tryb, vat)
        exp = zbuduj_csv_wlasny(final_map, wpisy)
        bak = zbuduj_csv_wlasny(backup_map, wpisy)
        st.session_state["eksport"] = {
            "disp": disp, "exp": exp, "bak": bak, "stat": stat,
            "sklep": sklep_nazwa, "tryb": tryb, "pobrano": len(sku_map),
            "czas": dt.datetime.now().strftime("%d.%m.%Y, godz. %H:%M"),
        }

    wynik = st.session_state.get("eksport")
    if not wynik:
        return

    st.header("📋 Podgląd zmian (dry-run)")
    st.caption(f"🕒 {wynik['czas']} · sklep: {wynik['sklep']} · ceny w sklepie: {wynik['tryb']} · "
               f"pobrano {wynik.get('pobrano', 0)} SKU ze sklepu")
    stat = wynik["stat"]
    m1, m2, m3 = st.columns(3)
    m1.metric("🟠 Do zmiany (SKU)", stat["zmiany"])
    m2.metric("✅ Bez zmian", stat["bez_zmian"])
    m3.metric("❓ Brak SKU na stronie", stat["brak"])

    disp = wynik["disp"]
    if not disp.empty:
        st.dataframe(disp.style.apply(koloruj, axis=None), use_container_width=True, height=440)

    exp = wynik["exp"]
    if exp.empty:
        st.info("Brak zmian do zapisania — plik zgodny ze sklepem.")
        return

    st.success(f"{stat['zmiany']} produktów ze zmianą · plik importu ma {len(exp)} wierszy "
               f"(z wierszami Parent serii).")
    c1, c2 = st.columns(2)
    c1.download_button("📥 Pobierz plik importu (format Twojego cennika)", exp.to_csv(index=False),
                       file_name=f"import_{wynik['sklep']}_{dt.date.today():%Y-%m-%d}.csv",
                       mime="text/csv", type="primary")
    c2.download_button("🛟 Pobierz backup obecnych wartości", wynik["bak"].to_csv(index=False),
                       file_name=f"backup_{wynik['sklep']}_{dt.date.today():%Y-%m-%d}.csv",
                       mime="text/csv")
    st.warning("⚠️ Zanim zaimportujesz: **zachowaj backup**, przetestuj na 1 serii, sprawdź "
               "dopasowanie po ID. Format = Twój cennik (11 kolumn, Parent przed wariantami). "
               "Uwaga: nie ma kolumny EAN — jeśli chcesz aktualizować EAN, powiem jak dodać.")


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
        auto_vat = st.checkbox("Auto-wykryj VAT / brutto z API", value=True,
                               help="Odczytuje z każdego sklepu: czy ceny są brutto/netto i stawkę VAT.")
        st.caption("Wartości ręczne (użyte, gdy API nie odda ustawień):")
        tryb_man = st.radio("Ceny w sklepie zapisane jako", ["brutto", "netto"], index=1)
        vat_man = st.number_input("Stawka VAT (%)", value=23.0, step=1.0, min_value=0.0)

        vat_map = {}
        for nazwa in wybrane_sklepy:
            tryb, vat = tryb_man, vat_man
            if auto_vat:
                k = pobierz_konfig_vat(nazwa)
                if k["tryb"]:
                    tryb = k["tryb"]
                if k["vat"] is not None:
                    vat = k["vat"]
                if k["tryb"] and k["vat"] is not None:
                    st.caption(f"✅ {nazwa}: {tryb}, VAT {vat}% (z API)")
                else:
                    st.caption(f"⚠️ {nazwa}: brak z API → ręczne ({tryb}, VAT {vat}%)")
            vat_map[nazwa] = (tryb, vat)

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

    tryb = st.radio("Tryb", ["📄 Weryfikacja z pliku", "🔎 Audyt sklepu", "📤 Przygotuj zmiany"],
                    horizontal=True, label_visibility="collapsed")
    st.divider()
    if tryb.startswith("🔎"):
        tryb_audyt(wybrane_sklepy)
        return
    if tryb.startswith("📤"):
        tryb_eksport_zmian(wybrane_sklepy)
        return

    plik_promo = st.file_uploader("📄 Plik z cenami (CSV / Excel)",
                                  type=["csv", "xlsx", "xls"])

    if not plik_promo:
        st.info("⬆️ Wgraj plik z cenami, aby rozpocząć weryfikację.")
        return

    # --- wybór arkusza (Excel) ---
    arkusze = lista_arkuszy(plik_promo)
    arkusz = st.selectbox("Arkusz", arkusze) if len(arkusze) > 1 else (arkusze[0] if arkusze else None)

    # --- wiersz nagłówka (cenniki mają tytuły nad tabelą) ---
    fsig = sygnatura_pliku(plik_promo)
    auto_hdr = wykryj_wiersz_naglowka(plik_promo, arkusz)
    hdr = st.number_input("Wiersz z nazwami kolumn (nr, licząc od 1)",
                          min_value=1, value=auto_hdr + 1, step=1, key=f"hdr_{fsig}",
                          help="Auto-wykryty. Zmień, jeśli nad tabelą są tytuły/scalone komórki.") - 1

    surowy = wczytaj_surowo(plik_promo, arkusz, header_row=hdr)
    st.caption(f"Wczytano {len(surowy)} wierszy, kolumny: {', '.join(map(str, surowy.columns))}")

    # --- mapowanie kolumn ---
    st.subheader("🧩 Mapowanie kolumn")
    st.caption("Auto-wykryte poniżej — popraw, jeśli któraś kolumna została źle dopasowana. Myślnik (—) oznacza: pomiń pole.")
    sig = sygnatura_kolumn(surowy)   # klucz zależny od pliku -> nowy plik = świeże auto-mapowanie
    auto = auto_mapowanie(list(surowy.columns))
    opcje = ["—"] + list(surowy.columns)
    mapowanie = {}
    kolumny_ui = st.columns(3)
    for i, pole in enumerate(POLA):
        with kolumny_ui[i % 3]:
            dom = auto.get(pole)
            idx = opcje.index(dom) if dom in opcje else 0
            mapowanie[pole] = st.selectbox(ETYKIETY.get(pole, pole), opcje, index=idx,
                                           key=f"map_{pole}_{sig}")

    if mapowanie.get("SKU", "—") == "—":
        st.error("Musisz wskazać kolumnę SKU — bez niej nie ma jak dopasować produktów.")
        return

    plik_df = normalizuj(surowy, mapowanie)
    st.success(f"Do weryfikacji: {len(plik_df)} produktów z SKU.")

    # baza cenowa pliku (netto/brutto) — domyślnie jak wykryty tryb sklepu
    bazy_sklepow = {t for t, _ in vat_map.values()}
    dom_basis = list(bazy_sklepow)[0] if len(bazy_sklepow) == 1 else "brutto"
    plik_basis = st.radio(
        "Ceny w pliku podane jako", ["brutto", "netto"],
        index=0 if dom_basis == "brutto" else 1, horizontal=True,
        help=("W jakiej postaci są kwoty w pliku. Eksport z WooCommerce ma ceny tak jak sklep — "
              f"tu wykryto: {dom_basis}. Cennik marketingowy B2C to zwykle brutto."),
    )

    # --- data końca promocji dla produktów w aktywnej promocji ---
    pokaz_promo = st.checkbox("Pokaż datę końca promocji dla produktów w promocji", value=True,
                              help="Po walidacji każdy produkt w aktywnej promocji dostanie wiersz "
                                   "z datą końca — także gdy dat nie ma w pliku.")
    oczek_data_do = None
    if pokaz_promo and st.checkbox("Porównaj z oczekiwaną datą końca promocji"):
        oczek_data_do = wybor_daty("Oczekiwana data końca promocji", "oczek_do",
                                   dt.date.today()).strftime("%Y-%m-%d")

    # --- uruchomienie: policz raz, wynik trzymaj w sesji ---
    if st.button("▶️ Uruchom weryfikację", type="primary"):
        try:
            with st.spinner("Pobieram aktualne ceny ze sklepów..."):
                sklep, kolizje = pobierz_ze_sklepow(wybrane_sklepy)
        except Exception as e:
            st.error(f"Błąd połączenia ze sklepem: {e}")
            st.stop()

        raport, stat = porownaj(plik_df, sklep, vat_map, plik_basis, tol, sprawdz_daty,
                                pokaz_promo, oczek_data_do)
        st.session_state["wynik"] = {
            "raport": raport, "stat": stat,
            "pobrano": len(sklep), "kolizje": len(kolizje),
            "sklepy": len(wybrane_sklepy), "oczek": oczek_data_do,
            "czas": dt.datetime.now().strftime("%d.%m.%Y, godz. %H:%M"),
        }

    # --- render wyniku (poza przyciskiem — nie znika po filtrze/pobraniu) ---
    wynik = st.session_state.get("wynik")
    if not wynik:
        return

    st.header("📋 Wynik walidacji")
    st.caption(f"🕒 Data i godzina walidacji: {wynik['czas']}")
    st.caption(f"Pobrano {wynik['pobrano']} SKU z {wynik['sklepy']} sklepu/ów.")
    if wynik["kolizje"]:
        st.warning(f"{wynik['kolizje']} SKU występuje w obu sklepach — porównano do pierwszego trafionego.")

    stat, pelny = wynik["stat"], wynik["raport"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("✅ Zgodne", stat["zgodne"])
    m2.metric("🔴 Różnice", stat["roznica"])
    m3.metric("❓ Brak na stronie", stat["brak"])
    if wynik.get("oczek"):
        m4.metric("🔴 Zła data końca promo", stat.get("promo_zla", 0),
                  help=f"Produkty w promocji z datą końca inną niż {wynik['oczek']}")
    else:
        m4.metric("🔵 W promocji", stat.get("promo", 0))

    st.divider()
    st.subheader("Raport szczegółowy")
    KANON = ["✅ ZGODNE", "🔴 RÓŻNICA", "🟠 OSTRZEŻENIE", "❓ BRAK NA STRONIE",
             "🔵 PROMOCJA — data końca", "🔴 PROMOCJA — inna data", "✅ PROMOCJA — data OK"]
    obecne = pelny["Status"].unique().tolist()
    opcje_st = KANON + [s for s in obecne if s not in KANON]
    wybrane = st.multiselect("Filtruj status (ZGODNE = zielone)", opcje_st, default=obecne)
    widok = pelny[pelny["Status"].isin(wybrane)]

    st.dataframe(widok.style.apply(koloruj, axis=None),
                 use_container_width=True, height=500)

    # eksport = to, co widać po filtrze (widok), nie cały raport
    st.caption(f"Pobierane pliki zawierają aktualnie przefiltrowane wiersze ({len(widok)}).")
    c1, c2 = st.columns(2)
    buf = io.StringIO()
    widok.to_csv(buf, index=False)
    c1.download_button("💾 Pobierz CSV", buf.getvalue(),
                       file_name=f"raport_weryfikacji_{dt.date.today():%Y-%m-%d}.csv",
                       mime="text/csv")
    c2.download_button("📊 Pobierz Excel", do_excela(widok),
                       file_name=f"raport_weryfikacji_{dt.date.today():%Y-%m-%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ===========================================================================
# START
# ===========================================================================

wstrzyknij_styl()

# ---- zdalny wyłącznik: flaga APP_ENABLED w Streamlit Secrets ----
# Ustaw APP_ENABLED = "false" w Secrets (z dowolnego komputera), aby zablokować dostęp.
if str(st.secrets.get("APP_ENABLED", "true")).strip().lower() == "false":
    st.title("⛔ Aplikacja tymczasowo wyłączona")
    st.info("Narzędzie zostało wstrzymane przez administratora. Spróbuj później.")
    st.stop()

if sprawdz_haslo():
    main()

# ---- stała stopka: wersja + autor ----
st.markdown(
    f"<div style='text-align:center;color:var(--muted,#94A3B8);font-size:12px;padding:16px 0 4px;"
    f"border-top:1px solid var(--linia,#E6EAF0);margin-top:24px'>"
    f"Weryfikator Cen · wersja {WERSJA} · autor: {AUTOR}</div>",
    unsafe_allow_html=True,
)
