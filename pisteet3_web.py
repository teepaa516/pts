# -*- coding: utf-8 -*-
import io
import re
import csv
import base64
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

import streamlit as st

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
    raise RuntimeError("PDF-tekstin luku epäonnistui. Tarvitaan pdfplumber, PyPDF2 tai PyMuPDF. Jos PDF on skannattu kuva, tarvitaan OCR.")

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
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Paiva", "Pisteet"])
    for d in sorted(daily_totals.keys()):
        w.writerow([d, daily_totals[d]])
    return buf.getvalue().encode("utf-8")

def to_csv_bytes_day_detail(day: str, by_code: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Koodi", "Kpl", "Pist./koodi", "Pisteet yht."])
    for code in sorted(by_code.keys()):
        d = by_code[code]
        w.writerow([code, d['count'], d['per_code'], d['sum']])
    return buf.getvalue().encode("utf-8")

# ---------- Streamlit UI ----------
st.set_page_config(page_title="Pistekertymä (PDF + TXT)", layout="wide")

st.title("Pistekertymä laskuri (PDF + TXT) — selainversio")

col1, col2 = st.columns(2)
with col1:
    pdf_file = st.file_uploader("📄 Lataa PDF", type=["pdf"])
with col2:
    txt_file = st.file_uploader("📝 Lataa pisteet.txt", type=["txt", "csv"])

compute = st.button("Lataa / Laske pisteet", type="primary", use_container_width=False)

if compute:
    if not pdf_file or not txt_file:
        st.warning("Valitse sekä PDF että pisteet.txt.")
    else:
        try:
            pdf_bytes = pdf_file.read()
            txt_bytes = txt_file.read()
            pdf_text = read_pdf_text_bytes(pdf_bytes)
            rows = parse_pdf_rows(pdf_text)
            points_map = read_points_txt_bytes(txt_bytes)
            daily_totals, daily_by_code, totals_by_code, grand_total = aggregate(rows, points_map)

            # Summary header
            st.subheader("Kooste (päiväkohtaiset)")
            kpi1, kpi2 = st.columns(2)
            with kpi1:
                st.metric("Kokonaispisteet", grand_total)
            with kpi2:
                st.metric("Päiviä", len(daily_totals))

            # Daily totals table & chart
            if daily_totals:
                import pandas as pd
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
            else:
                st.info("Ei päiväkohtaista dataa. Tarkista PDF:n rakenne.")

            # Day detail
            st.subheader("Päiväkohtainen erittely")
            day = None
            if daily_by_code:
                day = st.selectbox("Valitse päivä", sorted(daily_by_code.keys()))
                if day:
                    by_code = daily_by_code[day]
                    import pandas as pd
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
                        data=to_csv_bytes_day_detail(day, by_code),
                        file_name=f"erittely_{day}.csv",
                        mime="text/csv",
                    )
            else:
                st.info("Ei erittelyä saatavilla.")

            # Code totals
            st.subheader("Yhteenveto (koodit)")
            if totals_by_code:
                import pandas as pd
                df_codes = pd.DataFrame(
                    [{
                        "Koodi": c,
                        "Kpl": d["count"],
                        "Pist./koodi": d["per_code"],
                        "Pisteet yht.": d["sum"],
                    } for c, d in sorted(totals_by_code.items())]
                ).sort_values(["Pisteet yht.", "Koodi"], ascending=[False, True])
                st.dataframe(df_codes, use_container_width=True, hide_index=True)
            else:
                st.info("Ei koodikohtaista dataa.")

        except Exception as e:
            st.error(f"Virhe: {e}")

st.markdown("""---
**Huom:** Jos PDF on skannattu kuva (ei tekstiä), pelkkä tekstin poiminta ei onnistu ilman OCR:ää. Silloin PDF:stä ei löydy rivejä mallia `AA1BG 07.10.2025 21.46`.
""")
