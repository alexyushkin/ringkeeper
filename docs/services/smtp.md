# Email sending (SMTP)

For sending through any provider: SendGrid, Mailgun, Postmark, Brevo, Amazon SES, your
web host.

## Where to get the key

From your email provider: the SMTP server, and a username and password or SMTP key. The
connection is always encrypted, with SSL (usually port 465) or STARTTLS (usually port
587). The username defaults to the From address.

## What it can do

- **Read:** which address it sends from.
- **Change:** send an email, or a test to your own address.

## What's held

- **Every email waits for your approval,** even on test data.
- **More than 10 people at once** (To, Cc and Bcc together) needs the connection's name
  typed, in the app. Chat apps and notifications can't approve that.
- **A test to your own address runs by itself,** if you set one. The AI can't change
  where it goes.

## Undo

A sent email can't be unsent.
