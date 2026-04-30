"""
Backfill Daily Tracker — One-Time Script (v2)
----------------------------------------------
1. Pulls ALL RSVPs from Luma
2. Inserts missing date rows (4/13–4/19) into the Daily Tracker
3. Fills Column E/F (New RSVPs) for every date
4. Applies formulas for Cumul. RSVPs, vs. Target, % to Goal, Pacing

Run once via GitHub Actions (manual trigger), then delete.
"""

import requests
import gspread
import os
import json
import time
import base64
from datetime import date, timedelta
from collections import defaultdict
from google.oauth2.service_account import Credentials


# ── CONFIG ──────────────────────────────────────────────────────────
LUMA_API_KEY = os.environ.get("LUMA_API_KEY", "")
LUMA_EVENT_ID = os.environ.get("LUMA_EVENT_ID", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDS_B64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
TRACKER_TAB = "Daily Tracker"
RSVP_GOAL = 167


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
    """Read the header row and find column indices by name.
    Returns a dict like {'New RSVPs': 6, 'Cumul. RSVPs': 7, ...}"""

    # Search first 10 rows for the header row containing 'New RSVPs'
    for row_num in range(1, 11):
        row = worksheet.row_values(row_num)
        for i, cell in enumerate(row):
            if "New RSVPs" in cell:
                # Found the header row — build the column map
                col_map = {}
                for j, header in enumerate(row):
                    header = header.strip()
                    if header:
                        col_map[header] = j + 1  # 1-indexed for gspread
                print(f"✓ Found headers in row {row_num}: {col_map}")
                return col_map, row_num

    print("ERROR: Could not find header row with 'New RSVPs'")
    return None, None


# ── STEP 3: INSERT MISSING DATES & BACKFILL ────────────────────────
def backfill_tracker(daily_counts):
    """Insert 4/13–4/19 rows, fill all RSVP counts, apply formulas."""

    creds_json = json.loads(base64.b64decode(GOOGLE_CREDS_B64))
    credentials = Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(TRACKER_TAB)

    # Find column positions from header row
    col_map, header_row = find_columns(worksheet)
    if not col_map:
        return

    # Column indices we need
    col_date = col_map.get("Date", 1)
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

    # Find the row containing "4/20/26"
    dates = worksheet.col_values(col_date)
    first_date_row = None
    for i, d in enumerate(dates):
        if "4/20" in d.strip():
            first_date_row = i + 1  # 1-indexed
            break

    if not first_date_row:
        print("ERROR: Could not find 4/20/26 in the sheet")
        return

    print(f"  Found 4/20/26 at row {first_date_row}")

    # ── INSERT 7 NEW ROWS (4/13 through 4/19) ──────────────────────
    # Figure out how many columns the sheet has
    header_vals = worksheet.row_values(header_row)
    num_cols = len(header_vals)

    # Insert 7 blank rows above 4/20
    blank_rows = [[''] * num_cols for _ in range(7)]
    worksheet.insert_rows(blank_rows, row=first_date_row)
    print(f"  Inserted 7 rows above row {first_date_row}")

    # After insertion, 4/20 moved down by 7
    # New rows are at first_date_row through first_date_row+6

    # Day-of-week names
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    col_day = col_map.get("Day")

    new_dates = [date(2026, 4, d) for d in range(13, 20)]

    for idx, dt in enumerate(new_dates):
        row = first_date_row + idx
        date_str = f"{dt.month}/{dt.day}/{str(dt.year)[2:]}"
        iso_str = dt.isoformat()
        rsvps = daily_counts.get(iso_str, 0)

        # Column A: Date
        worksheet.update_cell(row, col_date, date_str)

        # Column B: Day (if it exists)
        if col_day:
            worksheet.update_cell(row, col_day, day_names[dt.weekday()])

        # New RSVPs
        worksheet.update_cell(row, col_new, rsvps)

        # Cumul. RSVPs (formula)
        if col_cumul:
            c_new = gspread.utils.rowcol_to_a1(row, col_new)      # e.g. "F8"
            if idx == 0:
                # First row — cumulative = just this row
                worksheet.update_acell(
                    gspread.utils.rowcol_to_a1(row, col_cumul),
                    f"={c_new}"
                )
            else:
                c_prev = gspread.utils.rowcol_to_a1(row - 1, col_cumul)  # previous cumul
                worksheet.update_acell(
                    gspread.utils.rowcol_to_a1(row, col_cumul),
                    f"={c_prev}+{c_new}"
                )

        # RSVP Target (0 for pre-campaign dates)
        if col_target:
            worksheet.update_cell(row, col_target, 0)

        # vs. Target formula: (cumul - target) / target, blank if target = 0
        if col_vs and col_cumul and col_target:
            c_cumul = gspread.utils.rowcol_to_a1(row, col_cumul)
            c_target = gspread.utils.rowcol_to_a1(row, col_target)
            worksheet.update_acell(
                gspread.utils.rowcol_to_a1(row, col_vs),
                f'=IF({c_target}=0,"",({c_cumul}-{c_target})/{c_target})'
            )

        # % to Goal formula: cumul / goal
        if col_pct and col_cumul:
            c_cumul = gspread.utils.rowcol_to_a1(row, col_cumul)
            worksheet.update_acell(
                gspread.utils.rowcol_to_a1(row, col_pct),
                f"={c_cumul}/{RSVP_GOAL}"
            )

        # Pacing formula: On Track if cumul >= target, else Behind
        if col_pacing and col_cumul and col_target:
            c_cumul = gspread.utils.rowcol_to_a1(row, col_cumul)
            c_target = gspread.utils.rowcol_to_a1(row, col_target)
            worksheet.update_acell(
                gspread.utils.rowcol_to_a1(row, col_pacing),
                f'=IF({c_target}=0,"",IF({c_cumul}>={c_target},"On Track","Behind"))'
            )

        print(f"  ✓ {date_str} → {rsvps} new RSVPs")
        time.sleep(0.5)

    # ── FIX 4/20's CUMULATIVE to chain from 4/19 ───────────────────
    row_420 = first_date_row + 7  # 4/20 shifted down by 7
    if col_cumul:
        c_prev = gspread.utils.rowcol_to_a1(row_420 - 1, col_cumul)  # 4/19 cumul
        c_new = gspread.utils.rowcol_to_a1(row_420, col_new)
        worksheet.update_acell(
            gspread.utils.rowcol_to_a1(row_420, col_cumul),
            f"={c_prev}+{c_new}"
        )
        print(f"  ✓ Fixed 4/20 cumulative formula at row {row_420}")

    # ── NOW BACKFILL ANY REMAINING EMPTY ROWS ──────────────────────
    print("\n  Checking remaining rows for empty New RSVPs...")
    # Re-read dates after insertion
    dates = worksheet.col_values(col_date)
    existing_new = worksheet.col_values(col_new)
    while len(existing_new) < len(dates):
        existing_new.append("")

    updates = 0
    for i, cell_date in enumerate(dates):
        cell_date = cell_date.strip()
        if "/" not in cell_date or cell_date.startswith("Date"):
            continue

        try:
            parts = cell_date.split("/")
            month = int(parts[0])
            day = int(parts[1])
            year = 2000 + int(parts[2])
            iso_date = f"{year}-{month:02d}-{day:02d}"
        except (ValueError, IndexError):
            continue

        row_num = i + 1
        current_value = existing_new[i].strip()

        if current_value == "":
            rsvp_count = daily_counts.get(iso_date, 0)
            worksheet.update_cell(row_num, col_new, rsvp_count)
            print(f"  ✓ {cell_date} → {rsvp_count} RSVPs (was empty)")
            updates += 1
            time.sleep(0.5)

    print(f"\n✓ Backfill complete: inserted 7 date rows, filled {updates} additional empty cells")


# ── MAIN ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔄 Backfill Daily Tracker (v2 — with row insertion)")
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
    print("✓ Done — you can delete this script now")
