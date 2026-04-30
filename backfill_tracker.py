"""
Backfill Daily Tracker — One-Time Script
-----------------------------------------
Pulls ALL RSVPs from Luma, counts registrations per day,
and fills in Column E (New RSVPs) for every date in the
Daily Tracker that's currently empty.

Run once via GitHub Actions (manual trigger), then delete.
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


# ── STEP 2: BACKFILL THE SHEET ─────────────────────────────────────
def backfill_tracker(daily_counts):
    """Write RSVP counts to Column E for every empty row in the tracker."""

    creds_json = json.loads(base64.b64decode(GOOGLE_CREDS_B64))
    credentials = Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(TRACKER_TAB)

    # Read all dates (Column A) and existing New RSVPs (Column E)
    dates = worksheet.col_values(1)
    existing_rsvps = worksheet.col_values(5)

    # Pad existing_rsvps to match dates length (empty cells won't be returned)
    while len(existing_rsvps) < len(dates):
        existing_rsvps.append("")

    updates = 0

    for i, cell_date in enumerate(dates):
        cell_date = cell_date.strip()

        # Skip header rows and non-date rows
        if "/" not in cell_date or cell_date.startswith("Date"):
            continue

        # Parse the sheet date (M/D/YY) into ISO format (YYYY-MM-DD)
        try:
            parts = cell_date.split("/")
            month = int(parts[0])
            day = int(parts[1])
            year = 2000 + int(parts[2])
            iso_date = f"{year}-{month:02d}-{day:02d}"
        except (ValueError, IndexError):
            continue

        # Only backfill rows where Column E is empty
        row_num = i + 1  # gspread is 1-indexed
        current_value = existing_rsvps[i].strip()

        if current_value == "":
            rsvp_count = daily_counts.get(iso_date, 0)
            worksheet.update_cell(row_num, 5, rsvp_count)
            print(f"  ✓ {cell_date} ({iso_date}) → {rsvp_count} RSVPs")
            updates += 1
            # Small delay to avoid Google Sheets rate limits
            time.sleep(0.5)

    print(f"\n✓ Backfill complete: updated {updates} rows")


# ── MAIN ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔄 Backfill Daily Tracker")
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
