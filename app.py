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
import concurrent.futures as cf

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
# STYL (wygląd — nie zmienia logiki)
# ===========================================================================

def wstrzyknij_styl():
    """Wstrzykuje CSS: font Plus Jakarta Sans, jasne tło, białe zaokrąglone
    karty, akcent pomarańczowy. Wyłącznie warstwa wizualna."""
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    :root { --akcent:#F15A29; --tekst:#1A1D1F; --tlo:#F4F5F7; --karta:#FFFFFF; }
    html, body, .stApp, [class*="css"] {
        font-family:'Plus Jakarta Sans','Poppins',system-ui,sans-serif !important;
        color:var(--tekst);
    }
    .stApp { background:var(--tlo); }
    h1,h2,h3 { font-weight:800 !important; letter-spacing:-.02em; }
    [data-testid="stSidebar"] { background:var(--karta); border-right:1px solid #E9EBEE; }
    [data-testid="stMetric"] {
        background:var(--karta); border-radius:18px; padding:18px 22px;
        box-shadow:0 2px 10px rgba(20,20,40,.05); border:1px solid #EDEFF2;
    }
    [data-testid="stFileUploader"], .stDataFrame, [data-testid="stExpander"] {
        background:var(--karta); border-radius:18px; border:1px solid #EDEFF2;
    }
    [data-testid="stFileUploader"] { padding:10px 14px; }
    .stButton>button, .stDownloadButton>button, .stFormSubmitButton>button {
        border-radius:12px; font-weight:600; border:1px solid #E3E6EA; padding:.5rem 1.1rem;
    }
    .stButton>button[kind="primary"], .stFormSubmitButton>button[kind="primary"] {
        background:var(--akcent); border:none; color:#fff;
    }
    .stButton>button[kind="primary"]:hover { background:#d94a1e; }
    input, textarea, [data-baseweb="input"], [data-baseweb="select"]>div {
        border-radius:12px !important;
    }
    [data-baseweb="tag"] { background:var(--akcent) !important; border-radius:8px !important; }
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
        if haslo and haslo == st.secrets.get("APP_HASLO", ""):
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
    POLA_PROD = "id,sku,name,type,regular_price,sale_price,on_sale,date_on_sale_from,date_on_sale_to,meta_data"
    POLA_VAR  = "id,sku,regular_price,sale_price,on_sale,date_on_sale_from,date_on_sale_to,meta_data"

    sess = requests.Session()
    sess.auth = (key, secret)

    def _get(endpoint, page, fields):
        r = sess.get(f"{url}/wp-json/wc/v3/{endpoint}",
                     params={"per_page": 100, "page": page, "_fields": fields}, timeout=30)
        if r.status_code == 401:
            raise RuntimeError(f"[{sklep_nazwa}] 401 — złe klucze API lub brak uprawnień.")
        if r.status_code != 200:
            raise RuntimeError(f"[{sklep_nazwa}] API {r.status_code}: {r.text[:200]}")
        return r

    def fetch_all(endpoint, fields):
        """Pobiera stronę 1, potem pozostałe strony równolegle."""
        r = _get(endpoint, 1, fields)
        out = r.json()
        total = int(r.headers.get("X-WP-TotalPages", 1))
        if total > 1:
            with cf.ThreadPoolExecutor(max_workers=8) as ex:
                for batch in ex.map(lambda p: _get(endpoint, p, fields).json(),
                                    range(2, total + 1)):
                    out.extend(batch)
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
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
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


def porownaj(plik_df, sklep, vat_map, plik_basis, tol, sprawdz_daty):
    """Porównuje plik ze sklepem — WYŁĄCZNIE w wartościach netto.
    Zwraca (DataFrame raportu, statystyki). Jeden wiersz = jedno porównane pole,
    z widocznymi wartościami po obu stronach (także dla zgodnych)."""
    wiersze = []
    stat = {"zgodne": 0, "roznica": 0, "brak": 0}
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


def _wyciag_ean(obj):
    """EAN/GTIN — z pola global_unique_id (Woo 9.2+) lub z meta."""
    g = obj.get("global_unique_id")
    if g and str(g).strip():
        return str(g).strip()
    klucze = ("_ean", "ean", "_gtin", "gtin", "_alg_wc_ean", "_wpm_gtin_code",
              "_global_unique_id", "barcode", "_barcode")
    for m in obj.get("meta_data", []):
        if m.get("key") in klucze and str(m.get("value") or "").strip():
            return str(m.get("value")).strip()
    return ""


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
                     params={"per_page": 100, "page": page, "_fields": fields}, timeout=30)
        if r.status_code == 401:
            raise RuntimeError(f"[{sklep_nazwa}] 401 — złe klucze API lub brak uprawnień.")
        if r.status_code != 200:
            raise RuntimeError(f"[{sklep_nazwa}] API {r.status_code}: {r.text[:200]}")
        return r

    def fetch_all(endpoint, fields):
        r = _get(endpoint, 1, fields)
        out = r.json()
        total = int(r.headers.get("X-WP-TotalPages", 1))
        if total > 1:
            with cf.ThreadPoolExecutor(max_workers=8) as ex:
                for b in ex.map(lambda p: _get(endpoint, p, fields).json(), range(2, total + 1)):
                    out.extend(b)
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
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
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

def koloruj(row):
    status = row["Status"]
    if "ZGODNE" in status or "KOMPLETNE" in status:
        kolor = "background-color: #EAF3DE"          # zielony
    elif "RÓŻNICA" in status or "KRYTYCZNE" in status:
        kolor = "background-color: #FBE4E4"          # czerwony
    elif "OSTRZE" in status or "BRAKI" in status:
        kolor = "background-color: #FFF3CD"          # żółty
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

    st.dataframe(widok.style.apply(koloruj, axis=1), use_container_width=True, height=520)

    a, b = st.columns(2)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    a.download_button("💾 Pobierz CSV", buf.getvalue(),
                      file_name="audyt_kompletnosci.csv", mime="text/csv")
    b.download_button("📊 Pobierz Excel", do_excela(df),
                      file_name="audyt_kompletnosci.xlsx",
                      mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
        tryb_man = st.radio("Ceny w sklepie zapisane jako", ["brutto", "netto"], index=0)
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

    tryb = st.radio("Tryb", ["📄 Weryfikacja z pliku", "🔎 Audyt sklepu"],
                    horizontal=True, label_visibility="collapsed")
    st.divider()
    if tryb.startswith("🔎"):
        tryb_audyt(wybrane_sklepy)
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
    auto_hdr = wykryj_wiersz_naglowka(plik_promo, arkusz)
    hdr = st.number_input("Wiersz z nazwami kolumn (nr, licząc od 1)",
                          min_value=1, value=auto_hdr + 1, step=1,
                          help="Auto-wykryty. Zmień, jeśli nad tabelą są tytuły/scalone komórki.") - 1

    surowy = wczytaj_surowo(plik_promo, arkusz, header_row=hdr)
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

    # baza cenowa pliku (netto/brutto) — domyślnie jak wykryty tryb sklepu
    bazy_sklepow = {t for t, _ in vat_map.values()}
    dom_basis = list(bazy_sklepow)[0] if len(bazy_sklepow) == 1 else "brutto"
    plik_basis = st.radio(
        "Ceny w pliku podane jako", ["brutto", "netto"],
        index=0 if dom_basis == "brutto" else 1, horizontal=True,
        help=("W jakiej postaci są kwoty w pliku. Eksport z WooCommerce ma ceny tak jak sklep — "
              f"tu wykryto: {dom_basis}. Cennik marketingowy B2C to zwykle brutto."),
    )

    # --- uruchomienie: policz raz, wynik trzymaj w sesji ---
    if st.button("▶️ Uruchom weryfikację", type="primary"):
        try:
            with st.spinner("Pobieram aktualne ceny ze sklepów..."):
                sklep, kolizje = pobierz_ze_sklepow(wybrane_sklepy)
        except Exception as e:
            st.error(f"Błąd połączenia ze sklepem: {e}")
            st.stop()

        raport, stat = porownaj(plik_df, sklep, vat_map, plik_basis, tol, sprawdz_daty)
        st.session_state["wynik"] = {
            "raport": raport, "stat": stat,
            "pobrano": len(sklep), "kolizje": len(kolizje),
            "sklepy": len(wybrane_sklepy),
        }

    # --- render wyniku (poza przyciskiem — nie znika po filtrze/pobraniu) ---
    wynik = st.session_state.get("wynik")
    if not wynik:
        return

    st.caption(f"Pobrano {wynik['pobrano']} SKU z {wynik['sklepy']} sklepu/ów.")
    if wynik["kolizje"]:
        st.warning(f"{wynik['kolizje']} SKU występuje w obu sklepach — porównano do pierwszego trafionego.")

    stat, pelny = wynik["stat"], wynik["raport"]
    m1, m2, m3 = st.columns(3)
    m1.metric("✅ Zgodne", stat["zgodne"])
    m2.metric("🔴 Różnice", stat["roznica"])
    m3.metric("❓ Brak na stronie", stat["brak"])

    st.divider()
    st.subheader("Raport szczegółowy")
    KANON = ["✅ ZGODNE", "🔴 RÓŻNICA", "🟠 OSTRZEŻENIE", "❓ BRAK NA STRONIE"]
    obecne = pelny["Status"].unique().tolist()
    opcje_st = KANON + [s for s in obecne if s not in KANON]
    wybrane = st.multiselect("Filtruj status (ZGODNE = zielone)", opcje_st, default=obecne)
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

wstrzyknij_styl()
if sprawdz_haslo():
    main()
