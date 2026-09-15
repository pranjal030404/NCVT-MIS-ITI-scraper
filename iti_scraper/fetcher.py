"""Concurrent fetch of ITI Detail pages with a resumable raw-HTML cache."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .logutil import get_logger
from .parser import looks_like_error_page, parse_detail

logger = get_logger()

BASE_URL = "https://www.ncvtmis.gov.in/Pages/ITI/Detail.aspx?ITI={}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# A saved response must be at least this big and must not look like an error
# page before we trust the cache entry (guards against partial/error saves).
_MIN_VALID_SIZE = 500


def cache_path_for(iti_id, raw_dir):
    return os.path.join(raw_dir, f"{iti_id}.html")


def cache_valid(path):
    """True only when the cached file exists, is non-trivial, and does not look
    like an ASP.NET error / empty page. Re-fetches anything suspicious."""
    if not os.path.exists(path):
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < _MIN_VALID_SIZE:
        logger.debug("Cache %s too small (%d B) -> re-fetch", path, size)
        return False
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        head = f.read(8000)
    if not head.strip():
        return False
    if looks_like_error_page(head):
        logger.debug("Cache %s looks like an error page -> re-fetch", path)
        return False
    return True


def _run_one(item, resume, delay, raw_dir, base_url, verify=True):
    """Fetch/parse one ITI. Returns (result, failure) where failure is either
    None (success), ('HTTP <code>',...), ('net', msg), or ('parse', msg)."""
    iti_id, code = item["iti_id"], item["iti_code"]
    path = cache_path_for(iti_id, raw_dir)

    if resume and cache_valid(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
        try:
            parsed = parse_detail(html, iti_id)
        except Exception as exc:  # noqa: BLE001
            return None, (code, iti_id, f"parse(cached): {exc}")
        parsed["iti_code"] = code
        parsed["_source"] = "cache"
        return parsed, None

    time.sleep(delay)  # politeness gate regardless of worker count
    try:
        resp = requests.get(base_url.format(iti_id), headers=HEADERS,
                            timeout=20, verify=verify)
    except requests.exceptions.Timeout:
        return None, (code, iti_id, "timeout")
    except requests.exceptions.RequestException as exc:
        return None, (code, iti_id, f"net: {exc}")

    if resp.status_code != 200:
        return None, (code, iti_id, f"HTTP {resp.status_code}")

    html = resp.text or ""
    if looks_like_error_page(html):
        return None, (code, iti_id, "parse: error page returned")

    os.makedirs(raw_dir, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as exc:
        # Non-fatal: parsing continues, just no cache entry.
        logger.warning("Could not cache %s -> %s: %s", iti_id, path, exc)

    try:
        parsed = parse_detail(html, iti_id)
    except Exception as exc:  # noqa: BLE001
        return None, (code, iti_id, f"parse: {exc}")

    if not parsed["trades"] and _looks_empty(parsed):
        return None, (code, iti_id, "parse: empty detail page")

    parsed["iti_code"] = code
    parsed["_source"] = "live"
    return parsed, None


def _looks_empty(parsed):
    non_id = {k: v for k, v in parsed.items() if k not in ("iti_id", "iti_code",
                                                          "trades", "_source")}
    return not non_id


def fetch_all(items, raw_dir, workers=4, delay=0.3, resume=True, base_url=BASE_URL,
              verify=True):
    """Fetch every item concurrently.

    items: [{iti_id, iti_code}, ...]
    Returns (records, failures) where failures are (code, iti_id, reason) tuples.
    """
    records, failures = [], []
    os.makedirs(raw_dir, exist_ok=True)

    if not verify:
        # The legacy NCVT cert is frequently expired; opt-in only.
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    total = len(items)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_run_one, it, resume, delay, raw_dir, base_url,
                          verify): it for it in items}
        for fut in as_completed(futs):
            done += 1
            try:
                result, failure = fut.result()
            except Exception as exc:  # noqa: BLE001
                item = futs[fut]
                failure = (item["iti_code"], item["iti_id"], f"worker: {exc}")
                result = None
            if failure is not None:
                failures.append(failure)
                logger.warning("FAIL(%d/%d) code=%s id=%s reason=%s",
                               done, total, failure[0], failure[1], failure[2])
            else:
                records.append(result)
                logger.info("OK(%d/%d) key=%s",
                            done, total, (result.get("iti_code")
                                          or result.get("iti_id")))
    return records, failures