import os
import requests
from bs4 import BeautifulSoup
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import difflib
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration pulled safely from GitHub Secrets
URL = "https://www.fcb-fanclub-mietraching.de/ticket/"
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL")
STATE_FILE = "ticket_state.txt"

def get_website_text():
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(URL, headers=headers, timeout=15, verify=False)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        text = soup.get_text(separator='\n')
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return '\n'.join(lines)
    except Exception as e:
        print(f"Error fetching website: {e}")
        return None

def send_email(diff_text):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = "Ticket Update: FCB Fanclub Mietraching"

    body = f"The following changes were detected on the ticket page:\n\n{diff_text}\n\nLink: {URL}"
    msg.attach(MIMEText(body, 'plain'))

    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15, context=context)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Email sent successfully from cloud!")
    except Exception as e:
        print(f"Error sending email: {e}")

def main():
    current_text = get_website_text()
    if not current_text:
        return

    old_text = ""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            old_text = f.read()

    # If previous state exists and website changed, send email
    if old_text and current_text != old_text:
        diff = difflib.unified_diff(
            old_text.splitlines(),
            current_text.splitlines(),
            fromfile='Old Website',
            tofile='New Website',
            lineterm=''
        )
        diff_text = '\n'.join(list(diff))
        if diff_text:
            send_email(diff_text)

    # Save the updated website text
    if current_text != old_text:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            f.write(current_text)
        print("State file updated.")

if __name__ == "__main__":
    main()
