# Cal.com

Bookings, event types, schedules and availability, through Cal.com's own hosted server.

## Sign in

Under **Connections**, choose **Cal.com** and sign in with your Cal.com account. You approve
what Ringkeeper may do on Cal.com's page, and there's no key to paste: Cal.com's hosted server
signs in rather than taking an API key. Connect one Cal.com account per client, or your own.
Sign-in works from the desktop app and from a server install.

A `CAL_API_KEY` in a project's `.env` is noticed when you add the project, and left there: Cal.com
connects from its tile.

If you connected before Cal.com added contacts, teams or organizations to what its server can do,
disconnect and sign in again to grant them.

## One team or the whole account

A project's link to the connection reaches the whole account unless you narrow it. To keep a
project to one team, set **Cal.com team** to the team's ID number when you link it. Then:

- a call that names another team is refused;
- a call that leaves the team out gets it filled in;
- a tool that works across the whole account, like your own bookings or your list of teams, isn't
  available on that link. Cal.com's help, your profile and links into its app still are.

Cal.com also checks your role in the team on every call.

## What it can do

The tools are Cal.com's own, and **Connections** lists them once you've signed in.

- **Reading runs.** Bookings, event types, schedules, availability, busy times, contacts, teams
  and Cal.com's help.
- **Bookings wait for your approval, every time:** creating, rescheduling, cancelling or
  confirming one, marking an absence, adding an attendee. Each of these can email real people,
  so "Trusted for an hour" doesn't apply here.
- **Every other change waits too:** creating or changing event types, schedules, routing forms,
  memberships, invites and your profile.
- **Deleting needs the connection's name typed:** an event type, a schedule, a membership or a
  contact. It can't be approved from Slack, Telegram or a notification.
- **Its tool for any API operation** goes by the operation's name: a `get` or `list` runs, a
  delete needs the name typed, and anything else waits.

These rules go by a tool's name. You can make a tool stricter, never looser.

Ringkeeper can't undo anything on Cal.com, and the card says so.
