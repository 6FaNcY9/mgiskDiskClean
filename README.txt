Mrija Archive
=============

Search the mrija.org email archive from your Windows PC.
No Docker, WSL, or server login is required for normal use.


HOW TO START
------------
Double-click MrijaArchive.exe.

The app opens a desktop window with the local archive viewer.
First launch can take a few seconds while the local server starts.


ARCHIVE DATA
------------
The app reads a local SQLite archive database.

If archive data is bundled with the package, it loads automatically.
If the app starts without data, contact your administrator for a current
SQLite archive file or an updated package.


HOW TO SEARCH
-------------
Type in the search box to search email subject, sender, recipients, and body.

Use the left sidebar to filter results:

  - Mailbox: show all mailboxes or one selected mailbox.
  - Date from / Date to: limit results by message date.
  - Attachments: show any emails, emails with attachments, or emails without
    attachments.

Click Browse to list messages without entering a search term.
Click any email row to read the full message in the detail pane.


ATTACHMENTS
-----------
Attachments are shown below the selected email when they exist.
Click an attachment name to download it from the local archive data.


UPDATES
-------
If an update server is configured, use the Update button in the top bar.
The app downloads the new archive, verifies its checksum, and loads it locally.


HOW TO STOP
-----------
Close the MrijaArchive window. The local server stops with the app.


SYSTEM REQUIREMENTS
-------------------
Windows 10 or 11 (64-bit)
Enough disk space for the app and archive data


TROUBLESHOOTING
---------------
App shows a startup error:
  Close and reopen MrijaArchive.exe.

Search shows no results:
  Clear filters in the left sidebar and try Browse.

Attachments do not open:
  Ask your administrator to confirm the package includes the attachment data.

If problems persist, contact your administrator.
