# -*- coding: utf-8 -*-
import io
import re
import csv
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

import streamlit as st

# ---------- Helpers to persist state ----------
def _get_state():
    if "pdf_bytes" not in st.session_state:
        st.session_state.pdf_bytes = None
    if "txt_bytes" not in st.session_state:
        st.session_state.txt_bytes = None
    if "calc" not in st.session_state:
        st.session_state.calc = None   # dict with daily_totals, daily_by_code, totals_by_code, grand_total
    if "selected_day" not in st.session_state:
        st.session_state.selected_day = None
    return st.session_state

# ---------- PDF reading with fallbacks ----------
def _read_pdf_text_with_pdfplumber_bytes(data: bytes):
    try:
        import pdfplumber
    except Exception:
        return None
    try:
        texts = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                texts.append(page.extract_text() or "")
        return "\n".join(texts)
    except Exception:
        return None

def _read_pdf_text_with_pypdf2_bytes(data: bytes):
    try:
        import PyPDF2
    except Exception:
        return None
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(data))
        texts = [(p.extract_text() or "") for p in reader.pages]
        return "\n".join(texts)
    except Exception:
        return None

def _read_pdf_text_with_pymupdf_bytes(data: bytes):
    try:
        import fitz  # PyMuPDF
    except Exception:
        return None
    try:
        doc = fitz.open(stream=io.BytesIO(data).read(), filetype="pdf")
        texts = []
        for page in doc:
            texts.append(page.get_text() or "")
        return "\n".join(texts)
    except Exception:
        return None

def read_pdf_text_bytes(data: bytes) -> str:
    for fn in (_read_pdf_text_with_pdfplumber_bytes,
               _read_pdf_text_with_pypdf2_bytes,
               _read_pdf_text_with_pymupdf_bytes):
        txt = fn(data)
        if isinstance(txt, str) and txt.strip():
            return txt
    raise RuntimeError("PDF-tekstin luku epäonnistui. Jos PDF on skannattu kuva, tarvitaan OCR.")

# ---------- Parsing & aggregation ----------
@dataclass
class Row:
    code: str
    dt: dt.datetime

DATE_PAT = r"(?:\d{2}\.\d{2}\.\d{4})"
TIME_PAT = r"(?:\d{2}[:\.]\d{2})"
CODE_PAT = r"[A-Z0-9]{5}"
ROW_RE = re.compile(rf"\b({CODE_PAT})\s+({DATE_PAT})\s+({TIME_PAT})\b")

def parse_pdf_rows(text: str):
    rows = []
    for m in ROW_RE.finditer(text):
        code = m.group(1)
        day = m.group(2)
        tim = m.group(3).replace(":", ".")
        try:
            when = dt.datetime.strptime(f"{day} {tim}", "%d.%m.%Y %H.%M")
        except ValueError:
            tim2 = tim.replace(".", ":")
            when = dt.datetime.strptime(f"{day} {tim2}", "%d.%m.%Y %H:%M")
        rows.append(Row(code=code, dt=when))
    return rows

def read_points_txt_bytes(data: bytes):
    mapping = {}
    text = data.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 2:
            continue
        code, pts = parts[0].upper(), parts[-1]
        try:
            mapping[code] = int(pts)
        except ValueError:
            try:
                mapping[code] = int(float(pts))
            except Exception:
                pass
    return mapping

def aggregate(rows, points_map):
    daily_totals = defaultdict(int)
    daily_by_code = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'per_code': 0, 'sum': 0}))
    totals_by_code = defaultdict(lambda: {'count': 0, 'per_code': 0, 'sum': 0})
    grand_total_points = 0

    for r in rows:
        date_key = r.dt.strftime("%Y-%m-%d")
        per_code = points_map.get(r.code, 0)
        d = daily_by_code[date_key][r.code]
        d['count'] += 1
        d['per_code'] = per_code
        d['sum'] = d['count'] * per_code

        t = totals_by_code[r.code]
        t['count'] += 1
        t['per_code'] = per_code
        t['sum'] = t['count'] * per_code

        daily_totals[date_key] += per_code
        grand_total_points += per_code

    return (dict(daily_totals),
            {d: dict(v) for d, v in daily_by_code.items()},
            dict(totals_by_code),
            grand_total_points)

def to_csv_bytes_daily(daily_totals: dict) -> bytes:
    import io, csv
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Paiva", "Pisteet"])
    for d in sorted(daily_totals.keys()):
        w.writerow([d, daily_totals[d]])
    return buf.getvalue().encode("utf-8")

def to_csv_bytes_day_detail(by_code: dict) -> bytes:
    import io, csv
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Koodi", "Kpl", "Pist./koodi", "Pisteet yht."])
    for code in sorted(by_code.keys()):
        d = by_code[code]
        w.writerow([code, d['count'], d['per_code'], d['sum']])
    return buf.getvalue().encode("utf-8")

# ---------- UI ----------
st.set_page_config(page_title="pts", layout="wide")
state = _get_state()

st.title(".")

col1, col2 = st.columns(2)
with col1:
    pdf_file = st.file_uploader("📄 Lataa PDF", type=["pdf"], key="uploader_pdf")
    if pdf_file is not None:
        state.pdf_bytes = pdf_file.read()
with col2:
    txt_file = st.file_uploader("📝 Lataa pisteet.txt", type=["txt", "csv"], key="uploader_txt")
    if txt_file is not None:
        state.txt_bytes = txt_file.read()

btns = st.columns([1,1,6])
with btns[0]:
    compute = st.button("Lataa / Laske pisteet", type="primary")
with btns[1]:
    clear = st.button("Tyhjennä")

if clear:
    state.pdf_bytes = None
    state.txt_bytes = None
    state.calc = None
    state.selected_day = None
    st.experimental_rerun()

# Perform calculation only when user presses the button AND both files are present
if compute:
    if not state.pdf_bytes or not state.txt_bytes:
        st.warning("Valitse sekä PDF että pisteet.txt ennen laskentaa.")
    else:
        try:
            pdf_text = read_pdf_text_bytes(state.pdf_bytes)
            rows = parse_pdf_rows(pdf_text)
            points_map = read_points_txt_bytes(state.txt_bytes)
            daily_totals, daily_by_code, totals_by_code, grand_total = aggregate(rows, points_map)
            state.calc = {
                "daily_totals": daily_totals,
                "daily_by_code": daily_by_code,
                "totals_by_code": totals_by_code,
                "grand_total": grand_total,
            }
            # Default selected day
            days = sorted(daily_by_code.keys())
            if days:
                state.selected_day = days[0] if state.selected_day not in days else state.selected_day
        except Exception as e:
            st.error(f"Virhe: {e}")

# If we have calculation results in state, show them (no need to re-upload)
if state.calc is not None:
    daily_totals = state.calc["daily_totals"]
    daily_by_code = state.calc["daily_by_code"]
    totals_by_code = state.calc["totals_by_code"]
    grand_total = state.calc["grand_total"]

    st.subheader("Kooste (päiväkohtaiset)")
    kpi1, kpi2 = st.columns(2)
    with kpi1:
        st.metric("Kokonaispisteet", grand_total)
    with kpi2:
        st.metric("Päiviä", len(daily_totals))

    import pandas as pd
    if daily_totals:
        df_daily = pd.DataFrame(
            [{"Päivä": d, "Pisteet": pts} for d, pts in daily_totals.items()]
        ).sort_values("Päivä")
        st.dataframe(df_daily, use_container_width=True, hide_index=True)
        st.bar_chart(df_daily.set_index("Päivä"))
        st.download_button(
            "Lataa päiväkohtaiset pisteet (CSV)",
            data=to_csv_bytes_daily(daily_totals),
            file_name="paivakertymat.csv",
            mime="text/csv",
        )

    st.subheader("Päiväkohtainen erittely")
    if daily_by_code:
        # Keep selected day in state
        days = sorted(daily_by_code.keys())
        # Initialize selection if needed
        if state.selected_day not in days and days:
            state.selected_day = days[0]
        state.selected_day = st.selectbox("Valitse päivä", days, index=days.index(state.selected_day) if state.selected_day in days else 0, key="day_select_box")
        day = state.selected_day
        if day:
            by_code = daily_by_code[day]
            df_day = pd.DataFrame(
                [{
                    "Koodi": c,
                    "Kpl": d["count"],
                    "Pist./koodi": d["per_code"],
                    "Pisteet yht.": d["sum"],
                } for c, d in sorted(by_code.items())]
            ).sort_values("Koodi")
            st.dataframe(df_day, use_container_width=True, hide_index=True)
            st.download_button(
                f"Lataa erittely ({day}) (CSV)",
                data=to_csv_bytes_day_detail(by_code),
                file_name=f"erittely_{day}.csv",
                mime="text/csv",
            )

    st.subheader("Yhteenveto (koodit)")
    if totals_by_code:
        df_codes = pd.DataFrame(
            [{
                "Koodi": c,
                "Kpl": d["count"],
                "Pist./koodi": d["per_code"],
                "Pisteet yht.": d["sum"],
            } for c, d in sorted(totals_by_code.items())]
        ).sort_values(["Pisteet yht.", "Koodi"], ascending=[False, True])
        st.dataframe(df_codes, use_container_width=True, hide_index=True)

st.markdown("""---
**Vinkki:** Sovellus säilyttää ladatut tiedostot ja lasketut tulokset istunnon muistissa, joten voit vaihtaa päiviä vapaasti ilman, että palaa latausnäkymään. Tyhjennä tiedot napista **"Tyhjennä"**.
""")
