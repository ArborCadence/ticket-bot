import json
import os
import smtplib
import ssl
import sys
import requests
import urllib3
from email.message import EmailMessage
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# Suppress the SSL warning for the ticket page download (public, read-only page)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

STATE_FILE = "ticket_state.json"

# How many failed checks in a row before you get a "bot is broken" alert.
# At one check every 5 minutes, 3 failures = roughly 15 minutes.
FAILURE_ALERT_THRESHOLD = 3


def clean(text):
    """Collapse all whitespace (double spaces, newlines) into single spaces."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Reading the ticket page
# ---------------------------------------------------------------------------

def fetch_page(url):
    print("Connecting to website...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    response = requests.get(url, headers=headers, verify=False, timeout=15)
    response.raise_for_status()
    print("Successfully downloaded the ticket page!")
    return response.text


def parse_matches(html):
    """Turn the ticket portal into structured data:

    {match_id: {"date": ..., "title": ..., "categories": {name: {"available": int, "price": str}}}}
    """
    soup = BeautifulSoup(html, "html.parser")
    matches = {}

    # Every match is one Bootstrap "card": a header with date/opponent and a table of categories
    for card in soup.select("div.card"):
        header = card.select_one(".card-header")
        table = card.select_one("table")
        if not header or not table:
            continue

        bold = [clean(b.get_text()) for b in header.find_all("b")]
        date = bold[0] if len(bold) > 0 else ""
        title = bold[1] if len(bold) > 1 else ""

        # The booking form carries a stable match id; fall back to date + title
        id_input = card.find("input", attrs={"name": "match"})
        match_id = id_input["value"] if id_input and id_input.get("value") else f"{date}|{title}"

        categories = {}
        for row in table.select("tbody tr"):
            name_cell = row.find("th")
            cells = row.find_all("td")
            if not name_cell or len(cells) < 2:
                continue
            available_text = clean(cells[0].get_text())
            if not available_text.isdigit():
                raise ValueError(f"Unexpected availability value {available_text!r} for {title}")
            categories[clean(name_cell.get_text())] = {
                "available": int(available_text),
                "price": clean(cells[1].get_text()),
            }

        matches[match_id] = {"date": date, "title": title, "categories": categories}

    # Page loaded and clearly has a ticket table, but we couldn't read any match:
    # the layout probably changed, so treat it as a failure instead of "no tickets".
    if not matches and "verfügbar" in html:
        raise ValueError("Ticket page loaded, but no matches could be read. The page layout may have changed.")

    return matches


def find_new_tickets(old_matches, new_matches):
    """Return one line per category whose available count went UP (new match, new release, restock).

    Sell-outs, price changes and changed notes are ignored on purpose.
    """
    changes = []
    for match_id, match in new_matches.items():
        is_new_match = match_id not in old_matches
        old_categories = old_matches.get(match_id, {}).get("categories", {})
        for name, category in match["categories"].items():
            before = old_categories.get(name, {}).get("available", 0)
            now = category["available"]
            if now > before:
                prefix = "NEW MATCH: " if is_new_match else ""
                changes.append(
                    f"{prefix}{match['title']} ({match['date']}) - Kat. {name}: "
                    f"{now} available (was {before}), {category['price']}"
                )
    return changes


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def load_state():
    if not os.path.exists(STATE_FILE):
        # "matches": None means there is no baseline yet, so the first run never alerts
        return {"failures": 0, "matches": None}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    # sort_keys keeps the file stable, so it only gets committed when something really changed
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def send_email(subject, body):
    print("Preparing to send email...")
    sender = os.environ.get("SENDER_EMAIL")
    password = os.environ.get("SENDER_PASSWORD")
    receiver = os.environ.get("RECEIVER_EMAIL")

    if not sender or not password or not receiver:
        print("Email credentials missing from secrets. Skipping email.")
        return

    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = receiver

        # Default context = certificate and hostname are verified, so the
        # Gmail password only ever goes to the real smtp.gmail.com
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
            server.login(sender, password)
            server.send_message(msg)

        print("Email sent successfully!")
    except Exception as e:
        print(f"    -> ERROR sending email: {e}")


def take_screenshot(url, filename="screenshot.png"):
    print("Taking visual proof screenshot...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url)
            page.screenshot(path=filename, full_page=True)
            browser.close()
        print("Screenshot saved successfully.")
        return True
    except Exception as e:
        print(f"Failed to take screenshot: {e}")
        return False


def send_push(title, message, priority="default", tags="", click=None, screenshot_url=None):
    channel = os.environ.get("NTFY_CHANNEL")
    if not channel:
        print("NTFY_CHANNEL secret is missing. Skipping push.")
        return

    # Title and message go as URL parameters so umlauts and the € sign survive
    # (HTTP headers can't carry them reliably)
    params = {"title": title, "message": message}
    headers = {"Priority": priority}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click

    data = None
    if screenshot_url and take_screenshot(screenshot_url):
        with open("screenshot.png", "rb") as image_file:
            data = image_file.read()
        headers["Filename"] = "screenshot.png"

    print("Sending push notification...")
    try:
        response = requests.post(
            f"https://ntfy.sh/{channel}",
            params=params,
            data=data,
            headers=headers,
            timeout=30,
        )
        print(f"Push sent! Status: {response.status_code}")
    except Exception as e:
        print(f"Failed to send push notification: {e}")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def handle_failure(state, error, url):
    failures = state.get("failures", 0)

    if failures >= FAILURE_ALERT_THRESHOLD:
        # Already alerted for this outage. Don't touch the state file (no commit spam).
        print(f"Check failed again, alert was already sent: {error}")
        return 0

    failures += 1
    state["failures"] = failures
    save_state(state)
    print(f"Check failed ({failures} in a row): {error}")

    if failures == FAILURE_ALERT_THRESHOLD:
        message = (
            f"The ticket bot failed {failures} checks in a row and is NOT watching for tickets.\n\n"
            f"Last error: {error}\n\nURL: {url}"
        )
        send_email("⚠️ FCB ticket bot is broken", message)
        send_push("Ticket bot is broken", message, priority="high", tags="warning", click=url)
        # Mark this one run as failed, so it shows red in the GitHub Actions list
        return 1

    return 0


def main():
    print("--- Starting New Check ---")
    url = os.environ.get("TARGET_URL")
    if not url:
        print("TARGET_URL secret is missing!")
        return 1

    state = load_state()

    try:
        html = fetch_page(url)
        matches = parse_matches(html)
    except Exception as e:
        return handle_failure(state, e, url)

    print(f"Read {len(matches)} match(es) from the page.")

    if state.get("failures", 0) >= FAILURE_ALERT_THRESHOLD:
        print("Bot recovered after an outage.")
        send_push("Ticket bot is working again", "Checks are succeeding again.", tags="white_check_mark", click=url)
    state["failures"] = 0

    if state.get("matches") is None:
        print("No previous state found. Saving the current tickets as the baseline (no alerts).")
    else:
        changes = find_new_tickets(state["matches"], matches)
        if changes:
            print("New tickets found:")
            for line in changes:
                print("  " + line)
            summary = "\n".join(changes)
            send_email("🎟️ FCB tickets available!", f"{summary}\n\nBook here: {url}")
            send_push("FCB tickets available!", summary, priority="high", tags="tickets",
                      click=url, screenshot_url=url)
        else:
            print("No new tickets.")

    state["matches"] = matches
    save_state(state)
    print("Check complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
