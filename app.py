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

# [Keep all your helper functions here - extract_json_from_markdown, format_for_notion, send_email_via_sendgrid]

# This route serves the frontend HTML page
@app.route('/')
def index():
    return render_template('index.html')

# This is the API endpoint that does the magic
@app.route('/summarize', methods=['POST'])
def summarize():
    # [Keep your existing summarize function code here]
    pass

# Updated endpoint to manually trigger email sending for a Notion page
@app.route('/api/email-notion-summary', methods=['POST'])
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