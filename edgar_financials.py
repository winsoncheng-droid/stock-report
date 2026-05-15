"""
SEC EDGAR Annual Financials Fetcher
"""

import requests
from typing import Optional

# CHANGE THIS to your real email before deploying.
USER_AGENT = "StockReportTool winsoncheng5@gmail.com"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
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
]
NET_INCOME_TAGS = ["NetIncomeLoss"]
EPS_DILUTED_TAGS = [
    "EarningsPerShareDiluted",
    "IncomeLossFromContinuingOperationsPerDilutedShare",
]


def get_cik(ticker: str) -> Optional[str]:
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
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def get_annual_values(facts: dict, tags: list, unit: str = "USD") -> dict:
    """
    Collect FY 10-K facts across ALL tags in the fallback chain, then for each
    fiscal year keep the most recently filed value. This handles companies that:
      - Switch XBRL tags between filings (e.g. newly public companies like RDDT)
      - File restatements / amendments
      - Have historical data tagged differently than current data
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    all_facts = []

    for tag in tags:
        if tag not in us_gaap:
            continue
        units = us_gaap[tag].get("units", {})
        if unit not in units:
            continue
        for f in units[unit]:
            if (
                f.get("fp") == "FY"
                and f.get("form") in ("10-K", "10-K/A")
                and f.get("fy") is not None
            ):
                all_facts.append(f)

    if not all_facts:
        return {}

    by_year = {}
    for f in all_facts:
        fy = f["fy"]
        if fy not in by_year or f["filed"] > by_year[fy]["filed"]:
            by_year[fy] = f

    return {fy: f["val"] for fy, f in by_year.items()}


def compute_report(ticker: str, years: int = 5) -> dict:
    cik = get_cik(ticker)
    if not cik:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR")

    facts = get_company_facts(cik)
    entity_name = facts.get("entityName", ticker)

    revenue = get_annual_values(facts, REVENUE_TAGS)
    gross_profit = get_annual_values(facts, GROSS_PROFIT_TAGS)
    cogs = get_annual_values(facts, COGS_TAGS)
    operating_income = get_annual_values(facts, OPERATING_INCOME_TAGS)
    da = get_annual_values(facts, DA_TAGS)
    continuing_ops = get_annual_values(facts, CONTINUING_OPS_TAGS)
    net_income = get_annual_values(facts, NET_INCOME_TAGS)
    eps = get_annual_values(facts, EPS_DILUTED_TAGS, unit="USD/shares")

    available_years = sorted(revenue.keys(), reverse=True)[:years + 1]
    available_years = sorted(available_years)

    rows = []
    for fy in available_years:
        rev = revenue.get(fy)
        gp = gross_profit.get(fy)
        if gp is None and rev is not None and cogs.get(fy) is not None:
            gp = rev - cogs[fy]

        op = operating_income.get(fy)
        d_a = da.get(fy)
        ebitda = (op + d_a) if (op is not None and d_a is not None) else None

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

    for i in range(1, len(rows)):
        cur, prev = rows[i], rows[i - 1]
        if cur["revenue"] is not None and prev["revenue"]:
            cur["revenue_growth"] = (cur["revenue"] - prev["revenue"]) / prev["revenue"]
        if cur["eps_diluted"] is not None and prev["eps_diluted"]:
            cur["eps_growth"] = (cur["eps_diluted"] - prev["eps_diluted"]) / prev["eps_diluted"]

    if len(rows) > years:
        rows = rows[-years:]

    return {"ticker": ticker.upper(), "cik": cik, "company": entity_name, "rows": rows}
