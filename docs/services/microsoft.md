# Microsoft account

## How it signs in

Add your Microsoft app in **Settings › Developer apps**, then sign in from the tile with the
account, and choose what it may do. Each account is its own connection.

## What it can do

- **Read:** mail, attachments' details, calendars and events, OneDrive and SharePoint files
  and who can reach them. It can't read what's inside a file yet.
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

A task can also read an attachment's text (PDF, Word, Excel, CSV and text files) and a whole
conversation, oldest message first. Reading text from a picture or a scanned PDF needs the
free `tesseract` program on this computer. Without it, the task is told it couldn't be read.
