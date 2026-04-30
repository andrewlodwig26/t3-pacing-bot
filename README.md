# T3 Event Pacing Bot

Automated event registration pacing tracker. Pulls RSVP data from Luma, calculates daily pace against goal, and posts a formatted summary to Slack every weekday morning — zero human intervention.

Built to replace a 15-minute manual daily check across Luma, Google Sheets, and Slack.

## What it does

1. **Pulls guest data** from the Luma API (handles pagination for large events)
2. **Counts approved RSVPs** by day and calculates cumulative registration pace
3. **Projects final RSVP count** based on current daily pace vs. days remaining
4. **Posts a Slack summary** with pacing status (on track / behind / ahead), projected outcome, and a plain-language recommendation

## Example output

```
📊 T3 Live NYC — Daily Pacing Update | Thursday, April 30

RSVPs: 45 / 150 (30.0% to goal)
Pacing: BEHIND ⚠️ | Pace: 2.1/day (need 8.8/day)
Days to Event: 12 (May 12)
Projected Final: 71 RSVPs

📈 Read: 🔴 Projected 71 at current pace (79 short). Need to add contacts or re-engage non-openers.

📝 Bot-generated. Reply in thread to discuss.
```

## Setup

### Prerequisites
- Python 3.9+
- Luma API key ([lu.ma/settings/api](https://lu.ma/settings/api))
- Slack incoming webhook URL
- (Optional) HubSpot private app token for email sequence stats

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `LUMA_API_KEY` | Yes | Your Luma API key |
| `LUMA_EVENT_ID` | Yes | Event ID (e.g., `evt-TnkxSuMG1fjaEez`) |
| `SLACK_WEBHOOK_URL` | No | Slack incoming webhook — prints to stdout if not set |
| `HUBSPOT_API_KEY` | No | HubSpot private app token (v2 feature) |

### Run locally

```bash
export LUMA_API_KEY="your-key-here"
export LUMA_EVENT_ID="evt-your-event-id"
python3 pacing_bot.py
```

### Run on schedule (GitHub Actions)

The included workflow runs every weekday at 7:00 AM PT. Add your API keys as repository secrets in Settings → Secrets and variables → Actions.

## Tech stack

- Python 3 (requests, datetime, collections)
- Luma API (event guest data)
- Slack Incoming Webhooks (message delivery)
- GitHub Actions (scheduled automation)
- HubSpot API (email sequence stats — v2)

## Roadmap

- [x] Luma RSVP pull + pacing calculation
- [x] Slack message formatting
- [ ] HubSpot email sequence stats integration
- [ ] Multi-event support (run for any event via config)
- [ ] Historical pace comparison (this event vs. past events)
- [ ] Cloudflare Pages live dashboard

## Author

Andrew Lodwig — Field Marketing Manager → aspiring GTM engineer. This is project #1 in a portfolio of AI-native operations tools.
