# NCVT-MIS-ITI-scraper

Scrapes full ITI detail records (contact info, principal/chairperson, COE,
trade-wise seats) from the NCVT MIS public site (`ncvtmis.gov.in`), starting
from an "Export" CSV downloaded off the ITI Search page.

Flow: `Search page (DevTools) -> paginate_map.py -> scrape.py fetch -> xlsx`

## One-time setup

```bash
cd C:\vscode\NCVT-MIS-ITI-scraper
pip install -r requirements.txt
```

## Step 1 — Run your search and export the CSV

1. Go to `https://www.ncvtmis.gov.in/Pages/ITI/Search.aspx`.
2. Set your filters (State, District, Type, Status, etc.) and click **Search**.
3. Click **Export** to download the results CSV (this has `ITI Code` but
   no numeric ID — that's what the next steps solve).

## Step 2 — Capture the search-results page + session cookie (for the ID map)

The export CSV has no numeric ID, but `Detail.aspx?ITI=<id>` needs one. We
get every `ITI Code -> numeric ID` pair by replaying the site's own grid
paging requests, starting from one real response you save via DevTools.

1. With the same search results still on screen, open **DevTools → Network**
   tab, then re-run the search (or reload) so the request appears.
2. Find the **Search.aspx** POST request in the list (the one whose response
   is ~200-300 KB and contains the results table).
3. Right-click it → **Save** (or **Save response headers/response as...** —
   whatever your browser calls it) → save it somewhere, e.g.
   `Downloads\Search.aspx`.
4. Still in that request's **Headers** tab, scroll to **Request Headers**
   and copy the full value of the `Cookie:` line (starts with something like
   `__AntiXsrfToken=...; ASP.NET_SessionId=...`).

## Step 3 — Build the ITI Code -> numeric ID map

```bash
export NCVT_COOKIE="paste the full Cookie header value here"
python paginate_map.py "C:\path\to\Downloads\Search.aspx" code_id_map.json
```

This walks every page of the results grid (`Page$2`, `Page$3`, ...,
detecting the true last page as it goes) and merges every code->ID pair it
finds into `code_id_map.json` (existing entries are kept, so it's safe to
re-run / re-run for a different search on top of the same cache).

Sanity check coverage against your CSV:

```bash
python3 -c "
import csv, json
codes = {row['ITI Code'].strip() for row in csv.DictReader(open(r'C:\path\to\export.csv', encoding='utf-8'))}
m = json.load(open('code_id_map.json'))
missing = [c for c in codes if c not in m]
print('total codes:', len(codes), '| missing from map:', len(missing))
print(missing[:20])
"
```

If codes are missing, it usually means the grid had more pages than the
script walked (rare) — re-run Step 3 pointed at a later page's saved
response, or just re-save Search.aspx and re-run; the cache merges.

## Step 4 — Fetch every ITI's detail page and build the combined CSV

```bash
python scrape.py fetch \
  --input "C:\path\to\Downloads\export.csv" \
  --map code_id_map.json \
  --output combined.csv \
  --failed failed_codes.csv \
  --raw-dir iti_raw \
  --workers 6 \
  --delay 0.2 \
  --insecure
```

- `--insecure` is needed because the site's TLS cert is often expired.
- Raw HTML is cached under `iti_raw/<numeric_id>.html`. Re-running the same
  command **resumes** from cache instantly (no re-fetching) unless you pass
  `--no-resume` or delete `iti_raw/`.
- Output is one row per **trade** (ITI info repeated on each row) — so row
  count will be a few times your ITI count.
- `failed_codes.csv` lists anything that failed to fetch/parse; it should
  normally be empty (header-only).

Other useful `scrape.py` subcommands:

```bash
python scrape.py inspect --input export.csv     # check columns / whether mapping is needed
python scrape.py probe --html Search.aspx        # verify a saved page parses correctly
```

## Step 5 — Convert to Excel

```bash
python3 -c "
import pandas as pd
df = pd.read_csv('combined.csv', dtype=str)
df.to_excel('NCVT_MIS_ITI_full_details.xlsx', index=False, engine='openpyxl')
print('written', df.shape)
"
```

## Checking the results

```bash
python3 -c "
import pandas as pd
df = pd.read_csv('combined.csv', dtype=str)
print('rows:', len(df), 'cols:', len(df.columns))
print('unique ITIs:', df['iti_code'].nunique())
"
cat failed_codes.csv     # should be header-only if everything succeeded
```

Spot-check one ITI by filtering `iti_code` in the CSV/xlsx and comparing
against its live Detail page (`Detail.aspx?ITI=<id>` from `code_id_map.json`).

## Troubleshooting

- **"Validation of viewstate MAC failed"** when running `paginate_map.py`:
  your `NCVT_COOKIE` is stale/missing or belongs to a different session than
  the one that produced the saved `Search.aspx` file. Re-save both the
  response and the Cookie header together, from the same live page load.
- **Memory blows up / process hangs while building the CSV**: this was a
  real bug we hit and fixed — `parser.py`'s field extraction must walk
  `<th>Label</th><td>Value</td>` pairs (this site's actual layout), not
  bare 2-`<td>` rows. If you see a `pd.DataFrame` with thousands of columns
  or RAM climbing past a couple GB, check `parse_detail()` in
  `iti_scraper/parser.py` first.
- **`ModuleNotFoundError: No module named 'requests'`**: run
  `pip install -r requirements.txt`.
