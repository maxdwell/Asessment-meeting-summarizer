import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Flask app
app = Flask(__name__)

# Notion setup
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# Mailtrap SMTP setup
MAILTRAP_HOST = os.getenv("MAILTRAP_SMTP_SERVER", "live.smtp.mailtrap.io")
MAILTRAP_PORT = int(os.getenv("MAILTRAP_SMTP_PORT", "587"))
MAILTRAP_USER = os.getenv("MAILTRAP_SMTP_USERNAME")
MAILTRAP_PASS = os.getenv("MAILTRAP_SMTP_PASSWORD")
MAILTRAP_SENDER = os.getenv("MAILTRAP_VERIFIED_SENDER")


def send_email(subject, html_content, text_content, to_email):
    """Send email through Mailtrap SMTP"""
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"Meeting Bot <{MAILTRAP_SENDER}>"
        msg["To"] = to_email
        msg["Subject"] = subject

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(MAILTRAP_HOST, MAILTRAP_PORT) as server:
            server.starttls()
            server.login(MAILTRAP_USER, MAILTRAP_PASS)
            server.sendmail(MAILTRAP_SENDER, to_email, msg.as_string())

        return True
    except Exception as e:
        app.logger.error(f"Email send failed: {e}")
        return False


def build_email_content(page):
    """Build HTML + text content from Notion page"""
    meeting_name = (
        page.get("properties", {})
        .get("Meeting Name", {})
        .get("title", [{}])[0]
        .get("text", {})
        .get("content", "No Title")
    )
    summary = (
        page.get("properties", {})
        .get("Summary", {})
        .get("rich_text", [{}])[0]
        .get("text", {})
        .get("content", "No summary provided.")
    )
    action_items = (
        page.get("properties", {})
        .get("Action Items", {})
        .get("rich_text", [{}])[0]
        .get("text", {})
        .get("content", "No action items.")
    )
    key_questions = (
        page.get("properties", {})
        .get("Key Questions", {})
        .get("rich_text", [{}])[0]
        .get("text", {})
        .get("content", "No key questions.")
    )

    html = f"""
    <h2>New Meeting Summary: {meeting_name}</h2>
    <p><strong>Summary:</strong><br>{summary.replace("\n", "<br>")}</p>
    <p><strong>Action Items:</strong></p>
    <ul>{''.join(f'<li>{i}</li>' for i in action_items.splitlines() if i.strip())}</ul>
    <p><strong>Key Questions:</strong></p>
    <ul>{''.join(f'<li>{q}</li>' for q in key_questions.splitlines() if q.strip())}</ul>
    <p><strong>View in Notion:</strong> <a href="{page.get("url")}">{page.get("url")}</a></p>
    """

    text = f"""Meeting Summary: {meeting_name}

Summary:
{summary}

Action Items:
{action_items}

Key Questions:
{key_questions}

View in Notion: {page.get("url")}
"""
    return meeting_name, html, text


@app.route("/api/email-notion-summary", methods=["POST"])
def email_notion_summary():
    try:
        data = request.get_json(silent=True) or {}
        app.logger.info(f"Incoming payload: {data}")

        # Query Notion for unsent meeting summaries
        query = {
            "filter": {"property": "Sent", "checkbox": {"equals": False}}
        }
        res = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=NOTION_HEADERS,
            json=query,
        )
        res.raise_for_status()
        new_pages = res.json().get("results", [])

        processed = 0
        for page in new_pages:
            meeting_name, html_content, text_content = build_email_content(page)

            sent = send_email(
                subject=f"Meeting Summary: {meeting_name}",
                html_content=html_content,
                text_content=text_content,
                to_email=MAILTRAP_SENDER,  # Mailtrap trial restriction
            )

            if sent:
                # Mark as sent in Notion
                requests.patch(
                    f"https://api.notion.com/v1/pages/{page['id']}",
                    headers=NOTION_HEADERS,
                    json={"properties": {"Sent": {"checkbox": True}}},
                )
                processed += 1

        return jsonify(
            {"message": f"Successfully processed {processed} meeting summaries."}
        ), 200

    except Exception as e:
        app.logger.error(f"Error in email_notion_summary: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
