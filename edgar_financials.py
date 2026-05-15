"""
SEC EDGAR Annual Financials Fetcher
-----------------------------------
Pulls annual financial data for any US public company from SEC EDGAR (free, no API key).
Outputs: Revenue, Gross Profit, EBITDA, Earnings from Continuing Ops, Net Income,
Diluted EPS — with YoY growth and margin %.

Usage:
    python edgar_financials.py AAPL          # last 5 years
    python edgar_financials.py MSFT 10       # last 10 years
    python edgar_financials.py NVDA 5 csv    # also save as nvda_financials.csv

Requirements:
    pip install requests
"""

import csv
import sys
import requests
from typing import Optional

# IMPORTANT: SEC EDGAR requires a real User-Agent with your contact info.
# Replace the email below with yours — they will block generic agents.
USER_AGENT = "StockReportTool winsoncheng5@gmail.com"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# XBRL tag fallback chains — try each in order, use the first one that has data.
# These cover ~95% of US public companies across industries and filing years.
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",  # post-ASC 606, most common today
    "Revenues",                                              # generic, older filings
    "SalesRevenueNet",                                       # pre-2018
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueGoodsNet",
]
COGS_TAGS = [
    "CostOfGoodsAndServicesSold",
    "CostOfRevenue",
    "CostOfGoodsSold",
    "CostOfServices",
]
GROSS_PROFIT_TAGS = ["GrossProfit"]
OPERATING_INCOME_TAGS = ["OperatingIncomeLoss"]
DA_TAGS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "Depreciation",
]
CONTINUING_OPS_TAGS = [
    "IncomeLossFromContinuingOperations",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
]
NET_INCOME_TAGS = ["NetIncomeLoss"]
EPS_DILUTED_TAGS = [
    "EarningsPerShareDiluted",
    "IncomeLossFromContinuingOperationsPerDilutedShare",
]


def get_cik(ticker: str) -> Optional[str]:
    """Look up the 10-digit zero-padded CIK for a ticker."""
    url = "https://www.sec.gov/files/company_tickers.json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    ticker = ticker.upper()
    for entry in data.values():
        if entry["ticker"] == ticker:
            return str(entry["cik_str"]).zfill(10)
    return None


def get_company_facts(cik: str) -> dict:
    """Fetch the full XBRL facts JSON for a company."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def get_annual_values(facts: dict, tags: list, unit: str = "USD") -> dict:
    """
    Try each tag in order. For the first tag with FY 10-K data, return
    {fiscal_year: value} using the most recent filing per year (handles restatements).
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        if tag not in us_gaap:
            continue
        units = us_gaap[tag].get("units", {})
        if unit not in units:
            continue
        annual = [
            f for f in units[unit]
            if f.get("fp") == "FY" and f.get("form") in ("10-K", "10-K/A")
        ]
        if not annual:
            continue
        by_year = {}
        for f in annual:
            fy = f.get("fy")
            if fy is None:
                continue
            # Keep the most recently filed value for each fiscal year
            if fy not in by_year or f["filed"] > by_year[fy]["filed"]:
                by_year[fy] = f
        if by_year:
            return {fy: f["val"] for fy, f in by_year.items()}
    return {}


def compute_report(ticker: str, years: int = 5) -> dict:
    """Build the full annual financials report for a ticker."""
    cik = get_cik(ticker)
    if not cik:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR")

    facts = get_company_facts(cik)
    entity_name = facts.get("entityName", ticker)

    # Pull each metric series
    revenue = get_annual_values(facts, REVENUE_TAGS)
    gross_profit = get_annual_values(facts, GROSS_PROFIT_TAGS)
    cogs = get_annual_values(facts, COGS_TAGS)
    operating_income = get_annual_values(facts, OPERATING_INCOME_TAGS)
    da = get_annual_values(facts, DA_TAGS)
    continuing_ops = get_annual_values(facts, CONTINUING_OPS_TAGS)
    net_income = get_annual_values(facts, NET_INCOME_TAGS)
    eps = get_annual_values(facts, EPS_DILUTED_TAGS, unit="USD/shares")

    # Pick the most recent N years that have revenue data
    available_years = sorted(revenue.keys(), reverse=True)[:years + 1]  # +1 for growth calc
    available_years = sorted(available_years)

    rows = []
    for fy in available_years:
        rev = revenue.get(fy)
        # Gross profit: use reported value, otherwise compute Revenue - COGS
        gp = gross_profit.get(fy)
        if gp is None and rev is not None and cogs.get(fy) is not None:
            gp = rev - cogs[fy]

        # EBITDA: Operating Income + D&A (closest you can get from XBRL)
        op = operating_income.get(fy)
        d_a = da.get(fy)
        ebitda = (op + d_a) if (op is not None and d_a is not None) else None

        # If continuing ops isn't reported separately, it equals net income
        cont = continuing_ops.get(fy)
        ni = net_income.get(fy)
        if cont is None:
            cont = ni

        e = eps.get(fy)

        rows.append({
            "fiscal_year": fy,
            "revenue": rev,
            "revenue_growth": None,
            "gross_profit": gp,
            "gross_margin": (gp / rev) if (gp is not None and rev) else None,
            "ebitda": ebitda,
            "ebitda_margin": (ebitda / rev) if (ebitda is not None and rev) else None,
            "continuing_ops": cont,
            "continuing_ops_margin": (cont / rev) if (cont is not None and rev) else None,
            "net_income": ni,
            "net_margin": (ni / rev) if (ni is not None and rev) else None,
            "eps_diluted": e,
            "eps_growth": None,
        })

    # YoY growth
    for i in range(1, len(rows)):
        cur, prev = rows[i], rows[i - 1]
        if cur["revenue"] is not None and prev["revenue"]:
            cur["revenue_growth"] = (cur["revenue"] - prev["revenue"]) / prev["revenue"]
        if cur["eps_diluted"] is not None and prev["eps_diluted"]:
            cur["eps_growth"] = (cur["eps_diluted"] - prev["eps_diluted"]) / prev["eps_diluted"]

    # Drop the extra year used only for the first growth calc
    if len(rows) > years:
        rows = rows[-years:]

    return {"ticker": ticker.upper(), "cik": cik, "company": entity_name, "rows": rows}


def fmt_money(v):
    if v is None: return "—"
    if abs(v) >= 1e9: return f"${v / 1e9:,.2f}B"
    if abs(v) >= 1e6: return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


def fmt_pct(v):
    return f"{v * 100:+.1f}%" if v is not None else "—"


def fmt_eps(v):
    return f"${v:,.2f}" if v is not None else "—"


def print_report(report: dict):
    print(f"\n{report['company']}  ({report['ticker']}, CIK {report['cik']})")
    print("=" * 140)
    cols = [
        ("FY", 6), ("Revenue", 12), ("Rev YoY", 9),
        ("Gross Profit", 14), ("GM%", 8),
        ("EBITDA", 12), ("EBITDA%", 9),
        ("Cont. Ops", 12), ("ContOps%", 9),
        ("Net Income", 12), ("Net %", 8),
        ("Dil EPS", 9), ("EPS YoY", 9),
    ]
    print("".join(f"{name:<{w}}" for name, w in cols))
    print("-" * 140)
    for r in report["rows"]:
        vals = [
            (str(r["fiscal_year"]), 6),
            (fmt_money(r["revenue"]), 12),
            (fmt_pct(r["revenue_growth"]), 9),
            (fmt_money(r["gross_profit"]), 14),
            (fmt_pct(r["gross_margin"]), 8),
            (fmt_money(r["ebitda"]), 12),
            (fmt_pct(r["ebitda_margin"]), 9),
            (fmt_money(r["continuing_ops"]), 12),
            (fmt_pct(r["continuing_ops_margin"]), 9),
            (fmt_money(r["net_income"]), 12),
            (fmt_pct(r["net_margin"]), 8),
            (fmt_eps(r["eps_diluted"]), 9),
            (fmt_pct(r["eps_growth"]), 9),
        ]
        print("".join(f"{v:<{w}}" for v, w in vals))
    print()


def save_csv(report: dict, path: str):
    fields = [
        "fiscal_year", "revenue", "revenue_growth",
        "gross_profit", "gross_margin",
        "ebitda", "ebitda_margin",
        "continuing_ops", "continuing_ops_margin",
        "net_income", "net_margin",
        "eps_diluted", "eps_growth",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in report["rows"]:
            w.writerow(row)
    print(f"Saved {path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python edgar_financials.py TICKER [YEARS] [csv]")
        sys.exit(1)

    ticker = sys.argv[1]
    years = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 5
    want_csv = "csv" in sys.argv

    print(f"Fetching {ticker.upper()} from SEC EDGAR...")
    report = compute_report(ticker, years=years)
    print_report(report)

    if want_csv:
        save_csv(report, f"{ticker.lower()}_financials.csv")
