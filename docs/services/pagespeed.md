# PageSpeed Insights

Google's free check of a page's speed: the Lighthouse scores, Core Web Vitals from real
visitors when Google has them, and the fixes that would save the most time.

## Where to get the key

PageSpeed Insights needs a Google Cloud API key. It's free.

1. In the [Google Cloud console](https://console.cloud.google.com/projectcreate), pick a project
   or make one. Your Google app's project works. No billing needed.
2. Turn on the
   [PageSpeed Insights API](https://console.cloud.google.com/apis/library/pagespeedonline.googleapis.com).
3. Under **APIs & Services › Credentials**, choose **Create credentials › API key**. Restrict it
   to the PageSpeed Insights API.
4. Under **Connectors › Browse connectors**, choose **PageSpeed Insights** and paste the key.
   One key works for every client's sites, so add it as **your own tool** and link it to the
   projects that need it.

**Test** checks the key on example.com. A `PAGESPEED_API_KEY` in a project's `.env` is found
when you add the project.

## What it can do

- **Check a page:** its address, on a phone (the default) or a computer, for performance and,
  if asked, accessibility, best practices and SEO. A check takes 10 to 60 seconds.
- The answer is the part people act on: the scores out of 100, the lab metrics (Largest
  Contentful Paint, Total Blocking Time, Cumulative Layout Shift and more), real visitors'
  numbers when Google has enough of them, and up to five fixes with the time each would save.

## What's held

Nothing: it only reads. A check loads the page the way a visitor's browser would, and changes
nothing. The key goes to Google in a header, never in an address.

## Recipes that use it

- **Weekly speed check:** every Monday, checks the site's main pages and compares with last week.
- **Monthly search and speed report:** puts Search Console's clicks next to each page's speed,
  with your Google account.
