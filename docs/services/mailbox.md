# Mailbox (Gmail, Outlook, Zoho, Yahoo or any IMAP)

## Where to get the password

Choose your provider and enter the address and password. Most providers want an
**app password**, not your normal password:

- **Gmail:** [app passwords](https://myaccount.google.com/apppasswords), which need
  2-Step Verification.
- **Zoho:** an app password if two-factor sign-in is on, with IMAP turned on in Mail
  settings.
- **Yahoo:** Account security › Generate app password.
- **Another provider:** its IMAP and SMTP servers too.

**Microsoft:** for Microsoft 365, use the [Microsoft account](microsoft.md) tile instead.
Microsoft has turned password sign-in off for IMAP there, and is doing the same for SMTP.

## What it can do

- **Read:** folders, searching, reading a message. Reading never marks mail as read.
- **Change:** save a draft, move a message, send, move to the trash.

## What's held

- **Reading runs.**
- **Drafts, moving and sending** wait for your approval, even on test data.
- **Deleting, and sending to more than 10 people at once,** need the connection's name
  typed, in the app. Chat apps and notifications can't approve those.
- **A test to your own address runs by itself,** if you set one. The AI can't change
  where it goes.

## Undo

Changes to a mailbox can't be undone here. A deleted message stays in the mailbox's
trash for as long as your provider keeps it.

## Long emails and attachments

A task reads the whole message, up to 200,000 characters at a time, as clean text: links keep
their address and tables keep their rows. A longer one comes in parts, and each part says how
long the message is and where the next part starts. The earlier messages a reply quotes are
kept apart from what's new, and the task can ask for them.

A task can also read a whole conversation, oldest message first, and an attachment:

- **Its text:** PDF, Word, Excel, PowerPoint (with its speaker notes), CSV and text files, an
  attached email (.eml, or Outlook's .msg) and its own attachments, and a .zip, a file at a
  time. A long file starts with an outline: how many pages, which have text and which have
  pictures, and the first two pages. The task then reads the pages it needs, so one long PDF
  doesn't use up the task.
- **A page as a picture:** the task can look at one page, or one picture, the way you see it.
  That keeps a table's or a chart's layout, which reading its text loses.
- **A copy to work on:** saved in the project's `attachments` folder (where files you drop on
  a request go too), and listed with the task's files. Nothing changes in the mailbox.

Looking at a page and saving a copy run without asking, and both are in Activity. Reading text
from a picture or a scanned page needs the scan reader, a one-time download the desktop app
offers the first time a task needs it. The pictures on a page that also has text are read too,
like a price table pasted under a heading. A file it can't read, like a Numbers spreadsheet,
is named, with what would work instead (a PDF or .xlsx copy).
