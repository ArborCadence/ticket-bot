import os
import smtplib
import ssl
import requests
import urllib3
from difflib import unified_diff
from email.message import EmailMessage
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# Suppress the SSL warning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_website_text():
    print("Connecting to website...")
    url = os.environ.get("TARGET_URL")
    
    if not url:
        print("TARGET_URL secret is missing!")
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }

    try:
        response = requests.get(url, headers=headers, verify=False, timeout=15)
        response.raise_for_status()
        print("Successfully downloaded website text!")
        
        # Strip away all the HTML tags and extract only the clean, visible text
        soup = BeautifulSoup(response.text, 'html.parser')
        clean_text = soup.get_text(separator='\n', strip=True)
        return clean_text
        
    except Exception as e:
        print(f"Error downloading website: {e}")
        return None

def send_email(diff_text):
    print("Preparing to send email...")
    sender = os.environ.get("SENDER_EMAIL")
    password = os.environ.get("SENDER_PASSWORD")
    receiver = os.environ.get("RECEIVER_EMAIL")

    if not sender or not password or not receiver:
        print("Email credentials missing from secrets. Skipping email.")
        return

    try:
        print("    -> Assembling email...")
        msg = EmailMessage()
        msg.set_content(f"The ticket page has changed!\n\nHere are the exact text differences:\n\n{diff_text}")
        msg['Subject'] = '🚨 FCB Ticket Page Changed!'
        msg['From'] = sender
        msg['To'] = receiver

        print("    -> Connecting to Gmail server via SSL (port 465)...")
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
            print("    -> Logging in...")
            server.login(sender, password)
            print("    -> Sending email...")
            server.send_message(msg)
            
        print("Email sent successfully!")
    except Exception as e:
        print(f"    -> ERROR inside email function: {e}")
    
    print("Email function finished.")

def take_screenshot(url, filename="screenshot.png"):
    print("Taking visual proof screenshot...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url)
            page.screenshot(path=filename)
            browser.close()
            print("Screenshot saved successfully.")
    except Exception as e:
        print(f"Failed to take screenshot: {e}")

def send_ntfy_push():
    channel = os.environ.get("NTFY_CHANNEL")
    target_url = os.environ.get("TARGET_URL")
    
    if not channel or not target_url:
        print("NTFY_CHANNEL or TARGET_URL secret is missing. Skipping push.")
        return

    take_screenshot(target_url)

    print("Sending push notification with image...")
    
    try:
        with open("screenshot.png", "rb") as image_file:
            # Notice there are no \n\n breaks in this Message header anymore!
            headers = {
                "Title": "FCB Tickets Changed!",
                "Message": f"Website updated. See attached screenshot. Link: {target_url}",
                "Click": target_url,
                "Filename": "screenshot.png"
            }
            
            response = requests.post(
                f"https://ntfy.sh/{channel}",
                data=image_file,
                headers=headers
            )
            print(f"Push sent successfully! Status: {response.status_code}")
    except FileNotFoundError:
         print("Screenshot file not found, cannot attach to ntfy.")
    except Exception as e:
        print(f"Failed to send push notification: {e}")

def main():
    print("--- Starting New Check ---")
    current_text = get_website_text()
    
    if current_text is None:
        print("Check failed due to download error. Sleeping...")
        return

    file_path = "ticket_state.txt"
    
    if not os.path.exists(file_path):
        print("No previous state file found. Creating one now...")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(current_text)
        print("State file created. Check complete.")
        return

    print("Loaded previous text from state file.")
    with open(file_path, "r", encoding="utf-8") as f:
        previous_text = f.read()

    # BULLETPROOF CLEANING: Break into lines, strip invisible spaces from every line, and delete all empty lines
    prev_lines = [line.strip() for line in previous_text.splitlines() if line.strip()]
    curr_lines = [line.strip() for line in current_text.splitlines() if line.strip()]

    if curr_lines != prev_lines:
        print("Differences detected between website and text file!")
        
        diff = unified_diff(prev_lines, curr_lines, lineterm='')
        diff_text = '\n'.join(list(diff))
        
        # FAILSAFE: If the difference text is completely empty, do not send the alerts!
        if not diff_text.strip():
            print("Difference was only invisible formatting. Skipping phantom alerts.")
        else:
            send_email(diff_text)
            send_ntfy_push()
            
        # Always overwrite the file with the new state so we don't get stuck in a loop
        print("Overwriting the state file with new text...")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(current_text)
        print("File saved successfully.")
    else:
        print("No changes detected.")

    print("Check complete.")

if __name__ == "__main__":
    main()
