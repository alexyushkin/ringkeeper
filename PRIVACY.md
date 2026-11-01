# Ringkeeper privacy policy

Last updated: November 1, 2026

Ringkeeper is a free, open-source app by Alexey Yushkin. This policy covers
the Ringkeeper app and the access to Google accounts you give it.

## Where it runs

Ringkeeper runs on your own computer (or on a server you run yourself). There
is no Ringkeeper service or account behind it: your data is never sent to me,
and the app sends no analytics or usage data.

## Google data

When you connect a Google account, you choose what Ringkeeper may use (Gmail,
Drive, Calendar or Sheets), and Google asks you to approve it.

- **Used** only to carry out what you ask: a task you start, and the steps an
  agent takes on it. Sending, sharing, editing, drafting and deleting wait for
  your approval, unless you let a tool run without asking.
- **Stored** only on your computer: the account's sign-in tokens in
  Ringkeeper's encrypted vault (its key is kept in Windows Credential Manager),
  and what a task reads in Ringkeeper's data folder (the task, its log and the
  activity record) and in your project folders, when a task saves it there.
- **Shared** with no one except:
  - **Google**, to carry out the request;
  - **Anthropic**, the model provider, as part of handling a request: what an
    agent reads while working on your task is sent to Claude (with your own API
    key or Claude subscription) to process it. Anthropic's terms and privacy
    policy apply to that.

  If you connect other services yourself and ask a task to use them (say,
  "add these leads to Acme's Airtable"), what the task sends there is your
  request too. If you turn on Telegram notifications, a short summary of each
  approval and question goes to your Telegram chat.

Google data is never sold, used for advertising, or used by Ringkeeper to
train AI models. Ringkeeper's use and transfer of information received from
Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Removing access

- **In Ringkeeper:** Connections › the account › Change › Remove connection.
  This deletes its tokens from the vault and asks Google to revoke the
  sign-in.
- **In your Google account:** [Third-party apps and services](https://myaccount.google.com/connections)
  › Ringkeeper › Delete all connections.
- **Everything:** uninstall Ringkeeper with "Also delete my data" ticked. That
  removes its data folder and its key. Your project folders are yours and stay.

## Contact

Questions: [alex@yushkin.us](mailto:alex@yushkin.us). Changes to this policy
are published here, with the date at the top. It's also at
<https://yushkin.us/ringkeeper/privacy>.
