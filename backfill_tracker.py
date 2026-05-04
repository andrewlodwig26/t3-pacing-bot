"""
Backfill Daily Tracker — v3 (repair + overwrite)
-------------------------------------------------
1. Pulls ALL RSVPs from Luma
2. Deletes duplicate date rows left by v2
3. Overwrites every New RSVPs cell with real Luma data (not just blanks)
4. Rebuilds cumulative formula chain

Run via GitHub Actions (manual trigger).
"""

import requests
import gspread
import os
import json
import time
import base64
from datetime import date
from collections import defaultdict
from google.oauth2.service_account import Credentials


# ── CONFIG ──────────────────────────────────────────────────────────
LUMA_API_KEY = os.environ.get("LUMA_API_KEY", "")
LUMA_EVENT_ID = os.environ.get("LUMA_EVENT_ID", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDS_B64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
TRACKER_TAB = "Daily Tracker"
RSVP_GOAL = 188


# ── STEP 1: PULL ALL RSVPS FROM LUMA ───────────────────────────────
def get_luma_rsvps():
    """Call Luma API and return {date_string: count} dictionary."""

    url = "https://public-api.luma.com/v1/event/get-guests"
    headers = {"x-luma-api-key": LUMA_API_KEY}
    daily_counts = defaultdict(int)
    total_approved = 0
    cursor = None

    while True:
        params = {
            "event_id": LUMA_EVENT_ID,
            "pagination_limit": 100
        }
        if cursor:
            params["pagination_cursor"] = cursor

        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 429:
            print("⏳ Rate limited — waiting 90 seconds...")
            time.sleep(90)
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 429:
                print("❌ Still rate limited. Stopping.")
                return {}, 0

        if response.status_code != 200:
            print(f"ERROR: Luma API returned status {response.status_code}")
            return {}, 0

        data = response.json()
        entries = data.get("entries", [])

        for guest in entries:
            if guest.get("approval_status") == "approved":
                total_approved += 1
                registered_at = guest.get("registered_at", "")
                if registered_at:
                    day = registered_at.split("T")[0]
                    daily_counts[day] += 1

        cursor = data.get("pagination_cursor")
        if not cursor or len(entries) == 0:
            break

    print(f"✓ Luma: {total_approved} approved guests across {len(daily_counts)} days")
    return dict(daily_counts), total_approved


# ── STEP 2: FIND COLUMN POSITIONS ──────────────────────────────────
def find_columns(worksheet):
    """Read the header row and find column indices by name."""

    for row_num in range(1, 11):
        row = worksheet.row_values(row_num)
        for i, cell in enumerate(row):
            if "New RSVPs" in cell:
                col_map = {}
                for j, header in enumerate(row):
                    header = header.strip()
                    if header:
                        col_map[header] = j + 1  # 1-indexed for gspread
                print(f"✓ Found headers in row {row_num}: {list(col_map.keys())}")
                return col_map, row_num

    print("ERROR: Could not find header row with 'New RSVPs'")
    return None, None


# ── HELPER: parse sheet date to ISO ────────────────────────────────
def sheet_date_to_iso(cell_date):
    """Convert 'm/d/yy' or 'm/d/yyyy' to 'YYYY-MM-DD'. Returns None on failure."""
    cell_date = cell_date.strip()
    if "/" not in cell_date or cell_date.lower().startswith("date"):
        return None
    try:
        parts = cell_date.split("/")
        month = int(parts[0])
        day = int(parts[1])
        year_part = int(parts[2])
        year = year_part if year_part > 100 else 2000 + year_part
        return f"{year}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return None


# ── STEP 3: DELETE DUPLICATE ROWS ──────────────────────────────────
def delete_duplicate_rows(worksheet, col_date, col_day, header_row):
    """Find and delete the 7 duplicate rows inserted by v2.

    v2 duplicates use short day names ('Mon', 'Tue') while originals use
    full names ('Monday', 'Tuesday'). We identify duplicates by finding
    dates that appear more than once and deleting the short-name version.
    """

    dates_col = worksheet.col_values(col_date)
    day_col = worksheet.col_values(col_day) if col_day else []

    # Pad day_col to match length
    while len(day_col) < len(dates_col):
        day_col.append("")

    # Find rows with dates that appear more than once
    date_rows = {}  # iso_date -> list of (row_num, day_name)
    for i, cell_date in enumerate(dates_col):
        row_num = i + 1
        if row_num <= header_row + 1:  # skip header + instruction rows
            continue
        iso = sheet_date_to_iso(cell_date)
        if iso:
            day_name = day_col[i].strip() if i < len(day_col) else ""
            if iso not in date_rows:
                date_rows[iso] = []
            date_rows[iso].append((row_num, day_name))

    # Collect rows to delete — the short-name duplicate
    rows_to_delete = []
    short_names = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    for iso, entries in date_rows.items():
        if len(entries) > 1:
            for row_num, day_name in entries:
                if day_name in short_names:
                    rows_to_delete.append(row_num)

    if not rows_to_delete:
        print("✓ No duplicate rows found — skipping deletion")
        return 0

    # Delete from bottom to top so row indices stay valid
    rows_to_delete.sort(reverse=True)
    for row_num in rows_to_delete:
        worksheet.delete_rows(row_num)
        print(f"  ✗ Deleted duplicate row {row_num}")
        time.sleep(0.3)

    print(f"✓ Deleted {len(rows_to_delete)} duplicate rows")
    return len(rows_to_delete)


# ── STEP 4: OVERWRITE ALL RSVP DATA + REBUILD FORMULAS ────────────
def backfill_tracker(daily_counts):
    """Overwrite New RSVPs for every date row, rebuild cumulative chain."""

    creds_json = json.loads(base64.b64decode(GOOGLE_CREDS_B64))
    credentials = Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(TRACKER_TAB)

    # Find column positions
    col_map, header_row = find_columns(worksheet)
    if not col_map:
        return

    col_date = col_map.get("Date", 1)
    col_day = col_map.get("Day")
    col_new = col_map.get("New RSVPs")
    col_cumul = col_map.get("Cumul. RSVPs")
    col_target = col_map.get("RSVP Target")
    col_vs = col_map.get("vs. Target")
    col_pct = col_map.get("% to Goal")
    col_pacing = col_map.get("Pacing")

    print(f"  Date=col {col_date}, New RSVPs=col {col_new}, "
          f"Cumul=col {col_cumul}, Target=col {col_target}")

    if not col_new:
        print("ERROR: Could not find 'New RSVPs' column")
        return

    # ── DELETE DUPLICATE ROWS FIRST ────────────────────────────────
    deleted = delete_duplicate_rows(worksheet, col_date, col_day, header_row)
    if deleted > 0:
        # Re-read the sheet after deletions
        print("  Re-reading sheet after cleanup...")
        time.sleep(1)

    # ── READ ALL DATE ROWS ─────────────────────────────────────────
    dates_col = worksheet.col_values(col_date)

    # Build list of (row_num, iso_date) for all data rows
    data_rows = []
    for i, cell_date in enumerate(dates_col):
        row_num = i + 1
        if row_num <= header_row + 1:  # skip header + instruction row
            continue
        iso = sheet_date_to_iso(cell_date)
        if iso:
            data_rows.append((row_num, iso))

    print(f"\n  Found {len(data_rows)} date rows to process")

    # ── BUILD ALL UPDATES IN MEMORY ───────────────────────────────
    batch_updates = []  # for values (New RSVPs)
    batch_formulas = []  # for formulas (Cumul, % to Goal)
    running_total = 0

    for idx, (row_num, iso_date) in enumerate(data_rows):
        rsvp_count = daily_counts.get(iso_date, 0)

        # New RSVPs — raw value
        cell_new = gspread.utils.rowcol_to_a1(row_num, col_new)
        batch_updates.append({
            "range": cell_new,
            "values": [[rsvp_count]]
        })

        # Cumulative RSVPs — formula
        if col_cumul:
            cell_cumul_addr = gspread.utils.rowcol_to_a1(row_num, col_cumul)
            if idx == 0:
                batch_formulas.append({
                    "range": cell_cumul_addr,
                    "values": [[f"={cell_new}"]]
                })
            else:
                prev_cumul = gspread.utils.rowcol_to_a1(row_num - 1, col_cumul)
                batch_formulas.append({
                    "range": cell_cumul_addr,
                    "values": [[f"={prev_cumul}+{cell_new}"]]
                })

        # % to Goal — formula
        if col_pct and col_cumul:
            cell_cumul_ref = gspread.utils.rowcol_to_a1(row_num, col_cumul)
            cell_pct_addr = gspread.utils.rowcol_to_a1(row_num, col_pct)
            batch_formulas.append({
                "range": cell_pct_addr,
                "values": [[f"={cell_cumul_ref}/{RSVP_GOAL}"]]
            })

        running_total += rsvp_count
        print(f"  ✓ {iso_date} → {rsvp_count} new (cumul: {running_total})")

    # ── SINGLE BATCH WRITE: values ─────────────────────────────────
    print(f"\n  Batch-writing {len(batch_updates)} New RSVP values...")
    worksheet.batch_update(batch_updates, value_input_option="RAW")

    # ── SINGLE BATCH WRITE: formulas ───────────────────────────────
    print(f"  Batch-writing {len(batch_formulas)} formulas (cumul + % to goal)...")
    worksheet.batch_update(batch_formulas, value_input_option="USER_ENTERED")

    print(f"\n✓ Backfill complete: {len(data_rows)} rows updated, "
          f"running total = {running_total} RSVPs")


# ── MAIN ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔄 Backfill Daily Tracker (v3 — repair + overwrite)")
    print("=" * 50)

    daily_counts, total = get_luma_rsvps()

    if total == 0:
        print("No RSVP data — nothing to backfill.")
    else:
        print(f"\nRSVPs by day:")
        for day in sorted(daily_counts.keys()):
            print(f"  {day}: {daily_counts[day]}")
        print()
        backfill_tracker(daily_counts)

    print("=" * 50)
    print("✓ Done")
