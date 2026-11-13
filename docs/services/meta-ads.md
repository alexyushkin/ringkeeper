# Meta Ads

Campaigns, ad sets, ads, creatives, audiences, catalogs, reporting and Meta's ads help, through
Meta's own ads MCP server (`https://mcp.facebook.com/ads`). It's free.

The catalog shows two ways to reach Meta ads:

- **Meta Ads**: Meta's own server, described here.
- **Meta Ads (Pipeboard)**: a third party, not Meta, with a free tier of 30 calls a week.

## Sign in through your Meta app

Meta Ads signs in with Facebook Login for Business, through your own Meta app: the same app
Facebook Pages and Instagram use (Settings › Developer apps, **Meta**). Your App ID is the client
ID.

1. In your app on Meta for Developers, add the use case **Create & manage ads with ads MCP
   server**.
2. Open **Facebook Login for Business**, then **Settings**, and paste the address the connect
   form shows as a Valid OAuth Redirect URI. It's the same address your Meta app already uses.
3. Add the app in Settings › Developer apps, if it isn't there yet.
4. Under **Connections**, choose **Meta Ads** and click **Sign in with Meta**.

It asks for `ads_mcp_management`, `ads_read`, `ads_management`, `catalog_management`,
`business_management`, `pages_show_list` and `instagram_basic`.

**Your own ad accounts work straight away.** Managing other businesses' ads needs Advanced
Access on `ads_mcp_management`, which means Meta's app review.

Meta gives no refresh token: a sign-in lasts about 60 days. The connection shows the day it ends,
and you hear 7 days before. Then remove it and sign in again.

### Or paste a token

**Use a token instead** takes a user access token, for instance one from the
[Graph API Explorer](https://developers.facebook.com/tools/explorer/) with the permissions above.
It expires: one from the Explorer lasts about an hour. Signing in gives one that lasts about 60
days.

## One ad account or all of them

A project's link reaches every ad account the sign-in can see unless you narrow it. To keep a
project to one, set **Ad account** to its ID when you link it: `act_` followed by the number, like
`act_1234567890`. The number without `act_` counts as the same account. Then:

- a call that names another ad account is refused;
- a call that leaves the account out gets it filled in;
- a tool that names no ad account isn't available on that link: listing every ad account, or
  activating or changing a campaign, ad set or ad by its own ID, could reach another client's.
  Meta's help, the public Ad Library and field lookups still are.

## What it can do

The tools are Meta's own, and **Connections** lists them once you've connected.

- **Reading runs.** Ad accounts, campaigns, ad sets and ads with their numbers, creatives,
  previews, insights and benchmarks, audiences, catalogs, datasets, pixels, A/B tests, activity
  logs, delivery errors and Meta's help.
- **Anything that spends money or goes live needs the connection's name typed:** creating a
  campaign, ad set or ad, activating one, changing one (its budget, bid or status), boosting an
  Instagram post, and anything with a budget or bid in its name. Meta creates campaigns, ad sets
  and ads paused, but one activation starts them, so creating them needs the name too. Deleting
  an audience, a product or a feed needs it as well.
- **Every other change waits for your approval:** creatives, custom audiences, catalogs and
  products, pixel events and A/B tests.

These rules go by a tool's name. You can make a tool stricter, never looser.

Check my setup connects and counts its tools, without a model.

Ringkeeper can't undo anything on Meta, and the card says so.
