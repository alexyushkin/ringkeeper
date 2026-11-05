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

A task can also read an attachment's text (PDF, Word, Excel, CSV and text files) and a whole
conversation, oldest message first. Reading text from a picture or a scanned PDF needs the
scan reader, a one-time download the desktop app offers the first time a task needs it.
