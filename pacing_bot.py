"""
T3 Event Pacing Bot
-------------------
Pulls RSVPs from Luma + email stats from HubSpot, calculates pacing,
and posts a daily summary to Slack.

This is Andrew's first standalone Python script — built to run
independently via GitHub Actions without Cowork.
"""

# ── IMPORTS ──────────────────────────────────────────────────────────
# These load tools ("libraries") that other people wrote.
# "requests" makes API calls. "os" reads environment variables.
# "datetime" handles dates. "json" reads/writes JSON data.

import requests
import gspread
import os
import json
import time
import base64
from datetime import datetime, date
from collections import defaultdict
from google.oauth2.service_account import Credentials


# ── CONFIGURATION ────────────────────────────────────────────────────
# These values come from environment variables (set in GitHub Secrets
# later). For now, you can also set them directly for local testing.
#
# os.environ.get("NAME") reads a value stored outside the code.
# This keeps secrets (API keys) out of your GitHub repo.

LUMA_API_KEY = os.environ.get("LUMA_API_KEY", "")
LUMA_EVENT_ID = os.environ.get("LUMA_EVENT_ID", "")
HUBSPOT_API_KEY = os.environ.get("HUBSPOT_API_KEY", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDS_B64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")

# Sheet details
TRACKER_TAB = "Daily Tracker"

# Event details — hardcoded for now, could move to a config file later
EVENT_NAME = "T3 Live NYC"
EVENT_DATE = date(2026, 5, 12)
RSVP_GOAL = 188
CAMPAIGN_START = date(2026, 4, 9)


# ── FUNCTION 1: GET RSVPS FROM LUMA ─────────────────────────────────
# This function calls the Luma API and counts registrations per day.
#
# What's happening:
# 1. We send a GET request to Luma's API with our API key
# 2. Luma sends back JSON (a list of guest records)
# 3. We loop through each guest, extract the registration date,
#    and count how many registered on each day

def get_luma_rsvps():
    """Call Luma API and return {date_string: count} dictionary."""

    url = "https://public-api.luma.com/v1/event/get-guests"

    # "headers" are metadata sent with the request — like a cover letter
    # attached to your question. The API key goes here.
    headers = {
        "x-luma-api-key": LUMA_API_KEY
    }

    # Count registrations per day
    daily_counts = defaultdict(int)  # like a dictionary that defaults to 0
    total_approved = 0

    # Pagination: Luma returns results in pages. We keep fetching until
    # there are no more pages. "cursor" tells Luma where we left off.
    cursor = None

    while True:
        # "params" are filters added to the URL — like search parameters.
        params = {
            "event_id": LUMA_EVENT_ID,
            "pagination_limit": 100
        }
        if cursor:
            params["pagination_cursor"] = cursor

        # This is the actual API call — the moment your code talks to Luma.
        # requests.get() sends a GET request (meaning "give me data").
        response = requests.get(url, headers=headers, params=params)

        # Handle rate limiting (status 429 = "slow down").
        # Wait 90 seconds (Luma blocks for 1 min, so 90s gives margin)
        # and try exactly ONCE more. If still blocked, stop completely
        # so we don't keep extending the block window.
        if response.status_code == 429:
            print("⏳ Rate limited by Luma — waiting 90 seconds before ONE retry...")
            time.sleep(90)
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 429:
                print("❌ Still rate limited after retry. Stopping to avoid extending the block.")
                return {}, 0

        # Check if the request worked. Status 200 = success.
        if response.status_code != 200:
            print(f"ERROR: Luma API returned status {response.status_code}")
            print(f"Response: {response.text}")
            return {}, 0

        # Parse the JSON response into a Python dictionary.
        # This is where JSON becomes real for you — the API sends text,
        # and json decoding turns it into data you can work with.
        data = response.json()

        # Luma wraps each guest in an "entries" list.
        # Each entry has fields like "approval_status" and "registered_at".
        entries = data.get("entries", [])

        for guest in entries:
            status = guest.get("approval_status", "")
            if status == "approved":
                total_approved += 1
                # Extract just the date portion from the timestamp
                registered_at = guest.get("registered_at", "")
                if registered_at:
                    day = registered_at.split("T")[0]  # "2026-04-13T20:48:28Z" → "2026-04-13"
                    daily_counts[day] += 1

        # Check if there are more pages
        cursor = data.get("pagination_cursor")
        if not cursor or len(entries) == 0:
            break

    print(f"✓ Luma: {total_approved} approved guests across {len(daily_counts)} days")
    return dict(daily_counts), total_approved


# ── FUNCTION 2: GET EMAIL STATS FROM HUBSPOT ────────────────────────
# This calls the HubSpot API to get email sequence performance.
#
# Note: HubSpot's API is more complex than Luma's. For v1 of this bot
# we'll use the campaign analytics endpoint to get opens/replies.
# If the API structure doesn't match, we can adjust after testing.

def get_hubspot_stats():
    """Call HubSpot API and return email sequence stats."""

    # HubSpot uses Bearer token auth (different from Luma's header key)
    headers = {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type": "application/json"
    }

    # For v1, we'll try the sequences endpoint
    # This may need adjustment once we test with real data
    url = "https://api.hubapi.com/automation/v4/sequences"

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print(f"WARNING: HubSpot API returned status {response.status_code}")
        # Return placeholder values — we'll refine this after testing
        return {"enrolled": 0, "opens": 0, "replies": 0}

    data = response.json()
    # TODO: Parse actual sequence stats once we test the real response shape
    # For now, return the raw data so we can inspect it
    print(f"✓ HubSpot: Connected successfully")
    return data


# ── FUNCTION 3: CALCULATE PACING ────────────────────────────────────
# Pure math — no APIs. Takes RSVP count and figures out if we're
# on track, behind, or ahead.

def calculate_pacing(total_rsvps):
    """Calculate pacing metrics against the RSVP goal."""

    today = date.today()
    days_elapsed = (today - CAMPAIGN_START).days
    days_remaining = (EVENT_DATE - today).days
    rsvps_needed = RSVP_GOAL - total_rsvps

    # Avoid division by zero
    daily_pace = total_rsvps / max(days_elapsed, 1)
    needed_pace = rsvps_needed / max(days_remaining, 1)

    # Projected final count if current pace holds
    projected = total_rsvps + (daily_pace * days_remaining)

    # Pacing status
    pct_to_goal = round((total_rsvps / RSVP_GOAL) * 100, 1)

    if projected >= RSVP_GOAL:
        status = "ON TRACK ✅"
    elif projected >= RSVP_GOAL * 0.85:
        status = "CLOSE ⚡"
    else:
        status = "BEHIND ⚠️"

    return {
        "total_rsvps": total_rsvps,
        "rsvp_goal": RSVP_GOAL,
        "pct_to_goal": pct_to_goal,
        "days_remaining": days_remaining,
        "daily_pace": round(daily_pace, 1),
        "needed_pace": round(needed_pace, 1),
        "projected": round(projected),
        "status": status
    }


# ── FUNCTION 4: BUILD SLACK MESSAGE ─────────────────────────────────
# Takes the pacing data and formats it into a Slack-ready message.
# Slack uses a format called "mrkdwn" (their version of markdown).

def build_slack_message(pacing, daily_counts):
    """Format pacing data into a Slack message string."""

    today_str = datetime.now().strftime("%A, %B %d")
    event_date_str = EVENT_DATE.strftime("%B %d")

    # Build the recommendation line based on gap
    gap = pacing["needed_pace"] - pacing["daily_pace"]
    if gap > 5:
        rec = (f"🔴 Projected {pacing['projected']} at current pace "
               f"({pacing['rsvp_goal'] - pacing['projected']} short). "
               f"Need to add contacts or re-engage non-openers.")
    elif gap > 2:
        rec = (f"🟡 Slightly behind — need {pacing['needed_pace']}/day "
               f"vs. current {pacing['daily_pace']}/day. One targeted push closes the gap.")
    else:
        rec = (f"🟢 Pacing well — projected {pacing['projected']} at current rate. "
               f"Stay the course.")

    message = (
        f"📊 *{EVENT_NAME} — Daily Pacing Update | {today_str}*\n\n"
        f"RSVPs: *{pacing['total_rsvps']}* / {pacing['rsvp_goal']} "
        f"({pacing['pct_to_goal']}% to goal)\n"
        f"Pacing: {pacing['status']} | "
        f"Pace: {pacing['daily_pace']}/day (need {pacing['needed_pace']}/day)\n"
        f"Days to Event: {pacing['days_remaining']} ({event_date_str})\n"
        f"Projected Final: {pacing['projected']} RSVPs\n\n"
        f"📈 *Read:* {rec}\n\n"
        f"📝 _Bot-generated. Reply in thread to discuss._"
    )

    return message


# ── FUNCTION 5: POST TO SLACK ────────────────────────────────────────
# Sends the formatted message to Slack via webhook.
# A webhook is just a URL that accepts incoming data.

def post_to_slack(message):
    """Send a message to Slack via incoming webhook."""

    if not SLACK_WEBHOOK_URL:
        print("\n📋 SLACK MESSAGE (no webhook configured — printing instead):\n")
        print(message)
        print()
        return True

    # This is a POST request (meaning "here's data for you to process")
    # vs. the GET requests above (meaning "give me data").
    payload = {"text": message}
    response = requests.post(SLACK_WEBHOOK_URL, json=payload)

    if response.status_code == 200:
        print("✓ Posted to Slack successfully")
        return True
    else:
        print(f"ERROR: Slack returned status {response.status_code}")
        return False


# ── FUNCTION 6: WRITE RSVPS TO DAILY TRACKER ────────────────────────
# Finds today's date in the Daily Tracker tab and writes the daily
# RSVP count to Column E (New RSVPs). The sheet's formulas handle
# cumulative totals, pacing, and % to goal automatically.

def write_to_daily_tracker(daily_counts):
    """Write today's new RSVP count to the Daily Tracker Google Sheet."""

    if not GOOGLE_CREDS_B64 or not GOOGLE_SHEET_ID:
        print("⏭️  No Google Sheets credentials — skipping tracker update")
        return False

    # Authenticate with Google (same service account as attendee sync)
    creds_json = json.loads(base64.b64decode(GOOGLE_CREDS_B64))
    credentials = Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(credentials)

    # Open the sheet and select the Daily Tracker tab
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(TRACKER_TAB)

    # Read all dates from Column A to find today's row
    # Dates in the sheet are formatted like "4/30/26"
    today = date.today()
    today_short = f"{today.month}/{today.day}/{str(today.year)[2:]}"

    dates = worksheet.col_values(1)  # Column A

    target_row = None
    for i, cell_date in enumerate(dates):
        if cell_date.strip() == today_short:
            target_row = i + 1  # gspread is 1-indexed
            break

    if not target_row:
        print(f"⚠️  Could not find today's date ({today_short}) in Daily Tracker")
        return False

    # Get today's RSVP count from the daily_counts dictionary
    # daily_counts uses "2026-04-30" format, so convert
    today_iso = today.isoformat()  # "2026-04-30"
    todays_rsvps = daily_counts.get(today_iso, 0)

    # Write to Column E (New RSVPs) — column 5
    worksheet.update_cell(target_row, 5, todays_rsvps)

    print(f"✓ Daily Tracker: wrote {todays_rsvps} new RSVPs to row {target_row} (Column E)")
    return True


# ── MAIN: RUN EVERYTHING ────────────────────────────────────────────
# This is the entry point — when you run the script, this is what
# executes. It calls each function in order: get data → calculate → post.
#
# "if __name__ == '__main__'" is a Python convention that means
# "only run this if the script is executed directly"
# (not if it's imported by another script).

if __name__ == "__main__":
    print(f"🤖 Pacing Bot — {EVENT_NAME}")
    print(f"   Running at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # Step 1: Get RSVP data from Luma
    daily_counts, total_rsvps = get_luma_rsvps()

    # Step 2: Write today's count to the Daily Tracker sheet
    write_to_daily_tracker(daily_counts)

    # Step 3: Calculate pacing
    pacing = calculate_pacing(total_rsvps)

    # Step 4: Build and send Slack message
    message = build_slack_message(pacing, daily_counts)
    post_to_slack(message)

    print("=" * 50)
    print("✓ Done")
