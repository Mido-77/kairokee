KAIROKEE  -  karaoke from any song (Mac version)
=================================================

Search a song, and Kairokee removes the singer, keeps the music, and shows the
lyrics in time with the music. You can turn the singer and the ad-libs up or down.

For personal use only, with music you are allowed to use. Nothing you make is
uploaded or shared anywhere. Everything stays on your Mac.


WHICH MACS
----------
 - A Mac with Apple Silicon: M1, M2, M3, M4 or newer
   (Apple menu > About This Mac shows "Chip: Apple M...").
 - macOS 14 Sonoma or newer.
 - 8 GB of memory or more is recommended, and about 12 GB of free disk space.
Macs with an Intel processor are not supported.


HOW TO START
------------
1. Double-click the ZIP file. A folder named "Kairokee" appears next to it.
   You can move that folder anywhere (for example into your Documents folder).
2. Open the folder and double-click  Kairokee.command
3. The first time, macOS blocks it, because the app is not signed with a paid
   Apple certificate. To allow it:
     - Click "Done" (or "OK") in the message.
     - Open System Settings > Privacy & Security, scroll down, and next to
       "Kairokee.command was blocked" click "Open Anyway". Confirm with your password.
     - (On macOS 14 you can also right-click Kairokee.command, choose Open, then Open.)
   You only do this once. Kairokee-Phone.command and Kairokee-Repair.command then open
   without the warning too.
4. The FIRST time, a Terminal window installs everything. This downloads about 3 GB
   and takes 10 to 60 minutes, depending on your internet. Keep the window open.
   (The Mac is kept awake while it installs.)
   If the internet drops, just open Kairokee.command again - it continues.
5. After that, Kairokee starts in a few seconds and opens your browser at
   http://localhost:8000  (if another program already uses that address, Kairokee
   picks the next free one, for example http://localhost:8001, and tells you).

To stop Kairokee: close the Terminal window (or press Ctrl+C in it).
If the Mac asks whether to stop the running program, click "Terminate".
To stop a song that is being prepared, press Stop under the progress list.


WHAT YOU CAN DO WITH A SONG
---------------------------
 - Turn the singer and the ad-libs on or off, and set the music volume.
 - Key: press - or + to sing it lower or higher (takes a few seconds the first time).
 - Speed: slower or faster for practice, without changing the key.
 - Full screen: big lyrics for a TV or a party (press F, or Esc to leave).
   Dots count down before the singing starts again.
 - Use a song file from this device: make karaoke from your own audio or video file
   instead of YouTube (on the home page, under the search box).
 - Delete this song: you can press Undo right away, and deleted songs are kept for
   30 days in the "data/deleted" folder before they are removed.

THE FIRST TIME YOU USE EACH FEATURE
-----------------------------------
Some AI models download only when first needed (once only, then they are kept):
 - Splitting the singer from the ad-libs:  about 1 GB
 - Writing lyrics from the audio:          0.5 GB or 1.5 GB (depends on your Mac's memory)


SPEED
-----
Kairokee uses the Mac's graphics (Metal) to separate the singer from the music.
A song takes about a minute or two on an M1, and less on newer chips.
Splitting the ad-libs takes a few minutes more. Writing lyrics from the audio runs on
the processor, which is fast on Apple Silicon.
Kairokee learns how fast your Mac is and shows the expected time before you start.


USING IT FROM A PHONE (same Wi-Fi)
----------------------------------
Open Kairokee-Phone.command instead. The Terminal window shows an address like
http://192.168.1.19:8000 - type it in your phone's browser.
If the Mac asks "Do you want the application python to accept incoming network
connections?", click Allow.
Only use phone mode on a Wi-Fi you trust: anyone on that Wi-Fi can open the app.
The normal Kairokee.command can only be opened from this Mac.


IF SOMETHING GOES WRONG
-----------------------
 - Open Kairokee-Repair.command (keeps your songs and downloaded models).
 - When a newer Kairokee is available, the home page says so and what changed.
 - If YouTube search or download stops working, just open Kairokee again.
   It updates the YouTube downloader (yt-dlp) automatically once a day.
 - If double-clicking does nothing or says "permission denied", open Terminal and type
   (with a space at the end):   chmod +x
   then drag the three .command files into the Terminal window, and press Return.
 - Logs are saved in:  ~/Library/Application Support/Kairokee/logs
   (In Finder: Go menu > Go to Folder..., then paste that path.)


WHERE THINGS ARE STORED
-----------------------
 - The program (this folder): small, you can move or replace it.
 - Installed parts, models and your songs:  ~/Library/Application Support/Kairokee
   (songs are in the "data/output" folder inside it)

To remove Kairokee completely: delete this folder AND the folder above.


WHAT KAIROKEE DOWNLOADS, AND FROM WHERE
---------------------------------------
Only well-known official sources: GitHub (the installer tool "uv", a private
copy of Python, and Deno - a small helper YouTube downloads need), pypi.org (the
programs), ffmpeg.martin-riedl.de (ffmpeg for Apple Silicon), Hugging Face and GitHub
(the AI models), YouTube (the songs you pick, through yt-dlp), LRCLIB (lyrics) and
Apple iTunes search (cover pictures).
Nothing is installed outside the Kairokee folders: no admin password is needed.
