from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from notion_client import Client
import os
import json
import re
from dotenv import load_dotenv
from datetime import datetime
from flask_mail import Mail, Message
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

mail = Mail(app)
MAILTRAP_VERIFIED_SENDER = os.getenv('MAILTRAP_VERIFIED_SENDER', 'Maxdwell@hotmail.com')

# -----------------------------------------------------
# Helpers
# -----------------------------------------------------
def extract_json_from_markdown(text):
    pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    matches = re.findall(pattern, text)
    return matches[0].strip() if matches else text

def format_for_notion(data):
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and 'action' in data[0]:
            return "\n".join(
                f"• {item.get('action', '')} (Owner: {item.get('owner', '')})" if item.get('owner')
                else f"• {item.get('action', '')}"
                for item in data
            ).strip()
        elif data and isinstance(data[0], str):
            return "\n".join([f"• {q}" for q in data])
        return "\n".join([f"• {str(i)}" for i in data])
    elif isinstance(data, dict):
        return json.dumps(data, indent=2)
    return str(data)

def send_email_via_mailtrap(meeting_name, summary, action_items, key_questions, notion_url):
    try:
        html_content = f"""
        <h2>Meeting Summary: {meeting_name}</h2>
        <p><strong>Summary:</strong><br>{summary.replace(chr(10), '<br>')}</p>
        <p><strong>Action Items:</strong></p>
        <ul>{"".join([f'<li>{i}</li>' for i in action_items.split(chr(10)) if i.strip()])}</ul>
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

        msg = Message(
            subject=f"Meeting Summary: {meeting_name}",
            sender=(MAILTRAP_VERIFIED_SENDER, "AI Meeting Summarizer"),
            recipients=[MAILTRAP_VERIFIED_SENDER]
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
        if not request.is_json:
            return jsonify({'error': 'Request must be JSON'}), 400

        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Empty JSON body'}), 400

        transcript = data.get('transcript')
        meeting_name = data.get('meetingName', 'Untitled Meeting')

        if not transcript:
            return jsonify({'error': 'No transcript provided'}), 400

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
        if not request.is_json:
            return jsonify({'error': 'Request must be JSON'}), 400

        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Empty JSON body'}), 400

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
# Entrypoint
# -----------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
