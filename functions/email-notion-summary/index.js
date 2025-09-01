import { Client } from "@notionhq/client";
import sgMail from "@sendgrid/mail";

// Initialize clients using Railway's environment variables
const notion = new Client({ auth: process.env.NOTION_API_KEY });
sgMail.setApiKey(process.env.SENDGRID_API_KEY);

export default async function (req, res) {
  try {
    // 1. Query the Notion database for unsent summaries
    const response = await notion.databases.query({
      database_id: process.env.NOTION_DATABASE_ID,
      filter: {
        property: "Sent", // Ensure this checkbox property exists in your DB
        checkbox: {
          equals: false,
        },
      },
    });

    const newPages = response.results;

    // 2. Process each new page
    for (const page of newPages) {
      // Extract properties (add error handling for missing fields)
      const meetingName = page.properties["Meeting Name"]?.title[0]?.text?.content || "No Title";
      const summary = page.properties["Summary"]?.rich_text[0]?.text?.content || "No summary provided.";
      const actionItems = page.properties["Action Items"]?.rich_text[0]?.text?.content || "No action items.";
      const keyQuestions = page.properties["Key Questions"]?.rich_text[0]?.text?.content || "No key questions.";

      // 3. Format the email content
      const emailHtml = `
        <h2>New Meeting Summary: ${meetingName}</h2>
        <p><strong>Summary:</strong><br>${summary.replace(/\n/g, '<br>')}</p>
        <p><strong>Action Items:</strong></p>
        <ul>${actionItems.split('\n').filter(item => item.trim()).map(item => `<li>${item}</li>`).join('')}</ul>
        <p><strong>Key Questions:</strong></p>
        <ul>${keyQuestions.split('\n').filter(q => q.trim()).map(q => `<li>${q}</li>`).join('')}</ul>
        <p><strong>View in Notion:</strong> <a href="${page.url}">${page.url}</a></p>
      `;

      // 4. Send email via SendGrid
      const msg = {
        to: process.env.TEAM_LEAD_EMAIL, // Single recipient
        from: process.env.SENDER_EMAIL, // Your verified SendGrid sender
        subject: `Meeting Summary: ${meetingName}`,
        html: emailHtml,
      };

      await sgMail.send(msg);

      // 5. Mark page as sent in Notion
      await notion.pages.update({
        page_id: page.id,
        properties: {
          Sent: { checkbox: true },
        },
      });
    }

    // 6. Return success response
    res.json({ message: `Successfully processed ${newPages.length} meeting summaries.` });

  } catch (error) {
    console.error("Function error details:", error);
    res.status(500).json({ 
      error: "Internal server error", 
      details: error.message 
    });
  }
}