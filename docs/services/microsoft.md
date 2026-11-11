# Microsoft account

## How it signs in

Add your Microsoft app in **Settings › Developer apps**, then sign in from the tile with the
account, and choose what it may do. Each account is its own connection.

## What it can do

- **Read:** mail and attachments, calendars and events, OneDrive and SharePoint files (what's
  in them too) and who can reach them. A task can also look at a page of a file as a picture,
  and save a copy in the project to work on.
- **Change:** send and draft email, mark and move messages, add and change events, answer
  invitations, add, rename, move and copy files and folders.

It can't upload a file, set a mail rule or auto-reply, or reach another person's mailbox.

## What's held

- **Every write waits for your approval,** even on test data.
- **More than 10 recipients or attendees** needs the connection's name typed, in the app.
- **So do** sending a saved draft and replying to all, because how many people it reaches
  can't be seen from the call.
- **So does** deleting mail, events or files, canceling a meeting, and sharing a file or
  making a link to it.

## Undo

An edit to a draft, an event, a message's marks, or a file's name can be undone. A sent email
can't be unsent.

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

A OneDrive or SharePoint file is read, looked at and saved the same way.
