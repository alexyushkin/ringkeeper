# Backup and moving to another computer

**Settings › Backup** (desktop app) makes one file with everything Ringkeeper
knows: clients, projects, connections and their keys, agents, routines,
settings and history. It's locked with a password you choose when you make it.
Without that password nobody can open it, including you, and it can't be
recovered. You need it on the new computer, so keep it in a password manager,
apart from the file.

## Where to keep it

A backup on the same disk as Ringkeeper is lost with the computer. To restore
on a new one, keep it somewhere that leaves this computer:

- **A synced folder.** Next to **Browse**, Settings › Backup has a button for each
  synced folder signed in on this computer: **Use OneDrive**, **Use Google Drive**,
  **Use Dropbox**, **Use iCloud Drive**. Click one and it shows the folder it chose, a
  "Ringkeeper backups" folder in that account's own folder, and that the service
  uploads it. Ringkeeper uploads nothing itself: it writes the file, and that
  service's own app copies it. A folder is offered only when its app has an account
  signed in here (OneDrive's accounts in Windows' settings, Dropbox's linked
  account, Google Drive's own drive while it runs, iCloud Drive on a Mac): a
  OneDrive folder with no account signed in syncs nowhere. If the app is signed in
  but not running, the button says it won't upload until it is. iCloud for Windows
  isn't offered, since Ringkeeper can't tell whether it's signed in: Browse to its
  folder instead. With none found, the screen says so.
- **Another drive**, like a USB drive or a second disk.
- **A folder you back up another way** (iDrive, Backblaze, Time Machine...). The
  default folder, Documents › Ringkeeper backups, is in Documents, so most backup
  tools already include it.

When the folder is on the same disk as Ringkeeper and isn't a synced folder,
Settings › Backup says so: "This backup is on the same disk as Ringkeeper. If this
computer is lost, so is the backup." If you back that folder up another way,
tick **I back this folder up another way**: the warning gives way to a line naming
the folder, and untick it to see the warning again.

**Project folders aren't in the backup.** They stay where they are. A restored
project points to the same folder path; on a new computer, copy the folders
over yourself and, if the path changed, set each project's folder on its page.

## Make a backup

Choose a password (at least 8 characters), a folder (Documents › Ringkeeper
backups unless you pick another, or a synced folder), and **Save the backup**.
The file ends in `.rkbackup`.

## Restore on another computer

1. Install Ringkeeper and connect Claude in the first run. On the next step,
   choose **Restore a backup instead**: the backup brings your projects and
   connections.
2. **Settings › Backup › Restore a backup**: choose the file (from your synced
   folder or wherever you kept it), type its password,
   and **Show what's in it**. You see the clients, projects (and whether each
   project's folder is on this computer), connections and how much history
   comes in. Nothing has changed yet.
3. **Restore and restart.** The keys go into this computer's vault, and
   Ringkeeper restarts with the backup's data. What was here before is kept
   beside it, as `ringkeeper.db.before-restore` in the data folder.

## Back up to Google Drive

**Settings › Backup › Back up to Google Drive** uploads each backup, the ones you make and
the weekly ones, into a "Ringkeeper backups" folder Ringkeeper creates in your Drive. It's
the same file, locked with the same password, and the last few are kept there (as many as
you keep here). The copy on this computer is still saved.

- **Which account:** one of your own Google accounts (a shared one, or your own client's),
  never a client's. It needs "Drive: files Ringkeeper creates", which lets Ringkeeper see
  only the files it makes. If none of yours has it, **Add Drive access** signs that account
  in again with it.
- **What you see:** when the last upload finished, with a link to the file. If an upload
  doesn't work, Home and Settings say so, and the copy on this computer is kept.
- **Restoring:** on the new computer, download the file from Drive in your browser, then
  pick it under **Restore a backup** with its password.

## Automatic backups

Turn on **Make a backup every week**, choose a folder (a synced folder is a good
choice), how many to keep, and a password. The password is stored only in
Ringkeeper's vault on this computer, so it can lock each backup. You need it on
the new computer and it can't be recovered: keep it in a password manager. The
first backup is made right away, then one every week; older ones are deleted,
keeping the last few you chose.

While they're off, Home asks once to turn them on, after your first real task
(Check my setup's and the demo's don't count). **Turn them on** opens Settings ›
Backup at the folder choice. **Not now** hides it for good, and so does turning
them on, even if you turn them off again later.

## On a server

The server (Docker) keeps its data in Postgres and its data folder: back those
up with your usual tools (`pg_dump`, and a copy of the data folder with its
master key).
