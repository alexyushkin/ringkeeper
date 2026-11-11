# Google (Gmail, Calendar, Drive, Sheets, Docs, Search Console, Analytics)

Google accounts sign in through **your own Google app**: you make it once, for
free, and then add as many clients' Google accounts as you need. This is in the
desktop app.

## Make the app (once, about 10 minutes)

**Settings › Developer apps** has every step with links that open the right page:

1. In the [Google Cloud console](https://console.cloud.google.com/projectcreate),
   signed in with your own account, create a project. No billing needed.
2. Turn on the Gmail, Calendar, Drive, Sheets and Docs APIs.
3. **Google Auth Platform › Branding › Get started**: app name "Ringkeeper",
   audience **External**. Fill in the home page, privacy policy link and
   authorized domain that Settings shows, then save.
4. **Audience › Publish app.** Left in "Testing", Google signs accounts out
   after 7 days.
5. **Data Access**: paste the scopes Settings lists.
6. **Clients › Create client**: type **Desktop app**. Copy the client ID and
   secret (Google shows the secret only once) and paste both into Settings.

## Add a Google account

**Settings › Developer apps › Add a Google account** (or the Google account tile under
Connectors › Browse connectors), then sign in with the client's account. Google
will say it hasn't verified the app: click **Advanced › Go to Ringkeeper**.
It's your own app, so that's expected. Choose what it may do (mail, calendar,
files, sheets) when you add it.

## What waits for approval

Reading runs. Every write (sending, sharing, invites, edits, uploads, drafts,
moving to the trash) asks you first. Per account, you can later let a tool run
without asking, except sending: an email always asks, and more than 10 people at once
needs the connection's name typed, in the app itself.

## Good to know

- An unverified app can sign in up to 100 accounts over its whole life.
- An account stops working if its password changes (for mail), if it's unused
  for six months, or if its owner removes Ringkeeper. Sign in again then.
- A client's Workspace admin may need to trust your app's client ID.

## Docs

Choose **Docs: read** or **Docs: read, create and edit** when you sign in, or later with
Change access. An account added before Docs was here doesn't have it: open it under
Connections, choose **Change access**, tick Docs and sign in again. If your Google app was
made before then too, first turn on the
[Google Docs API](https://console.cloud.google.com/apis/library/docs.googleapis.com) and add the
`documents` and `documents.readonly` scopes under Data Access.

- **Reading a document runs:** its title and text, tables as rows.
- **Making a document waits for your approval.** It's in the account's Drive, and only the
  account can see it until someone shares it.
- **Editing a document waits for your approval, every time:** replacing a piece of text
  everywhere it appears, or adding text at the end. A document may be someone else's, so unlike
  other Google writes, you can't set an edit to run without asking. If the document changes
  between the request and your approval, the edit doesn't run; ask again.
- Ringkeeper can't undo an edit. Google Docs keeps every version: **File › Version history**.

## Search Console and Analytics

Choose **Search Console: read** and **Analytics: read** when you sign in, or later with
Change access. Both only read: search clicks, impressions and positions, and Analytics
reports. If your Google app was made before these were added, first turn on the
[Search Console](https://console.cloud.google.com/apis/library/searchconsole.googleapis.com),
[Analytics Data](https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com)
and [Analytics Admin](https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com)
APIs, and add the `webmasters.readonly` and `analytics.readonly` scopes under Data Access.

## YouTube

Choose what the account may do when you sign in, or later with Change access: **read videos and
comments**, **upload videos**, **edit and delete videos, reply to comments**, and **read
analytics**. First turn on the
[YouTube Data](https://console.cloud.google.com/apis/library/youtube.googleapis.com) and
[YouTube Analytics](https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com)
APIs, and add the `youtube.readonly`, `youtube.upload`, `youtube.force-ssl` and
`yt-analytics.readonly` scopes under Data Access. A sign-in reaches one channel: the one you pick
on Google's page.

**A video is private unless you choose otherwise.** Ringkeeper always sends its privacy, because
YouTube's own default is public.

- **Reading runs by itself.**
- **A private video waits for your approval,** scheduled or not. A scheduled one goes public by
  itself at its time, and approving it is what allows that.
- **Public and unlisted videos, edits, replies and deletes need the connection's name typed,** in
  the app itself. They can't be approved from a chat app.
- **A time that has passed is refused,** even if it passed while the card waited: YouTube would
  publish at once.

A video comes from the project's own folder. YouTube allows 100 uploads a day.

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

A file in Drive is read, looked at and saved the same way. A Doc, a Sheet or a Slides deck is
looked at as its PDF, and saved as Word, Excel or PowerPoint.
