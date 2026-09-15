#!/usr/bin/env python3
"""NCVT MIS ITI scraper CLI.

Flow:
    inspect  ->  map  ->  fetch

Subcommands
    inspect  Read the export CSV/Excel, report columns and whether a numeric
             ITI ID column exists (mapping step required if none).
    probe    Inspect a SAVED search-results HTML file (from DevTools) and
             report its form inputs / Detail links / detected code->id pairs,
             so the idmap logic can be verified against the live site.
    map      Extract ITI Code -> numeric ID pairs from saved search-results
             HTML pages, merge into code_id_map.json (resumable).
    fetch    Fetch every Detail.aspx page (concurrent, cached in iti_raw/),
             parse, and write one combined denormalized CSV + failed_codes.csv
             + a coverage report.
"""

import argparse
import glob
import json
import os
import sys

from iti_scraper import __version__, fetcher, idmap, io
from iti_scraper.logutil import setup_logging, get_logger

logger = get_logger()

DEFAULT_MAP = "code_id_map.json"
DEFAULT_RAW = "iti_raw"


def _read_codes(args):
    df, code_col, id_col = io.inspect_input(args.input)
    if args.code_col:
        code_col = args.code_col
    codes = df[code_col].dropna().astype(str).tolist()
    seen = set()
    uniq = []
    for c in codes:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq, id_col


def cmd_inspect(args):
    df, code_col, id_col = io.inspect_input(args.input)
    if args.code_col:
        code_col = args.code_col
    uniq = df[code_col].dropna().astype(str).unique()
    print(f"Input file    : {args.input}")
    print(f"Rows          : {len(df)}")
    print(f"Unique codes  : {len(uniq)}")
    print(f"ITI Code col  : {code_col!r}")
    if id_col:
        print(f"Numeric ID col: {id_col!r} (no mapping step needed)")
    else:
        print("Numeric ID col: NONE -> run `map` to build code->ID mapping "
              "for every row before `fetch`.")
    return 0


def cmd_probe(args):
    files = _collect_html_files(args)
    if not files:
        _fail_html_collection(args)
    for path in files:
        info = {"file": path,
                "html": open(path, encoding="utf-8", errors="ignore").read()}
        rep = idmap.do_probe(info)
        print(f"\n=== {path} ===")
        print(f"Distinct <input> names : {rep['input_names_count']}")
        for n in rep["input_names"]:
            print(f"   - {n}")
        print(f"Has __VIEWSTATE/EVENTVALIDATION: {rep['has_viewstate']}")
        print(f"Detail.aspx links found : {rep['detail_link_count']}")
        for text, href in rep["detail_link_sample"]:
            print(f"   - text={text!r}  href={href!r}")
        print(f"Code-looking cells      : {rep['code_cell_count']}")
        pairs = rep["pair_found"]
        print(f"code->id pairs parsed   : {len(pairs)}")
        for k, v in list(pairs.items())[:5]:
            print(f"   - {k} -> {v}")
        if not rep["has_viewstate"]:
            print("NOTE: no ASP.NET tokens on page -> plain GET may be enough.")
    return 0


def cmd_map(args):
    files = _collect_html_files(args)
    if not files:
        _fail_html_collection(args)

    uniq_codes, _ = _read_codes(args)
    if args.limit:
        uniq_codes = uniq_codes[: args.limit]

    matched, missing, merged = idmap.build_map(
        uniq_codes, files, args.map_output, logger
    )
    logger.info("Map cache (%s) now holds %d code->id pairs.",
                args.map_output, len(merged))
    logger.info("Requested unique codes: %d", len(uniq_codes))
    logger.info("Resolved in cache      : %d", len(matched))
    for m in matched:
        logger.debug("  %s -> %s", m[0], m[1])
    for code in missing:
        logger.warning("MISSING mapping for %s", code)

    print(f"Mapped keys in cache  : {len(merged)}")
    print(f"Requested codes       : {len(uniq_codes)}")
    print(f"Resolved from cache   : {len(matched)}")
    print(f"Still unmapped        : {len(missing)}")
    print("see scrape.log for the list of unmapped codes. Re-run `map` after "
          "saving more result pages; existing mappings are kept.")
    return 1 if missing else 0


def cmd_fetch(args):
    uniq_codes, _ = _read_codes(args)
    if args.limit:
        uniq_codes = uniq_codes[: args.limit]

    if not os.path.exists(args.map):
        print(f"Mapping file {args.map!r} not found. Run `map` first.", file=sys.stderr)
        return 2
    with open(args.map, encoding="utf-8") as f:
        mapping = json.load(f)

    unresolved = []
    items = []
    for code in uniq_codes:
        iti_id = mapping.get(code)
        if not iti_id:
            unresolved.append((code, "", "no_id_in_map"))
        else:
            items.append({"iti_id": str(iti_id), "iti_code": code})

    logger.info("Items with resolved ID : %d", len(items))
    for code, _, reason in unresolved:
        logger.warning("SKIPPED (no mapping) code=%s reason=%s", code, reason)

    records, failures = fetcher.fetch_all(
        items,
        raw_dir=args.raw_dir,
        workers=args.workers,
        delay=args.delay,
        resume=not args.no_resume,
        verify=not args.insecure,
    )

    for rec in records:
        rec.pop("_source", None)

    rows = io.build_trade_rows(records)
    out_df = io.write_combined_csv(rows, args.output)
    all_failures = unresolved + failures
    io.write_failed_csv(all_failures, args.failed)

    summary = io.validate_coverage(
        input_codes=uniq_codes,
        out_df=out_df,
        failed_codes=[c for c, _, _ in all_failures],
    )

    print()
    print("========== SUMMARY ==========")
    print(f"Input unique ITI codes : {summary['input_total']}")
    print(f"Fetched + parsed OK    : {len(records)}")
    print(f"Rows written to CSV    : {len(out_df)} (1 per trade, main info repeated)")
    print(f"Failed / skipped       : {summary['failed']}")
    print(f"Output CSV             : {args.output}")
    print(f"Failed codes CSV       : {args.failed}")
    print(f"Raw HTML cache dir     : {args.raw_dir}/")
    print("Log file               : scrape.log")
    if summary["unaccounted"]:
        print(f"NOT ACCOUNTED FOR      : {summary['unaccounted']} (see scrape.log)")
        return 1
    if summary["failed"]:
        print("Warning: some records failed; see failed_codes.csv / scrape.log.")
        return 1
    print("Coverage complete: every input code is present in the CSV.")
    return 0


def _collect_html_files(args):
    files = []
    if getattr(args, "html", None):
        files += [args.html]
    if getattr(args, "html_dir", None):
        files += sorted(glob.glob(os.path.join(args.html_dir, "*.html"))
                        + glob.glob(os.path.join(args.html_dir, "*.htm")))
    return files


def _fail_html_collection(args):
    print(
        "\nNo saved Search-results HTML provided.\n"
        "\nHow to get what map/probe needs (from Chrome DevTools):\n"
        "  1. Open the NCVT MIS Search page in Chrome and run your search.\n"
        "  2. DevTools -> Network -> reload -> click the document request.\n"
        "  3. Right-click the Response and 'Save as' -> search_results.html\n"
        "     (repeat for each page of the results grid).\n"
        "  4. Re-run with:  --html search_results.html   or   --html-dir saved_pages/\n"
        "\nFor `probe`, still start with ONE page to verify parsing works.\n",
        file=sys.stderr,
    )
    raise SystemExit(2)


def build_parser():
    p = argparse.ArgumentParser(
        prog="scrape.py",
        description="NCVT MIS ITI data scraper (inspect -> map -> fetch).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--log", default="scrape.log", help="log file (default: scrape.log)")
    p.add_argument("--verbose", action="store_true", help="log to console too")

    sub = p.add_subparsers(dest="command", required=True)

    insp = sub.add_parser("inspect", help="report export file columns/coverage needs")
    insp.add_argument("--input", required=True)
    insp.add_argument("--code-col", help="override the ITI Code column name")
    insp.set_defaults(func=cmd_inspect)

    probe = sub.add_parser("probe", help="verify parsing on ONE saved search page")
    probe.add_argument("--html", help="path to a saved search-results HTML file")
    probe.add_argument("--html-dir", help="directory of saved HTML files")
    probe.set_defaults(func=cmd_probe)

    m = sub.add_parser("map", help="build ITI Code -> numeric ID map")
    m.add_argument("--input", required=True, help="export CSV/Excel")
    m.add_argument("--html", help="a single saved search-results HTML file")
    m.add_argument("--html-dir", help="directory of saved search-results HTML files")
    m.add_argument("--map-output", default=DEFAULT_MAP, help="map cache path")
    m.add_argument("--code-col", help="override ITI Code column (default: auto)")
    m.add_argument("--limit", type=int, help="only resolve first N unique codes")
    m.set_defaults(func=cmd_map)

    f = sub.add_parser("fetch", help="fetch detail pages and build combined CSV")
    f.add_argument("--input", required=True, help="export CSV/Excel")
    f.add_argument("--map", default=DEFAULT_MAP, help="map cache path")
    f.add_argument("--output", default="combined.csv", help="output CSV path")
    f.add_argument("--failed", default="failed_codes.csv",
                   help="failed codes CSV path")
    f.add_argument("--raw-dir", default=DEFAULT_RAW, help="raw HTML cache dir")
    f.add_argument("--code-col", help="override ITI Code column (default: auto)")
    f.add_argument("--workers", type=int, default=4, help="parallel workers")
    f.add_argument("--delay", type=float, default=0.3,
                   help="seconds to sleep before each live request")
    f.add_argument("--limit", type=int, help="only process first N unique codes")
    f.add_argument("--no-resume", action="store_true",
                   help="ignore iti_raw cache and re-fetch everything")
    f.add_argument("--insecure", action="store_true",
                   help="skip TLS certificate verification (ncvtmis cert is "
                        "often expired; use only for this legacy endpoint)")
    f.set_defaults(func=cmd_fetch)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.log, args.verbose)
    try:
        return args.func(args)
    except SystemExit as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fatal error: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)