# Facebook Pages, Instagram and Threads

All three sign in through an app of your own at Meta. See
[Developer apps](developer-apps.md) for how to add one.

**Your own accounts work now, with no review from Meta.** You need a role on the app, and the
connection goes in as a shared tool, so it isn't tied to one client. Then pick the Page or
account each project may use.

**A client's account needs Meta's review first.** That means Tech Provider status, App Review
and Business Verification. Until then the client's tile says **Needs Meta review** and won't
sign in. Once Meta approves your app, change **Client accounts** on the app in
**Settings > Developer apps**.

Sign-in hasn't been completed against a real Meta app yet, so these tiles say Unverified.

## Where to get the key

Add the app in **Settings > Developer apps**, then sign in from the tile:

- **Facebook Pages:** pick the Pages it may reach on Meta's page.
- **Instagram:** the account must be a professional one, linked to one of those Pages.
- **Threads:** add the Threads use case to your app. It has its own app ID and secret.

If Meta says **Invalid Scopes**, a permission is missing from your app. Meta adds only a few
by itself. Open the app's **Dashboard**, select the use case, click **Customize**, and click
**Add** next to each one the message names.

## What it can do

| | Facebook Pages | Instagram | Threads |
|---|---|---|---|
| Read | Posts, scheduled posts, comments, insights | Posts, comments, insights, posts left today | Posts, replies, insights, posts left today |
| Prepare | Scheduled or unpublished posts | A post, kept unseen for a day | A post or reply, kept unseen |
| Publish | Posts, comments and replies | Posts and replies | Posts and replies |
| Hide | Comments | Comments | Replies |
| Delete | Posts and comments | Posts and comments | Posts |

Instagram and Threads fetch pictures and video from a public address. Instagram pictures must be
JPEG. Neither can schedule through this API, so Facebook Pages are the only ones here that do.
Direct messages aren't available yet.

## What's held

- **Reading runs by itself.**
- **Anything nobody can see yet waits for your approval:** a scheduled or unpublished Page post,
  or a prepared Instagram or Threads post. Hiding a comment waits too.
- **A scheduled Page post goes out by itself at its time.** Approving it is what allows that.
- **Publishing, editing a post, replying and deleting need the connection's name typed,** in the
  app itself. They can't be approved from a chat app, and nothing here can be trusted for an hour.

## Good to know

- Each Page keeps its own token. A project held to one Page can't see or reach another.
- A Threads sign-in lasts 60 days, and Ringkeeper renews it while it's in use. Left unused for
  that long, sign in again.
- Meta's docs want an app's secret on a server. Here it's in your computer's vault, like every
  other app's.

## Undo

None of these can be undone here. Delete a post instead, or edit it.
