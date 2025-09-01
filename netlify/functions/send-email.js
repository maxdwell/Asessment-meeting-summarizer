const { Client } = require("@notionhq/client");
const sgMail = require("@sendgrid/mail");

// Initialize clients
const notion = new Client({ auth: process.env.NOTION_API_KEY });
sgMail.setApiKey(process.env.SENDGRID_API_KEY);

exports.handler = async (event, context) => {
  try {
    // Query the Notion database for unsent summaries
    const response = await notion.databases.query({
      database_id: process.env.NOTION_DATABASE_ID,
      filter: {
        property: "Sent", // Add a "Sent" checkbox property in your Notion DB
        checkbox: {
          equals: false,
        },
      },
    });

    const newPages = response.results;

    for (const page of newPages) {
      // Extract page properties
      const meetingName = page.properties["Meeting Name"].title[0]?.text.content || "No title";
      const summary = page.properties["Summary"].rich_text[0]?.text.content || "No summary";
      const actionItems = page.properties["Action Items"].rich_text[0]?.text.content || "No action items";
      const keyQuestions = page.properties["Key Questions"].rich_text[0]?.text.content || "No key questions";

      // Format email content
      const emailContent = `
        <h2>New Meeting Summary: ${meetingName}</h2>
        <p><strong>Summary:</strong> ${summary}</p>
        <p><strong>Action Items:</strong></p>
        <ul>${actionItems.split('\n').map(item => `<li>${item}</li>`).join('')}</ul>
        <p><strong>Key Questions:</strong></p>
        <ul>${keyQuestions.split('\n').map(q => `<li>${q}</li>`).join('')}</ul>
        <p>View in Notion: <a href="${page.url}">${page.url}</a></p>
      `;

      // Send email via SendGrid
      const msg = {
        to: process.env.TEAM_LEAD_EMAIL,
        from: process.env.SENDER_EMAIL, // Use a verified sender in SendGrid
        subject: `Meeting Summary: ${meetingName}`,
        html: emailContent,
      };

      await sgMail.send(msg);

      // Mark as sent in Notion
      await notion.pages.update({
        page_id: page.id,
        properties: {
          Sent: {
            checkbox: true,
          },
        },
      });
    }

    return {
      statusCode: 200,
      body: JSON.stringify({ message: "Emails sent successfully" }),
    };
  } catch (error) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: error.message }),
    };
  }
};