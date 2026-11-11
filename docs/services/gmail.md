# Gmail (app password)

The simple way to connect one mailbox. For Calendar, Drive and Sheets too, or
for several accounts, use a [Google account](google.md) instead.

## Where to get the key

An [app password](https://myaccount.google.com/apppasswords) (Google needs
2-Step Verification on), and the Gmail address. A work admin may have turned app
passwords off, and accounts with Advanced Protection can't use them.

## What it can do

- **Read:** search mail and read messages (without marking them read).
- **Change:** save a draft, send an email.

## What's held

**Every email waits for your approval** before it's sent. Saving a draft
doesn't wait: only you see it.

## Undo

A sent email can't be unsent.

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
