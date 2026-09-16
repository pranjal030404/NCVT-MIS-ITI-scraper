"""Parsing of ITI Detail pages into structured dicts."""

import re

from bs4 import BeautifulSoup

from .logutil import get_logger

logger = get_logger()

_TRADE_COL_MAP = {
    "trade code": "trade_code",
    "trade name": "trade_name",
    "name of trade": "trade_name",
    "duration": "duration",
    "duration (months)": "duration",
    "shift": "shift",
    "seats": "seats",
    "sanctioned seats": "seats",
    "sanctioned seats (per year)": "seats",
    "intake": "seats",
    "vacant seats": "vacant_seats",
    "vacancy": "vacant_seats",
    "no. of units": "units",
    "units": "units",
}


def _slug(txt):
    s = re.sub(r"[^a-z0-9]+", "_", txt.strip().lower()).strip("_")
    return s or "col"


def _norm_key(header):
    hl = header.strip().lower()
    if hl in _TRADE_COL_MAP:
        return _TRADE_COL_MAP[hl]
    return _slug(hl)


def _add_main(data, label, value):
    """Insert a label/value pair, de-duplicating clashing labels with _2, _3..."""
    key = _slug(label) or "label"
    orig = key
    n = 2
    while key in data and data[key] != value:
        key = f"{orig}_{n}"
        n += 1
    data[key] = value


def _looks_like_trade_header(headers):
    joined = " ".join(h.lower() for h in headers)
    return "trade" in joined or any(h in _TRADE_COL_MAP for h in headers)


def _parse_trade_table(table):
    """Extract rows from one trade table; returns list of dicts."""
    headers = [c.get_text(strip=True) for c in table.find_all("th")]
    header_row_offset = 0
    if not headers:
        first_row = table.find("tr")
        if first_row is not None:
            headers = [c.get_text(strip=True) for c in first_row.find_all("td")]
            header_row_offset = 1
    if not _looks_like_trade_header(headers):
        return []

    trades = []
    trs = table.find_all("tr")[header_row_offset:]
    for tr in trs:
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        if not cells or all(not c for c in cells):
            continue
        trade = {}
        for i, h in enumerate(headers):
            key = _norm_key(h)
            val = cells[i] if i < len(cells) else ""
            trade[key] = val
        trades.append(trade)
    return trades


def parse_detail(html, iti_id):
    """Parse a raw Detail page into {'iti_id':...,  <main fields>, 'trades':[...]}.

    Main fields come from every 2-cell (label, value) table row on the page
    (covers the ITI details, contact, principal, chairperson, COE sections).
    Trades come from tables whose header names a trade column.
    """
    soup = BeautifulSoup(html, "lxml")
    data = {"iti_id": iti_id, "trades": []}

    for row in soup.select("table.form tr"):
        cells = row.find_all(["th", "td"])
        # Fields render as alternating <th>Label</th><td>Value</td> pairs,
        # possibly several per row, plus an occasional unpaired trailing
        # <td> (e.g. a rowspan'd side panel) which we simply ignore.
        i = 0
        while i + 1 < len(cells):
            th, td = cells[i], cells[i + 1]
            if th.name == "th" and td.name == "td":
                label = th.get_text(strip=True)
                value = td.get_text(strip=True)
                if label:
                    _add_main(data, label, value)
                i += 2
            else:
                i += 1

    for table in soup.find_all("table"):
        data["trades"].extend(_parse_trade_table(table))

    return data


def looks_like_error_page(html):
    """Quick heuristic: is this HTML an ASP.NET error / 'no record' page?"""
    lowered = html[:8000].lower()
    markers = (
        "server error in '/'",
        "object reference not set to an instance",
        "exception of type 'system.web.httpexception'",
        "the resource cannot be found",
    )
    return any(m in lowered for m in markers)