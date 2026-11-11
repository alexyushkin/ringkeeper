# File storage (AWS S3, Cloudflare R2, Vercel Blob)

Also any other S3-compatible service: MinIO, Backblaze B2, Wasabi, DigitalOcean Spaces.

## Where to get the key

Make a key limited to this client's bucket.

- **AWS S3:** an access key and its secret, and the region.
- **Cloudflare R2:** an R2 API token's access key and secret, and your account ID from
  the R2 overview page.
- **Vercel Blob:** open the Blob store and copy its read-write token. It starts with
  `vercel_blob_rw_`. A Blob store has no buckets.
- **Another service:** its address, region, access key and secret.

## What it can do

- **Read:** list buckets, list files, read a file: text as it is, and the text in PDF,
  Word, Excel, PowerPoint and .zip files (a long one starts with its outline, then page by
  page). Anything else, up to 2 MB, comes as it is.
- **Look and keep:** look at a PDF's page or a picture the way you see it, and save a copy in
  the project's `attachments` folder to work on.
- **Change:** upload a file, delete a file.

## What's held

- **Listing, reading, looking and saving a copy run.** Looking and saving are in Activity.
- **An upload** waits for your approval, even on test data.
- **Deleting** needs the connection's name typed, in the app.

A project's link can be pinned to a bucket and a folder, so each project stays on its
own files.

## Undo

Uploads and deletes can't be undone here. Versioning in the bucket, if you turn it on,
keeps earlier copies.
