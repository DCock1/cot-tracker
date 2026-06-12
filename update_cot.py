#!/usr/bin/env python3
"""
COT Tracker Updater — ES Mini Futures (13874A)
Läuft jeden Freitag nach 21:30 CEST (15:30 ET CFTC-Release).
Liest COT-Daten von Tradingster, aktualisiert index.html und pusht zu GitHub.
"""

import re
import sys
import json
import subprocess
import tempfile
import os
from datetime import datetime, timezone

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "requests", "beautifulsoup4"])
    import requests
    from bs4 import BeautifulSoup

# ─── Config ───────────────────────────────────────────
COT_URL   = "https://www.tradingster.com/cot/legacy-futures/13874A"
REPO      = "DCock1/cot-tracker"
BRANCH    = "gh-pages"
WORK_DIR  = "/home/user/workspace/cot-tracker"
HTML_FILE = os.path.join(WORK_DIR, "index.html")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

# ─── Fetch & Parse ────────────────────────────────────
def fetch_cot_data():
    """
    Liest die COT-Tabellendaten von Tradingster.
    Gibt Liste von Dicts zurück: date, close, ncLong, ncShort, oi
    """
    print(f"[{datetime.now().isoformat()}] Fetching COT data from Tradingster…")
    resp = requests.get(COT_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Suche nach der Datentabelle (historische Weekly-Daten)
    rows = []

    # Tradingster embeds data in a JS variable or table — try both approaches
    # Approach 1: look for JSON data embedded in script tags
    scripts = soup.find_all("script")
    data_found = False

    for script in scripts:
        text = script.string or ""
        # Look for pattern: date strings with numeric position data
        # Tradingster often has: var chartData = [...] or similar
        if "ncLong" in text or "NonComm" in text or "13874A" in text:
            print("  Found embedded data in script tag")
            # Try to extract JSON arrays
            json_match = re.search(r'\[\s*\{[^[]*"date"[^[]*\}\s*\]', text, re.DOTALL)
            if json_match:
                try:
                    rows = json.loads(json_match.group())
                    data_found = True
                    break
                except:
                    pass

    # Approach 2: parse HTML table
    if not data_found:
        tables = soup.find_all("table")
        for table in tables:
            headers_row = table.find("tr")
            if not headers_row:
                continue
            headers = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]

            # Look for relevant columns
            date_col = next((i for i, h in enumerate(headers) if "date" in h), None)
            nc_long_col = next((i for i, h in enumerate(headers) if "noncomm" in h and "long" in h), None)
            nc_short_col = next((i for i, h in enumerate(headers) if "noncomm" in h and "short" in h), None)

            if date_col is not None and nc_long_col is not None:
                for tr in table.find_all("tr")[1:]:
                    cells = tr.find_all("td")
                    if len(cells) <= max(date_col, nc_long_col or 0, nc_short_col or 0):
                        continue
                    try:
                        row = {
                            "date": cells[date_col].get_text(strip=True),
                            "ncLong": int(cells[nc_long_col].get_text(strip=True).replace(",", "").replace(".", "")),
                        }
                        if nc_short_col is not None:
                            row["ncShort"] = int(cells[nc_short_col].get_text(strip=True).replace(",", "").replace(".", ""))
                        rows.append(row)
                    except (ValueError, IndexError):
                        continue
                if rows:
                    data_found = True
                    break

    # Approach 3: look for chart data arrays in scripts
    if not data_found:
        for script in scripts:
            text = script.string or ""
            # Tradingster price/date arrays
            dates_match = re.search(r'dates\s*=\s*\[([^\]]+)\]', text)
            nc_long_match = re.search(r'nonCommLong\s*=\s*\[([^\]]+)\]', text)
            nc_short_match = re.search(r'nonCommShort\s*=\s*\[([^\]]+)\]', text)
            closes_match = re.search(r'closes?\s*=\s*\[([^\]]+)\]', text)
            oi_match = re.search(r'openInterest\s*=\s*\[([^\]]+)\]', text)

            if dates_match and nc_long_match and nc_short_match:
                dates_raw  = [d.strip().strip('"\'') for d in dates_match.group(1).split(",")]
                longs_raw  = [int(v.strip()) for v in nc_long_match.group(1).split(",")]
                shorts_raw = [int(v.strip()) for v in nc_short_match.group(1).split(",")]
                closes_raw = [float(v.strip()) for v in closes_match.group(1).split(",")] if closes_match else [0]*len(dates_raw)
                oi_raw     = [int(v.strip()) for v in oi_match.group(1).split(",")] if oi_match else [0]*len(dates_raw)

                for i, d in enumerate(dates_raw):
                    try:
                        rows.append({
                            "date": d[:10],
                            "close": closes_raw[i] if i < len(closes_raw) else 0,
                            "ncLong": longs_raw[i],
                            "ncShort": shorts_raw[i],
                            "oi": oi_raw[i] if i < len(oi_raw) else 0,
                        })
                    except IndexError:
                        break
                if rows:
                    data_found = True
                    break

    if not rows:
        raise ValueError("Keine COT-Daten auf der Seite gefunden. Seitenstruktur hat sich möglicherweise geändert.")

    print(f"  Gefundene Datenpunkte: {len(rows)}")
    return rows


# ─── Update HTML ──────────────────────────────────────
def update_html(new_rows):
    """Ersetzt den COT_DATA Block in index.html mit neuen Daten."""
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Build JS array string
    js_lines = []
    for r in new_rows:
        date   = r.get("date", "")[:10]
        close  = r.get("close", 0)
        ncLong = r.get("ncLong", 0)
        ncShort= r.get("ncShort", 0)
        oi     = r.get("oi", 0)
        js_lines.append(
            f'  {{ date:"{date}", close:{close}, ncLong:{ncLong}, ncShort:{ncShort}, oi:{oi} }},'
        )

    new_data_block = "const COT_DATA = [\n" + "\n".join(js_lines) + "\n];"

    # Replace existing COT_DATA block
    pattern = r'const COT_DATA\s*=\s*\[.*?\];'
    updated = re.sub(pattern, new_data_block, content, flags=re.DOTALL)

    if updated == content:
        print("  Keine Änderung in COT_DATA erkannt — Daten möglicherweise bereits aktuell.")
        return False

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(updated)

    print(f"  index.html aktualisiert ({len(new_rows)} Datenpunkte)")
    return True


# ─── Git Push ─────────────────────────────────────────
def git_push(latest_date):
    """Commitet und pusht die aktualisierte index.html zu GitHub gh-pages."""
    os.chdir(WORK_DIR)

    cmds = [
        ["git", "config", "user.email", "cot-bot@github.com"],
        ["git", "config", "user.name", "COT Bot"],
        ["git", "add", "index.html"],
        ["git", "commit", "-m", f"COT Update {latest_date} — wöchentliche Daten"],
        ["git", "push", "origin", "HEAD:gh-pages"],
    ]

    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # "nothing to commit" is fine
            if "nothing to commit" in result.stdout + result.stderr:
                print("  Git: Nichts zu committen.")
                return
            print(f"  Git-Fehler bei '{' '.join(cmd)}': {result.stderr}")
            if cmd[1] not in ["config", "add"]:
                raise RuntimeError(f"Git-Fehler: {result.stderr}")
        else:
            print(f"  Git OK: {' '.join(cmd[:3])}")

    print(f"  Gepusht zu gh-pages ✓")


# ─── Main ────────────────────────────────────────────
def main():
    print("=" * 56)
    print(f"COT Tracker Update — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 56)

    try:
        rows = fetch_cot_data()

        if not rows:
            print("FEHLER: Keine Daten gefunden.")
            sys.exit(1)

        # Sort by date
        rows = sorted(rows, key=lambda r: r.get("date", ""))

        # Get latest date for commit message
        latest = rows[-1]
        latest_date = latest.get("date", "unbekannt")
        print(f"  Neuester Datenpunkt: {latest_date}")

        changed = update_html(rows)

        if changed:
            git_push(latest_date)
            print(f"\n✓ Update erfolgreich — {latest_date}")
        else:
            print(f"\n→ Keine neuen Daten verfügbar (bereits aktuell: {latest_date})")

    except Exception as e:
        print(f"\n✗ FEHLER: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
