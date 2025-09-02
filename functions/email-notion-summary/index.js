import { Client } from "@notionhq/client";
import { MailerSend, EmailParams, Sender, Recipient } from "mailersend";

// Initialize clients
const notion = new Client({ auth: process.env.NOTION_API_KEY });
const mailersend = new MailerSend({ apiKey: process.env.MAILERSEND_API_KEY });

export default async function (request) {
  try {
    const response = await notion.databases.query({
      database_id: process.env.NOTION_DATABASE_ID,
      filter: {
        property: "Sent",
        checkbox: {
          equals: false,
        },
      },
    });

    const newPages = response.results;

    for (const page of newPages) {
      const meetingName = page.properties["Meeting Name"]?.title[0]?.text?.content || "No Title";
      const summary = page.properties["Summary"]?.rich_text[0]?.text?.content || "No summary provided.";
      const actionItems = page.properties["Action Items"]?.rich_text[0]?.text?.content || "No action items.";
      const keyQuestions = page.properties["Key Questions"]?.rich_text[0]?.text?.content || "No key questions.";

      const emailHtml = `
        <h2>New Meeting Summary: ${meetingName}</h2>
        <p><strong>Summary:</strong><br>${summary.replace(/\n/g, '<br>')}</p>
        <p><strong>Action Items:</strong></p>
        <ul>${actionItems.split('\n').filter(item => item.trim()).map(item => `<li>${item}</li>`).join('')}</ul>
        <p><strong>Key Questions:</strong></p>
        <ul>${keyQuestions.split('\n').filter(q => q.trim()).map(q => `<li>${q}</li>`).join('')}</ul>
        <p><strong>View in Notion:</strong> <a href="${page.url}">${page.url}</a></p>
      `;

      // MailerSend email configuration
      const sentFrom = new Sender(process.env.SENDER_EMAIL, "Meeting Summary Bot");
      const recipients = [new Recipient(process.env.TEAM_LEAD_EMAIL, "Team Lead")];

      const emailParams = new EmailParams()
        .setFrom(sentFrom)
        .setTo(recipients)
        .setSubject(`Meeting Summary: ${meetingName}`)
        .setHtml(emailHtml);

      await mailersend.email.send(emailParams);

      await notion.pages.update({
        page_id: page.id,
        properties: {
          Sent: { checkbox: true },
        },
      });
    }

    return new Response(
      JSON.stringify({ message: `Successfully processed ${newPages.length} meeting summaries.` }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }
    );

  } catch (error) {
    console.error("Function error details:", error);
    return new Response(
      JSON.stringify({ 
        error: "Internal server error", 
        details: error.message 
      }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
}