import os
import json
import re
import logging
import traceback
import concurrent.futures
import time
from datetime import datetime

from flask import Flask, request, jsonify, render_template
from flask_mail import Mail as FlaskMail, Message
from openai import OpenAI
from notion_client import Client
from dotenv import load_dotenv

# -----------------------------------------------------
# Load environment variables
# -----------------------------------------------------
load_dotenv()

# Flask setup
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# -----------------------------------------------------
# Initialize APIs
# -----------------------------------------------------
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
notion = Client(auth=os.getenv("NOTION_API_KEY"))
notion_database_id = os.getenv("NOTION_DATABASE_ID")

# -----------------------------------------------------
# Mailtrap (Flask-Mail) setup
# -----------------------------------------------------
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT"))
app.config["MAIL_USERNAME"] = os.getenv("MAILTRAP_SMTP_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAILTRAP_SMTP_PASSWORD")
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USE_SSL"] = False

mail = FlaskMail(app)
MAILTRAP_VERIFIED_SENDER = os.getenv("MAILTRAP_VERIFIED_SENDER")

# -----------------------------------------------------
# Timeout helper
# -----------------------------------------------------
def timeout_wrapper(func, *args, timeout=20, **kwargs):
    """Run function with timeout in seconds."""
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"{func.__name__} exceeded {timeout}s timeout")
        
notion_database_url = os.getenv(
    "NOTION_DATABASE_URL",
    "https://www.notion.so/2600089e9046800782ffc62e47b9da86?v=2600089e9046801c8aee000c68f9d671"
)

# -----------------------------------------------------
# Helpers
# -----------------------------------------------------
def extract_json_from_markdown(text):
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    matches = re.findall(pattern, text)
    return matches[0].strip() if matches else text

def format_for_notion(data):
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and "action" in data[0]:
            return "\n".join(
                f"• {item.get('action', '')} (Owner: {item.get('owner', '')})" if item.get("owner")
                else f"• {item.get('action', '')}"
                for item in data
            ).strip()
        elif data and isinstance(data[0], str):
            return "\n".join([f"• {q}" for q in data])
        return "\n".join([f"• {str(i)}" for i in data])
    elif isinstance(data, dict):
        return json.dumps(data, indent=2)
    return str(data)

def safe_get_text(prop, key_type="rich_text", page_id="unknown", field_name="unknown"):
    """
    Safely extract text from Notion properties - updated for rich_text
    """
    try:
        if not prop:
            app.logger.warning(f"[Notion] Missing entire property '{field_name}' on page {page_id}")
            return ""
        
        # Handle both rich_text and text types
        if key_type in prop:
            if prop[key_type]:
                # Extract text content from the first element
                if key_type == "rich_text":
                    return prop[key_type][0].get("text", {}).get("content", "")
                elif key_type == "title":
                    return prop[key_type][0].get("text", {}).get("content", "")
                elif key_type == "checkbox":
                    return str(prop[key_type])
            else:
                app.logger.warning(f"[Notion] Empty '{field_name}' (type={key_type}) on page {page_id}")
        else:
            app.logger.warning(f"[Notion] Missing key type '{key_type}' for field '{field_name}'. Actual keys: {list(prop.keys())}")
    except Exception as e:
        app.logger.error(f"[Notion] Error extracting '{field_name}' on page {page_id}: {e}")
    return ""

def send_email_via_mailtrap(meeting_name, summary, action_items, key_questions, page_url):
    try:
        # Include both links
        html_content = f"""
        <h2>Meeting Summary: {meeting_name}</h2>
        <p><strong>Summary:</strong><br>{summary.replace(chr(10), '<br>')}</p>
        <p><strong>Action Items:</strong></p>
        <ul>{"".join([f'<li>{i}</li>' for i in action_items.split(chr(10)) if i.strip()])}</ul>
        <p><strong>Key Questions:</strong></p>
        <ul>{"".join([f'<li>{q}</li>' for q in key_questions.split(chr(10)) if q.strip()])}</ul>
        <p><strong>View Page in Notion:</strong> <a href="{page_url}">{page_url}</a></p>
        <p><strong>View Database in Notion:</strong> <a href="{notion_database_url}">{notion_database_url}</a></p>
        """

        plain_text_content = f"""
        Meeting Summary: {meeting_name}

        Summary:
        {summary}

        Action Items:
        {action_items}

        Key Questions:
        {key_questions}

        View Page in Notion: {page_url}
        View Database in Notion: {notion_database_url}
        """

        msg = Message(
            subject=f"Meeting Summary: {meeting_name}",
            sender=(MAILTRAP_VERIFIED_SENDER, "AI Meeting Summarizer"),
            recipients=[MAILTRAP_VERIFIED_SENDER],  # Mailtrap trial restriction
        )
        msg.body = plain_text_content
        msg.html = html_content

        # Increase timeout and add retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                timeout_wrapper(mail.send, msg, timeout=45)  # Increased timeout
                return True, "Email sent successfully via Mailtrap!"
            except TimeoutError:
                if attempt < max_retries - 1:
                    app.logger.warning(f"Mailtrap timeout, retry {attempt + 1}/{max_retries}")
                    time.sleep(5)  # Wait before retry
                else:
                    raise
    except Exception as e:
        app.logger.error(f"Mailtrap send failed after {max_retries} attempts: {e}")
        return False, f"Failed to send email: {str(e)}"

# -----------------------------------------------------
# Routes
# -----------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/summarize", methods=["POST"])
def summarize():
    try:
        data = request.get_json(silent=True) or {}
        transcript = data.get("transcript")
        meeting_name = data.get("meetingName", "Untitled Meeting")

        if not transcript:
            return jsonify({"error": "No transcript provided"}), 400

        prompt = f"""
        Please analyze the following meeting transcript and extract:
        - A concise summary.
        - Action items with owners.
        - Key questions unresolved.

        Format as JSON with keys: summary, action_items, key_questions.

        Transcript:
        {transcript}
        """

        response = timeout_wrapper(
            openai_client.chat.completions.create,
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            timeout=30,
        )

        ai_content = response.choices[0].message.content
        cleaned_content = extract_json_from_markdown(ai_content)

        try:
            summary_data = json.loads(cleaned_content)
        except json.JSONDecodeError:
            summary_data = {
                "summary": ai_content,
                "action_items": "Could not parse action items.",
                "key_questions": "Could not parse key questions.",
            }

        summary_str = format_for_notion(summary_data.get("summary", ""))
        action_items_str = format_for_notion(summary_data.get("action_items", ""))
        key_questions_str = format_for_notion(summary_data.get("key_questions", ""))

        # Validate that we have meaningful content
        if not summary_str.strip() or summary_str.strip() == "Could not parse action items.":
            return jsonify({"error": "AI failed to generate meaningful summary"}), 500

        new_page = timeout_wrapper(
            notion.pages.create,
            parent={"database_id": notion_database_id},
            properties={
                "Meeting Name": {"title": [{"text": {"content": meeting_name}}]},
                "Summary": {"rich_text": [{"text": {"content": summary_str}}]},
                "Action Items": {"rich_text": [{"text": {"content": action_items_str}}]},
                "Key Questions": {"rich_text": [{"text": {"content": key_questions_str}}]},
                "Date": {"date": {"start": datetime.now().isoformat()[:10]}},
                "Sent": {"checkbox": False},
            },
            timeout=20,
        )

        notion_url = new_page.get("url", "No URL available")

        email_success, email_message = send_email_via_mailtrap(
            meeting_name, summary_str, action_items_str, key_questions_str, notion_url
        )

        if email_success:
            timeout_wrapper(
                notion.pages.update,
                page_id=new_page["id"],
                properties={"Sent": {"checkbox": True}},
                timeout=15,
            )

        return jsonify({
            "message": "Summary created successfully!",
            "notion_url": notion_url,
            "email_sent": email_success,
            "email_message": email_message,
        }), 200
    except TimeoutError as te:
        logging.error("Timeout: %s", str(te))
        return jsonify({"error": "Operation timed out", "details": str(te)}), 504
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/email-notion-summary", methods=["POST"])
def email_notion_summary():
    try:
        app.logger.info("Starting email-notion-summary processing")
        # Process only a limited number of pages to prevent timeouts
        query = {
            "filter": {"property": "Sent", "checkbox": {"equals": False}},
            "page_size": 5  # Limit to 5 pages per request
        }
        results = timeout_wrapper(
            notion.databases.query,
            database_id=notion_database_id,
            **query,
            timeout=20,
        )

        new_pages = results.get("results", [])
        app.logger.info(f"Found {len(new_pages)} unsent pages")
        processed = 0
        processed_ids = set()  # Track processed page IDs to avoid duplicates

        for page in new_pages:
            page_id = page.get("id")
            props = page.get("properties", {})
            
            # Check if meeting name is empty
            meeting_name = safe_get_text(props.get("Meeting Name", {}), "title", page_id, "Meeting Name")
            if not meeting_name or meeting_name == "No Title":
                app.logger.info(f"Skipping page {page_id} with empty meeting name")
                continue
            
            # Check if summary is empty
            summary = safe_get_text(props.get("Summary", {}), "rich_text", page_id, "Summary")
            if not summary or summary == "No summary provided.":
                app.logger.info(f"Skipping page {page_id} with empty summary")
                continue
            
            # Skip if we've already processed this page
            if page_id in processed_ids:
                app.logger.warning(f"Skipping duplicate page: {page_id}")
                continue
                
            processed_ids.add(page_id)
            
            # Extract properties with correct types
            meeting_name = safe_get_text(props.get("Meeting Name", {}), "title", page_id, "Meeting Name") or "No Title"
            summary = safe_get_text(props.get("Summary", {}), "rich_text", page_id, "Summary") or "No summary"
            action_items = safe_get_text(props.get("Action Items", {}), "rich_text", page_id, "Action Items") or "No action items"
            key_questions = safe_get_text(props.get("Key Questions", {}), "rich_text", page_id, "Key Questions") or "No key questions"
            notion_url = page.get("url", "No URL available")

            app.logger.info(f"Processing page: {page_id} ({notion_url})")

            email_success, _ = send_email_via_mailtrap(
                meeting_name, summary, action_items, key_questions, notion_url
            )

            if email_success:
                timeout_wrapper(
                    notion.pages.update,
                    page_id=page_id,
                    properties={"Sent": {"checkbox": True}},
                    timeout=15,
                )
                processed += 1

        return jsonify({"message": f"Processed {processed} unsent meeting summaries.", "has_more": results.get("has_more", False)}), 200
    except TimeoutError as te:
        logging.error("Timeout: %s", str(te))
        return jsonify({"error": "Operation timed out", "details": str(te)}), 504
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/functions/email-notion-summary", methods=["POST"])
def functions_email_notion_summary():
    return email_notion_summary()

# -----------------------------------------------------
# Entrypoint
# -----------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)