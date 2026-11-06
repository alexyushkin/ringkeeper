# Google Ads

Google Ads signs in through your [Google app](google.md), so there's no new app to add and no
developer token: Google retired those on 2026-09-09. Access now belongs to the Google Cloud
project that owns your app.

## Where to get the key

1. In that project, turn on the
   [Google Ads API](https://console.cloud.google.com/apis/library/googleads.googleapis.com).
2. On the API's overview page, open **Upgrade access level** and apply for **Explorer**. Google
   usually approves it by itself. Stay on Explorer or Basic: Standard adds an audit.
3. Sign in from the Google Ads tile, with a Google account that can reach the ad account.
4. On each project's link, set the **Ad account** it may use: the 10-digit customer ID.

An application can be turned down if the project is on Google Cloud's Free Trial, or its billing
is suspended. Google suggests upgrading or removing billing, and removing it costs nothing. A
client under a manager account needs the manager's ID too, and tasks can pass it.

## What it can do

- **Read:** any report in Google Ads Query Language, and the accounts this sign-in reaches.
- **Pause:** a campaign, an ad group or an ad.
- **Create:** a Search campaign with its daily budget, ad groups, responsive search ads and
  keywords. Everything starts paused.
- **Spend:** start a campaign, an ad group or an ad, and change a daily budget or a bid.

Only Search campaigns can be made here for now. Payment methods and billing aren't reachable at
all, and neither are creating accounts or changing who has access.

## What's held

- **Reports run by themselves.**
- **Pausing waits for your approval.** It only stops spending.
- **Creating, starting, and changing a budget or a bid need the connection's name typed,** in the
  app itself. They can't be approved from a chat app, and nothing here can be trusted for an hour.
- **The card shows the spend,** like `50.00 USD a day`. Just before the change runs, Ringkeeper
  checks that currency against the account. Starting a campaign also checks its real budget. If
  either differs from the card, nothing changes.
- **Approving a new campaign declares it has no EU political advertising.** Google won't make a
  campaign without that answer, so the card says it in full. If it isn't true, deny the card.
  A campaign that has any can't be made here.

## The daily operations

Google gives each Cloud project one daily allowance, and every client's calls share it:

| Level | Operations a day |
|---|---|
| Explorer | 2,880 |
| Basic | 15,000 |
| Standard | No limit |

**Settings > Developer apps** shows what's been used in the last 24 hours, under Google. Set the
level there to match what Google gave you. A report is one operation, each change is one, and
more pages of a report are free. Google still counts calls it refuses.

This is what this install sent, so Google's own count can differ.

## Undo

Nothing here can be undone by Ringkeeper. Pause it, or change it back with a new approval.
