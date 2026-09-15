"""ITI Code -> numeric ID mapping.

The export CSV only carries the ITI Code (PR09900478), while the Detail page
needs the numeric Detail.aspx?ITI=<id> value.  The intended, non-guessed flow is
that the user saves the Search Results HTML from DevTools (how many pages =
however many the results grid spans) and we extract every code->id pair from
those files.  Nothing about the live form/pagination is assumed.
"""

import json
import os
import re

from bs4 import BeautifulSoup

from .logutil import get_logger

logger = get_logger()

# Matches a Detail.aspx link and captures the numeric ITI id.
_DETAIL_HREF = re.compile(r"Detail\.aspx[^\"']*?ITI[^=]*?=(\d+)", re.I)

# ASP.NET WebForms tokens we may need for a POST-based pagination fallback.
_TOKENS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
           "__EVENTTARGET", "__EVENTARGUMENT")


def _load_map(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unreadable map cache %s (%s); starting fresh.", path, exc)
    return {}


def _save_map(mapping, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)


def merge_maps(existing, new):
    """Merge new pairs into an existing mapping (new wins), never overwrites
    previously mapped codes unless we have a newer value."""
    merged = dict(existing)
    merged.update(new)
    return merged


def _find_code_col(headers):
    for i, h in enumerate(headers):
        hl = h.lower()
        if "iti code" in hl or "code" == hl:
            return i
    return None


def extract_pairs_from_html(html, href_re=_DETAIL_HREF):
    """Extract {ITI_Code: numeric_id} from a search-results HTML blob.

    Strategy, weakest-assumption first:
      1. Find each table that has a header row naming an ITI Code column;
         for every body row take the code cell, then the row's Detail.aspx
         link for the numeric id.
      2. If no headerised table matches, scan every Detail.aspx link page-wide
         and treat its link text as the ITI code (link text is usually the
         code/name the user searched for).

    Returns a dict.  Duplicate codes keep the last id found.
    """
    soup = BeautifulSoup(html, "lxml")
    pairs = {}

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            # Some ASP.NET grids render the header row as <td>. Try that.
            first_row = table.find("tr")
            if first_row is not None:
                headers = [td.get_text(strip=True) for td in first_row.find_all("td")]
        code_idx = _find_code_col(headers)
        if code_idx is None:
            continue

        rows = table.find_all("tr")
        # Skip the header row when it used <td> styling.
        for tr in rows:
            cells = tr.find_all("td")
            if len(cells) <= code_idx:
                continue
            code = cells[code_idx].get_text(strip=True)
            id_link = None
            for a in tr.find_all("a", href=href_re):
                m = href_re.search(a.get("href", ""))
                if m:
                    id_link = m.group(1)
                    break
            # The code cell itself may wrap the link (happens when the code is
            # the clickable search term and the Detail link sits elsewhere).
            if not id_link and code:
                cell_link = cells[code_idx].find("a", href=href_re)
                if cell_link:
                    m = href_re.search(cell_link.get("href", ""))
                    if m:
                        id_link = m.group(1)
            if code and id_link:
                pairs[code] = id_link

    if not pairs:
        for a in soup.find_all("a", href=href_re):
            m = href_re.search(a.get("href", ""))
            if m and a.get_text(strip=True):
                pairs.setdefault(a.get_text(strip=True), m.group(1))

    return pairs


def extract_pairs_from_files(html_files):
    """Extract pairs across several saved HTML files; returns {"code": id}."""
    pairs = {}
    for path in html_files:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
        found = extract_pairs_from_html(html)
        if found:
            logger.debug("Extracted %d code->id pair(s) from %s", len(found), path)
        else:
            logger.warning("No code->id pairs found in %s (wrong page saved?)", path)
        pairs.update(found)
    return pairs


def build_map(input_codes, html_files, cache_path, logger):
    """Build/refresh code_id_map.json from saved search-result pages.

    - Extracts every pair found in the given HTML files.
    - MERGES into the existing cache (re-running a partial run is safe).
    - Resolves the requested input codes against the merged map.
    Returns (matched, missing) tuples of (code, id) / codes.
    """
    existing = _load_map(cache_path)
    new_pairs = extract_pairs_from_files(html_files)
    merged = merge_maps(existing, new_pairs)
    _save_map(merged, cache_path)

    matched, missing = [], []
    seen = set()
    for code in input_codes:
        if code in seen:
            continue
        seen.add(code)
        if code in merged:
            matched.append((code, str(merged[code])))
        else:
            missing.append(code)
    return matched, missing, merged


def do_probe(info):
    """Probe a saved search page HTML blob and return a structural report."""
    # info: dict with keys 'file', 'html'
    html = info["html"]
    soup = BeautifulSoup(html, "lxml")

    inputs = []
    for inp in soup.find_all("input"):
        inputs.append({
            "name": inp.get("name"),
            "type": inp.get("type"),
            "id": inp.get("id"),
            "value_len": len(inp.get("value", "") or ""),
        })
    # Distinct input names (what the POST body would look like).
    names = [i["name"] for i in inputs if i["name"]]

    details = soup.find_all("a", href=_DETAIL_HREF)
    detail_links = [
        (a.get_text(strip=True), a.get("href")) for a in details[:5]
    ]

    code_cells = {
        cell.get_text(strip=True)
        for cell in soup.find_all("td")
        if re.match(r"^[A-Za-z]{2}\d{4,}$", cell.get_text(strip=True))
    }

    return {
        "file": info["file"],
        "input_names_count": len(names),
        "input_names": names[:30],
        "has_viewstate": bool(any(n in _TOKENS for n in names)),
        "detail_link_count": len(details),
        "detail_link_sample": detail_links,
        "code_cell_count": len(code_cells),
        "pair_found": extract_pairs_from_html(html),
    }


# ---------------------------------------------------------------------------
# POST-based pagination fallback -- UNTESTED / PLACEHOLDER
# ---------------------------------------------------------------------------
# Do NOT rely on this yet. The search page structure has NOT been confirmed:
# the form's action URL, the exact search field names, the target control and
# the result-grid POST target are all unknown until the user confirms them via
# DevTools (see the `probe` command). Until then this raises NotImplementedError
# and is never invoked by `map`.
# ---------------------------------------------------------------------------

def _do_post_pagination(html_dir, page_size):  # pragma: no cover - placeholder
    raise NotImplementedError(
        "POST-based pagination is UNTESTED. Confirm the live search form "
        "(action URL, __VIEWSTATE/__EVENTVALIDATION/__EVENTTARGET handling, "
        "grid page size and POST target) via `probe` on a saved Search page, "
        "then wire this up. Prefer saving all result pages as HTML and using "
        "`map --html-dir` instead."
    )