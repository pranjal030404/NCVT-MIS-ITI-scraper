#!/usr/bin/env python3
"""Paginate the NCVT MIS ITI Search results grid (State=UTTAR PRADESH,
Status=Active) via ASP.NET postbacks, extracting ITI Code -> numeric ID pairs
from every page and merging them into code_id_map.json.

Starts from a saved first-page response (the file you get by right-clicking
the Search.aspx request in DevTools Network tab -> Save). Re-posts the same
search form with __EVENTTARGET=ctl00$cphBody$dgSearch and
__EVENTARGUMENT=Page$N for N=2..last, carrying forward the viewstate/
eventvalidation each response returns.
"""
import json
import re
import sys
import time

import requests
import urllib3

from iti_scraper.idmap import extract_pairs_from_html, _load_map, _save_map, merge_maps
from iti_scraper.logutil import get_logger, setup_logging

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://www.ncvtmis.gov.in/Pages/ITI/Search.aspx"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": URL,
    "Origin": "https://www.ncvtmis.gov.in",
    "Content-Type": "application/x-www-form-urlencoded",
}

HIDDEN_FIELDS = ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__VIEWSTATEENCRYPTED",
                  "__EVENTVALIDATION", "__SCROLLPOSITIONX", "__SCROLLPOSITIONY",
                  "__LASTFOCUS"]


def _extract_hidden(html):
    out = {}
    for name in HIDDEN_FIELDS:
        m = re.search(r'id="%s" value="([^"]*)"' % re.escape(name), html)
        out[name] = m.group(1) if m else ""
    return out


def _last_page(html):
    nums = [int(n) for n in re.findall(r"doPostBack\(&#39;ctl00\$cphBody\$dgSearch&#39;,&#39;Page\$(\d+)&#39;\)", html)]
    return max(nums) if nums else 1


def _base_form(hidden):
    form = dict(hidden)
    form.update({
        "ctl00$cphBody$ddlScheme": "0",
        "ctl00$cphBody$lbState": "9",
        "ctl00$cphBody$lbDistrict": "-1",
        "ctl00$cphBody$txtITI": "",
        "ctl00$cphBody$lbTrade": "-1",
        "ctl00$cphBody$ddlOtherCategory": "-1",
        "ctl00$cphBody$txtCode": "",
        "ctl00$cphBody$ddlITIScheme": "-1",
    })
    return form


def main(first_page_file, map_output="code_id_map.json", delay=1.0, verify=False,
         cookie=None):
    logger = get_logger()
    with open(first_page_file, encoding="utf-8", errors="ignore") as f:
        html = f.read()

    all_pairs = {}
    pairs = extract_pairs_from_html(html)
    all_pairs.update(pairs)
    last = _last_page(html)
    print(f"Page 1: {len(pairs)} pairs. Detected last page = {last}")

    sess = requests.Session()
    h = dict(HEADERS)
    if cookie:
        h["Cookie"] = cookie
    sess.headers.update(h)
    hidden = _extract_hidden(html)

    page = 2
    while page <= last:
        form = _base_form(hidden)
        form["__EVENTTARGET"] = "ctl00$cphBody$dgSearch"
        form["__EVENTARGUMENT"] = f"Page${page}"
        time.sleep(delay)
        resp = sess.post(URL, data=form, verify=verify, timeout=30)
        resp.raise_for_status()
        html = resp.text
        pairs = extract_pairs_from_html(html)
        if not pairs:
            print(f"Page {page}: 0 pairs returned, stopping (response may be an error page).")
            break
        all_pairs.update(pairs)
        hidden = _extract_hidden(html)
        new_last = _last_page(html)
        if new_last > last:
            last = new_last
        print(f"Page {page}/{last}: {len(pairs)} pairs (total so far {len(all_pairs)})")
        page += 1

    existing = _load_map(map_output)
    merged = merge_maps(existing, all_pairs)
    _save_map(merged, map_output)
    print(f"\nTotal pairs collected this run: {len(all_pairs)}")
    print(f"Map cache now holds: {len(merged)} entries -> {map_output}")


if __name__ == "__main__":
    setup_logging("scrape.log", False)
    import os
    if len(sys.argv) < 2:
        print("usage: paginate_map.py <first_page_saved_file> [map_output]", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "code_id_map.json",
         cookie=os.environ.get("NCVT_COOKIE"))
