from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from notion_client import Client
import os
import json
import re
from dotenv import load_dotenv
from datetime import datetime
from flask_mail import Mail, Message
from werkzeug.exceptions import BadRequest
import logging

# -----------------------------------------------------
# Load environment variables
# -----------------------------------------------------
load_dotenv()

# Initialize APIs
openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
notion = Client(auth=os.getenv('NOTION_API_KEY'))
notion_database_id = os.getenv('NOTION_DATABASE_ID')

# Flask setup
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# -----------------------------------------------------
# Flask-Mail Configuration for Mailtrap
# -----------------------------------------------------
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT'))
app.config['MAIL_USERNAME'] = os.getenv('MAILTRAP_SMTP_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAILTRAP_SMTP_PASSWORD')
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False

# Initialize Flask-Mail
mail = Mail(app)

# Sender email (must be verified in your Mailtrap account)
MAILTRAP_VERIFIED_SENDER = os.getenv('MAILTRAP_VERIFIED_SENDER', 'Maxdwell@hotmail.com')

# -----------------------------------------------------
# Helper functions
# -----------------------------------------------------
def extract_json_from_markdown(text):
    """Extract JSON from markdown code blocks if present."""
    pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    matches = re.findall(pattern, text)
    if matches:
        return matches[0].strip()
    return text

def format_for_notion(data):
    """Format summaries, action items, or questions for Notion."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and 'action' in data[0]:
            formatted_text = ""
            for item in data:
                action = item.get('action', '')
                owner = item.get('owner', '')
                if owner:
                    formatted_text += f"• {action} (Owner: {owner})\n"
                else:
                    formatted_text += f"• {action}\n"
            return formatted_text.strip()
        elif data and isinstance(data[0], str):
            return "\n".join([f"• {question}" for question in data])
        return "\n".join([f"• {str(item)}" for item in data])
    elif isinstance(data, dict):
        return json.dumps(data, indent=2)
    return str(data)

# -----------------------------------------------------
# Mailtrap email function
# -----------------------------------------------------
def send_email_via_mailtrap(meeting_name, summary, action_items, key_questions, notion_url):
    try:
        # Format email content
        html_content = f"""
        <h2>Meeting Summary: {meeting_name}</h2>
        <p><strong>Summary:</strong><br>{summary.replace(chr(10), '<br>')}</p>
        <p><strong>Action Items:</strong></p>
        <ul>{"".join([f'<li>{item}</li>' for item in action_items.split(chr(10)) if item.strip()])}</ul>
        <p><strong>Key Questions:</strong></p>
        <ul>{"".join([f'<li>{q}</li>' for q in key_questions.split(chr(10)) if q.strip()])}</ul>
        <p><strong>View in Notion:</strong> <a href="{notion_url}">{notion_url}</a></p>
        """

        plain_text_content = f"""
        Meeting Summary: {meeting_name}

        Summary:
        {summary}

        Action Items:
        {action_items}

        Key Questions:
        {key_questions}

        View in Notion: {notion_url}
        """

        # Create message (fixed sender tuple)
        msg = Message(
            subject=f"Meeting Summary: {meeting_name}",
            sender=("AI Meeting Summarizer", MAILTRAP_VERIFIED_SENDER),
            recipients=[MAILTRAP_VERIFIED_SENDER]  # Trial: only to verified sender
        )
        msg.body = plain_text_content
        msg.html = html_content

        mail.send(msg)
        return True, "Email sent successfully via Mailtrap!"

    except Exception as e:
        return False, f"Failed to send email: {str(e)}"

# -----------------------------------------------------
# Routes
# -----------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/summarize', methods=['POST'])
def summarize():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        transcript = data.get('transcript', '')
        meeting_name = data.get('meetingName', 'Untitled Meeting')

        if not transcript:
            return jsonify({'error': 'No transcript provided'}), 400

        # OpenAI request
        prompt = f"""
        Please analyze the following meeting transcript and extract:
        - A concise summary.
        - Action items with owners.
        - Key questions unresolved.

        Format as JSON with keys: summary, action_items, key_questions.

        Transcript:
        {transcript}
        """
        response = openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )

        ai_content = response.choices[0].message.content
        cleaned_content = extract_json_from_markdown(ai_content)

        try:
            summary_data = json.loads(cleaned_content)
        except json.JSONDecodeError:
            summary_data = {
                "summary": ai_content,
                "action_items": "Could not parse action items.",
                "key_questions": "Could not parse key questions."
            }

        summary_str = format_for_notion(summary_data.get('summary', ''))
        action_items_str = format_for_notion(summary_data.get('action_items', ''))
        key_questions_str = format_for_notion(summary_data.get('key_questions', ''))

        # Save to Notion
        new_page = notion.pages.create(
            parent={"database_id": notion_database_id},
            properties={
                "Meeting Name": {"title": [{"text": {"content": meeting_name}}]},
                "Summary": {"rich_text": [{"text": {"content": summary_str}}]},
                "Action Items": {"rich_text": [{"text": {"content": action_items_str}}]},
                "Key Questions": {"rich_text": [{"text": {"content": key_questions_str}}]},
                "Date": {"date": {"start": datetime.now().isoformat()[:10]}},
                "Sent": {"checkbox": False}
            }
        )

        notion_url = new_page.get("url", "No URL available")

        # Send email via Mailtrap
        email_success, email_message = send_email_via_mailtrap(
            meeting_name, summary_str, action_items_str, key_questions_str, notion_url
        )

        if email_success:
            notion.pages.update(
                page_id=new_page["id"],
                properties={"Sent": {"checkbox": True}}
            )

        return jsonify({
            "message": "Summary created successfully!",
            "notion_url": notion_url,
            "email_sent": email_success,
            "email_message": email_message
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/email-notion-summary', methods=['POST'])
def email_notion_summary():
    try:
        data = request.get_json(force=True)
        page_id = data.get('page_id')
        if not page_id:
            return jsonify({'error': 'No page_id provided'}), 400

        page = notion.pages.retrieve(page_id=page_id)
        props = page.get('properties', {})

        meeting_name = props.get("Meeting Name", {}).get('title', [{}])[0].get('text', {}).get('content', "No Title")
        summary = props.get("Summary", {}).get('rich_text', [{}])[0].get('text', {}).get('content', "No summary")
        action_items = props.get("Action Items", {}).get('rich_text', [{}])[0].get('text', {}).get('content', "No action items")
        key_questions = props.get("Key Questions", {}).get('rich_text', [{}])[0].get('text', {}).get('content', "No key questions")
        notion_url = page.get('url', 'No URL available')

        email_success, email_message = send_email_via_mailtrap(
            meeting_name, summary, action_items, key_questions, notion_url
        )

        if email_success:
            notion.pages.update(
                page_id=page_id,
                properties={"Sent": {"checkbox": True}}
            )

        return jsonify({"success": email_success, "message": email_message})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/functions/email-notion-summary', methods=['POST'])
def functions_email_notion_summary():
    return email_notion_summary()

# -----------------------------------------------------
# Test Email Route (new)
# -----------------------------------------------------
@app.route('/test-email')
def test_email():
    success, message = send_email_via_mailtrap(
        "Test Meeting",
        "This is a test summary.",
        "Action 1\nAction 2",
        "Question 1\nQuestion 2",
        "https://notion.so/test-page"
    )
    return jsonify({"success": success, "message": message})

# -----------------------------------------------------
# Entrypoint
# -----------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
