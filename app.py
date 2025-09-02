from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from notion_client import Client
import os
import json
import re
from dotenv import load_dotenv
from datetime import datetime
import sendgrid
from sendgrid.helpers.mail import Mail
from werkzeug.exceptions import BadRequest

# Load environment variables from .env file
load_dotenv()

# Initialize APIs
openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
notion = Client(auth=os.getenv('NOTION_API_KEY'))
notion_database_id = os.getenv('NOTION_DATABASE_ID')

# Initialize SendGrid only if API key is available
SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY) if SENDGRID_API_KEY else None

app = Flask(__name__)

# Helper function to extract JSON from markdown code blocks
def extract_json_from_markdown(text):
    pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    matches = re.findall(pattern, text)
    
    if matches:
        return matches[0].strip()
    return text

# Helper function to format action items and questions for Notion
def format_for_notion(data):
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

# Email sending function
def send_email_via_sendgrid(meeting_name, summary, action_items, key_questions, notion_url):
    try:
        if not sg:
            return False, "SendGrid not configured"
            
        # Format email content
        email_content = f"""
        <h2>New Meeting Summary: {meeting_name}</h2>
        <p><strong>Summary:</strong><br>{summary.replace(chr(10), '<br>')}</p>
        <p><strong>Action Items:</strong></p>
        <ul>{"".join([f'<li>{item}</li>' for item in action_items.split(chr(10)) if item.strip()])}</ul>
        <p><strong>Key Questions:</strong></p>
        <ul>{"".join([f'<li>{q}</li>' for q in key_questions.split(chr(10)) if q.strip()])}</ul>
        <p><strong>View in Notion:</strong> <a href="{notion_url}">{notion_url}</a></p>
        """

        # Create and send email
        from_email = os.getenv('SENDER_EMAIL')
        to_email = os.getenv('TEAM_LEAD_EMAIL')
        
        if not from_email or not to_email:
            return False, "Email configuration missing"
        
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=f'Meeting Summary: {meeting_name}',
            html_content=email_content
        )
        
        response = sg.send(message)
        return True, f"Email sent successfully. Status code: {response.status_code}"
    except Exception as e:
        return False, f"Failed to send email: {str(e)}"

# This route serves the frontend HTML page
@app.route('/')
def index():
    return render_template('index.html')

# This is the API endpoint that does the magic
@app.route('/summarize', methods=['POST'])
def summarize():
    try:
        # 1. Get the transcript from the user's request
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        transcript = data.get('transcript', '')
        meeting_name = data.get('meetingName', 'Untitled Meeting')

        if not transcript:
            return jsonify({'error': 'No transcript provided'}), 400

        # 2. Craft the prompt for OpenAI
        prompt = f"""
        Please analyze the following meeting transcript and extract the following information:

        - A concise summary of the main points and decisions made.
        - A list of clear action items, specifying the owner if mentioned.
        - A list of key questions that were raised but not resolved.

        Format the output as a JSON object with exactly these three keys: "summary", "action_items", "key_questions".

        For action_items, please provide an array of objects, each with "action" and "owner" fields.
        For key_questions, please provide an array of strings.

        Please provide only the JSON object without any additional text or markdown formatting.

        Transcript:
        {transcript}
        """

        # 3. Call the OpenAI API
        response = openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )

        # 4. Parse the AI's response
        ai_content = response.choices[0].message.content
        
        # Extract JSON from markdown code blocks if present
        cleaned_content = extract_json_from_markdown(ai_content)
        
        # The AI should return a JSON string. We need to convert it to a Python dict.
        try:
            summary_data = json.loads(cleaned_content)
        except json.JSONDecodeError:
            # Fallback if the AI doesn't return valid JSON
            summary_data = {
                "summary": ai_content,
                "action_items": "Could not parse action items.",
                "key_questions": "Could not parse key questions."
            }

        # 5. Format data for Notion API compatibility
        summary_str = format_for_notion(summary_data.get('summary', ''))
        action_items_str = format_for_notion(summary_data.get('action_items', ''))
        key_questions_str = format_for_notion(summary_data.get('key_questions', ''))

        # 6. Save the structured data to Notion
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

        # Get the URL of the new Notion page
        notion_url = new_page.get("url", "No URL available")

        # 7. Send email to team lead
        email_success, email_message = send_email_via_sendgrid(
            meeting_name, summary_str, action_items_str, key_questions_str, notion_url
        )
        
        # Update Notion page with email status
        if email_success:
            notion.pages.update(
                page_id=new_page["id"],
                properties={"Sent": {"checkbox": True}}
            )

        # 8. Send success back to the frontend
        return jsonify({
            "message": "Summary created successfully!",
            "notion_url": notion_url,
            "email_sent": email_success,
            "email_message": email_message
        })

    except Exception as e:
        print(f"Error in /summarize: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# New endpoint to manually trigger email sending for a Notion page
@app.route('/api/email-notion-summary', methods=['POST'])
@app.route('/functions/email-notion-summary', methods=['POST'])
def email_notion_summary():
    try:
        # Check if request contains JSON data
        if not request.data:
            return jsonify({'error': 'No data provided in request'}), 400
            
        # Try to parse JSON data
        try:
            data = request.get_json()
        except BadRequest:
            return jsonify({'error': 'Invalid JSON data'}), 400
            
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        page_id = data.get('page_id')
        
        if not page_id:
            return jsonify({'error': 'No page_id provided'}), 400
        
        # Get the page from Notion
        page = notion.pages.retrieve(page_id=page_id)
        
        # Extract properties with error handling
        meeting_name = page.properties["Meeting Name"].title[0].text.content if page.properties["Meeting Name"].title else "No Title"
        summary = page.properties["Summary"].rich_text[0].text.content if page.properties["Summary"].rich_text else "No summary"
        action_items = page.properties["Action Items"].rich_text[0].text.content if page.properties["Action Items"].rich_text else "No action items"
        key_questions = page.properties["Key Questions"].rich_text[0].text.content if page.properties["Key Questions"].rich_text else "No key questions"
        notion_url = page.url
        
        # Send email
        email_success, email_message = send_email_via_sendgrid(
            meeting_name, summary, action_items, key_questions, notion_url
        )
        
        # Update Notion page with email status
        if email_success:
            notion.pages.update(
                page_id=page_id,
                properties={"Sent": {"checkbox": True}}
            )
        
        return jsonify({
            "success": email_success,
            "message": email_message
        })
        
    except Exception as e:
        print(f"Error in /api/email-notion-summary: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)