"""
Stock Report Search — Streamlit UI
-----------------------------------
A clean web UI for searching US public company financials from SEC EDGAR.

Setup:
    pip install streamlit pandas requests
    # Make sure edgar_financials.py is in the same folder as this file.
    # Don't forget to set your User-Agent email in edgar_financials.py.

Run:
    streamlit run app.py
"""

import pandas as pd
import requests
import streamlit as st

from edgar_financials import HEADERS, compute_report

st.set_page_config(
    page_title="Stock Report Search",
    page_icon="📊",
    layout="wide",
)


# ---------- Caching ----------
@st.cache_data(ttl=3600, show_spinner=False)
def load_ticker_index():
    """Load the full ticker -> company name mapping (cached for 1 hour)."""
    r = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return [
        {
            "ticker": e["ticker"],
            "name": e["title"],
            "cik": str(e["cik_str"]).zfill(10),
        }
        for e in data.values()
    ]


@st.cache_data(ttl=3600, show_spinner=False)
def get_report(ticker: str, years: int):
    return compute_report(ticker, years=years)


# ---------- Formatters ----------
def fmt_money(v):
    if v is None or pd.isna(v):
        return "—"
    if abs(v) >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


def fmt_pct(v):
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:+.1f}%"


def fmt_eps(v):
    if v is None or pd.isna(v):
        return "—"
    return f"${v:,.2f}"


# ---------- UI ----------
st.title("📊 Stock Report Search")
st.caption("Annual financials for US public companies · data from SEC EDGAR")

col1, col2, col3 = st.columns([4, 1, 1])
with col1:
    query = st.text_input(
        "Search",
        placeholder="Ticker or company name — e.g. AAPL, Apple, NVDA, Microsoft",
        label_visibility="collapsed",
    )
with col2:
    years = st.selectbox("Years", [3, 5, 10, 15, 20], index=1)
with col3:
    submit = st.button("Search", type="primary", use_container_width=True)

st.divider()

if not query:
    st.info("Enter a ticker (AAPL) or company name (Apple), then click Search.")
    st.stop()

# ---------- Resolve ticker ----------
try:
    index = load_ticker_index()
except Exception as e:
    st.error(f"Could not load ticker index from SEC: {e}")
    st.stop()

q = query.upper().strip()
exact = next((e for e in index if e["ticker"] == q), None)

if exact:
    match = exact
else:
    q_lower = query.lower().strip()
    matches = [e for e in index if q_lower in e["name"].lower()]
    if not matches:
        st.error(f"No ticker or company matching **{query}**.")
        st.stop()
    if len(matches) > 1:
        options = {f"{m['ticker']} — {m['name']}": m for m in matches[:25]}
        choice = st.selectbox("Multiple matches found — pick one:", list(options.keys()))
        match = options[choice]
    else:
        match = matches[0]

# ---------- Fetch ----------
with st.spinner(f"Fetching {match['ticker']} from SEC EDGAR…"):
    try:
        report = get_report(match["ticker"], years)
    except Exception as e:
        st.error(f"Failed to fetch data: {e}")
        st.stop()

rows = report["rows"]
if not rows:
    st.warning("No annual data found for this company.")
    st.stop()

# ---------- Header ----------
st.header(report["company"])
st.caption(f"{report['ticker']} · CIK {report['cik']}")

# ---------- Headline metrics (latest year) ----------
latest = rows[-1]
prev = rows[-2] if len(rows) > 1 else None

def delta_pct(field):
    if prev and prev.get(field) and latest.get(field):
        return f"{((latest[field] - prev[field]) / prev[field]) * 100:+.1f}%"
    return None

st.subheader(f"FY {latest['fiscal_year']}")
m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    st.metric("Revenue", fmt_money(latest["revenue"]), delta_pct("revenue"))
with m2:
    st.metric(
        "Gross Profit",
        fmt_money(latest["gross_profit"]),
        f"{latest['gross_margin'] * 100:.1f}% margin" if latest["gross_margin"] else None,
    )
with m3:
    st.metric(
        "EBITDA",
        fmt_money(latest["ebitda"]),
        f"{latest['ebitda_margin'] * 100:.1f}% margin" if latest["ebitda_margin"] else None,
    )
with m4:
    st.metric(
        "Net Income",
        fmt_money(latest["net_income"]),
        f"{latest['net_margin'] * 100:.1f}% margin" if latest["net_margin"] else None,
    )
with m5:
    st.metric(
        "Diluted EPS",
        fmt_eps(latest["eps_diluted"]),
        delta_pct("eps_diluted"),
    )

st.divider()

# ---------- Full table ----------
st.subheader("Annual financials")

df = pd.DataFrame(rows)
display = df.copy()

money_cols = ["revenue", "gross_profit", "ebitda", "continuing_ops", "net_income"]
pct_cols = [
    "revenue_growth",
    "gross_margin",
    "ebitda_margin",
    "continuing_ops_margin",
    "net_margin",
    "eps_growth",
]
for c in money_cols:
    display[c] = display[c].apply(fmt_money)
for c in pct_cols:
    display[c] = display[c].apply(fmt_pct)
display["eps_diluted"] = display["eps_diluted"].apply(fmt_eps)

display = display[
    [
        "fiscal_year",
        "revenue",
        "revenue_growth",
        "gross_profit",
        "gross_margin",
        "ebitda",
        "ebitda_margin",
        "continuing_ops",
        "continuing_ops_margin",
        "net_income",
        "net_margin",
        "eps_diluted",
        "eps_growth",
    ]
]
display.columns = [
    "FY",
    "Revenue",
    "Rev YoY",
    "Gross Profit",
    "GM %",
    "EBITDA",
    "EBITDA %",
    "Cont. Ops",
    "Cont Ops %",
    "Net Income",
    "Net %",
    "Dil. EPS",
    "EPS YoY",
]
# Most recent year first for the table
display = display.iloc[::-1].reset_index(drop=True)

st.dataframe(display, use_container_width=True, hide_index=True)

# ---------- Charts ----------
st.divider()
st.subheader("Trends")

chart_df = df.copy()
chart_df["fiscal_year"] = chart_df["fiscal_year"].astype(str)
chart_df = chart_df.set_index("fiscal_year")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Revenue ($B)**")
    rev_b = (chart_df["revenue"] / 1e9).rename("Revenue ($B)")
    st.bar_chart(rev_b)
with c2:
    st.markdown("**Margins (%)**")
    margins = pd.DataFrame(
        {
            "Gross %": chart_df["gross_margin"] * 100,
            "EBITDA %": chart_df["ebitda_margin"] * 100,
            "Net %": chart_df["net_margin"] * 100,
        }
    )
    st.line_chart(margins)

# ---------- Download ----------
st.divider()
csv = df.to_csv(index=False)
st.download_button(
    "⬇ Download CSV",
    csv,
    file_name=f"{report['ticker'].lower()}_financials.csv",
    mime="text/csv",
)
