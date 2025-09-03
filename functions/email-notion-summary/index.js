import { Client } from "@notionhq/client";
import nodemailer from "nodemailer";

// Initialize Notion client
const notion = new Client({ auth: process.env.NOTION_API_KEY });

// Setup Mailtrap transporter with Nodemailer
const transporter = nodemailer.createTransport({
  host: process.env.MAILTRAP_SMTP_SERVER || "live.smtp.mailtrap.io",
  port: parseInt(process.env.MAILTRAP_SMTP_PORT || "587"),
  auth: {
    user: process.env.MAILTRAP_SMTP_USERNAME,
    pass: process.env.MAILTRAP_SMTP_PASSWORD,
  },
});

export default async function (request) {
  try {
    // Query Notion for unsent meeting summaries
    const response = await notion.databases.query({
      database_id: process.env.NOTION_DATABASE_ID,
      filter: {
        property: "Sent",
        checkbox: { equals: false },
      },
    });

    const newPages = response.results;

    for (const page of newPages) {
      const meetingName =
        page.properties["Meeting Name"]?.title?.[0]?.text?.content || "No Title";
      const summary =
        page.properties["Summary"]?.rich_text?.[0]?.text?.content || "No summary provided.";
      const actionItems =
        page.properties["Action Items"]?.rich_text?.[0]?.text?.content || "No action items.";
      const keyQuestions =
        page.properties["Key Questions"]?.rich_text?.[0]?.text?.content || "No key questions.";

      const emailHtml = `
        <h2>New Meeting Summary: ${meetingName}</h2>
        <p><strong>Summary:</strong><br>${summary.replace(/\n/g, "<br>")}</p>
        <p><strong>Action Items:</strong></p>
        <ul>${actionItems
          .split("\n")
          .filter((i) => i.trim())
          .map((i) => `<li>${i}</li>`)
          .join("")}</ul>
        <p><strong>Key Questions:</strong></p>
        <ul>${keyQuestions
          .split("\n")
          .filter((q) => q.trim())
          .map((q) => `<li>${q}</li>`)
          .join("")}</ul>
        <p><strong>View in Notion:</strong> <a href="${page.url}">${page.url}</a></p>
      `;

      // Send email via Mailtrap
      await transporter.sendMail({
        from: `"Meeting Summary Bot" <${process.env.MAILTRAP_VERIFIED_SENDER}>`,
        to: process.env.MAILTRAP_VERIFIED_SENDER, // trial restriction: send only to verified sender
        subject: `Meeting Summary: ${meetingName}`,
        html: emailHtml,
        text: `Meeting Summary: ${meetingName}\n\nSummary:\n${summary}\n\nAction Items:\n${actionItems}\n\nKey Questions:\n${keyQuestions}\n\nView in Notion: ${page.url}`,
      });

      // Call Flask API with valid JSON payload
      await fetch(`${process.env.FLASK_API_BASE_URL}/api/email-notion-summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page_id: page.id }),
      });

      // Update Notion page as sent
      await notion.pages.update({
        page_id: page.id,
        properties: { Sent: { checkbox: true } },
      });
    }

    return new Response(
      JSON.stringify({
        message: `Successfully processed ${newPages.length} meeting summaries.`,
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }
    );
  } catch (error) {
    console.error("Function error details:", error);
    return new Response(
      JSON.stringify({ error: "Internal server error", details: error.message }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
}
