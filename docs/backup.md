# Backup and moving to another computer

**Settings › Backup** (desktop app) makes one file with everything Ringkeeper
knows: clients, projects, connections and their keys, agents, routines,
settings and history. It's locked with a password you choose when you make it.
Without that password nobody can open it, including you, so keep it somewhere
safe and apart from the file.

**Project folders aren't in the backup.** They stay where they are. A restored
project points to the same folder path; on a new computer, copy the folders
over yourself and, if the path changed, set each project's folder on its page.

## Make a backup

Choose a password (at least 8 characters), a folder (Documents › Ringkeeper
backups unless you pick another), and **Save the backup**. The file ends in
`.rkbackup`.

## Restore on another computer

1. Install Ringkeeper and connect Claude in the first run. On the next step,
   choose **Restore a backup instead**: the backup brings your projects and
   connections.
2. **Settings › Backup › Restore a backup**: choose the file, type its password,
   and **Show what's in it**. You see the clients, projects (and whether each
   project's folder is on this computer), connections and how much history
   comes in. Nothing has changed yet.
3. **Restore and restart.** The keys go into this computer's vault, and
   Ringkeeper restarts with the backup's data. What was here before is kept
   beside it, as `ringkeeper.db.before-restore` in the data folder.

## Automatic backups

Turn on **Make a backup every week**, choose a folder (one your cloud drive
copies is a good choice), how many to keep, and a password. The password is
kept in Ringkeeper's vault on this computer so it can lock each backup; write
it down, because you need it to restore. The first backup is made right away,
then one every week; older ones are deleted, keeping the last few you chose.

## On a server

The server (Docker) keeps its data in Postgres and its data folder: back those
up with your usual tools (`pg_dump`, and a copy of the data folder with its
master key).
