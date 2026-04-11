Mrija Archive
=============

This app lets you search the mrija.org email archive.

HOW TO START
------------
1. Double-click MrijaArchive.exe

   If Docker Desktop is not installed, the app will download and
   install it automatically (one-time, ~600 MB, takes 5-10 minutes).

2. The app will start and show the email search interface.
   This takes about 30 seconds on first launch.

HOW TO SEARCH
-------------
Type any keyword in the search box and press Enter or click Search.
You can filter by mailbox using the dropdown.
Click any result to read the full email.

HOW TO STOP
-----------
Click the "Stop" button in the top-right corner.
Close the window normally -- the app will stop automatically.

GETTING NEW EMAILS
------------------
When a data update zip is sent to you (MrijaArchive-data-update.zip):
1. Open the zip and copy mail_index.sqlite to:
   %APPDATA%\MrijaArchive\data\index\
   (paste that path into Windows Explorer address bar)
2. Click Stop then Start Again in the app.

SYSTEM REQUIREMENTS
-------------------
- Windows 10 or 11
- Internet connection (first launch only, for Docker Desktop)
- ~2 GB disk space
