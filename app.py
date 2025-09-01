from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from notion_client import Client
import os
import json
import re
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

# Initialize APIs
openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
notion = Client(auth=os.getenv('NOTION_API_KEY'))
notion_database_id = os.getenv('NOTION_DATABASE_ID')

app = Flask(__name__)

# Helper function to extract JSON from markdown code blocks
def extract_json_from_markdown(text):
    # Pattern to match JSON code blocks
    pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    matches = re.findall(pattern, text)
    
    if matches:
        # Return the first JSON code block content
        return matches[0].strip()
    else:
        # If no code blocks found, return the original text
        return text

# Helper function to format action items and questions for Notion
def format_for_notion(data):
    if isinstance(data, list):
        # Handle list of action items with owners
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
        # Handle list of questions
        elif data and isinstance(data[0], str):
            return "\n".join([f"• {question}" for question in data])
        # Handle other lists
        else:
            return "\n".join([f"• {str(item)}" for item in data])
    elif isinstance(data, dict):
        # Handle dictionary responses by converting to string
        return json.dumps(data, indent=2)
    else:
        # Return as-is if it's already a string
        return str(data)

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
            model="gpt-4-turbo",  # Use a model you have access to
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2  # Low temperature for more factual, less creative output
        )

        # 4. Parse the AI's response
        ai_content = response.choices[0].message.content
        
        # Debug: Print the raw AI response
        print(f"Raw AI response: {ai_content}")
        
        # Extract JSON from markdown code blocks if present
        cleaned_content = extract_json_from_markdown(ai_content)
        print(f"Cleaned content: {cleaned_content}")
        
        # The AI should return a JSON string. We need to convert it to a Python dict.
        try:
            summary_data = json.loads(cleaned_content)
            print(f"Parsed summary data: {summary_data}")
        except json.JSONDecodeError as e:
            # Fallback if the AI doesn't return valid JSON
            print(f"JSON decode error: {e}")
            summary_data = {
                "summary": ai_content,
                "action_items": "Could not parse action items.",
                "key_questions": "Could not parse key questions."
            }

        # 5. Format data for Notion API compatibility
        # Ensure all values are properly formatted strings
        summary_str = format_for_notion(summary_data.get('summary', ''))
        action_items_str = format_for_notion(summary_data.get('action_items', ''))
        key_questions_str = format_for_notion(summary_data.get('key_questions', ''))
        
        print(f"Formatted action items: {action_items_str}")
        print(f"Formatted key questions: {key_questions_str}")

        # 6. Save the structured data to Notion
        new_page = notion.pages.create(
            parent={"database_id": notion_database_id},
            properties={
                "Meeting Name": {"title": [{"text": {"content": meeting_name}}]},
                "Summary": {"rich_text": [{"text": {"content": summary_str}}]},
                "Action Items": {"rich_text": [{"text": {"content": action_items_str}}]},
                "Key Questions": {"rich_text": [{"text": {"content": key_questions_str}}]},
                "Date": {"date": {"start": datetime.now().isoformat()[:10]}}  # Today's date
            }
        )

        # 7. Send success back to the frontend
        # Handle potential missing 'url' key in Notion response
        notion_url = new_page.get("url", "No URL available")
        
        return jsonify({
            "message": "Summary created successfully!",
            "notion_url": notion_url
        })

    except Exception as e:
        print(f"Error in /summarize: {str(e)}")  # Log the error for debugging
        import traceback
        traceback.print_exc()  # Print full traceback
        return jsonify({'error': str(e)}), 500

#if __name__ == '__main__':
#    app.run(debug=True)
if __name__ == '__main__':
    # Get port from environment variable or default to 5000
    port = int(os.environ.get("PORT", 5000))
    # Run on all available interfaces (0.0.0.0) instead of localhost only
    app.run(host='0.0.0.0', port=port, debug=False)  # Set debug=False for production