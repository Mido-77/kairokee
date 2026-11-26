KAIROKEE  -  karaoke from any song (Windows version)
=====================================================

Search a song, and Kairokee removes the singer, keeps the music, and shows the
lyrics in time with the music. You can turn the singer and the ad-libs up or down.

For personal use only, with music you are allowed to use. Nothing you make is
uploaded or shared anywhere. Everything stays on your PC.


HOW TO START
------------
1. Right-click the ZIP file, choose "Extract All", and extract it to any folder.
   (Do not run it from inside the ZIP.)
2. Open the new folder and double-click  Kairokee.bat
3. Windows may say "Windows protected your PC". This happens because the app is
   not signed with a paid certificate. Click "More info", then "Run anyway".
4. The FIRST time, a black window installs everything. This downloads about 3 GB
   and takes 10 to 60 minutes, depending on your internet. Keep the window open and the PC awake.
   If the internet drops, just open Kairokee.bat again - it continues.
5. After that, Kairokee starts in a few seconds and opens your browser at
   http://localhost:8000

To stop Kairokee: close the black window.


THE FIRST TIME YOU USE EACH FEATURE
-----------------------------------
Some AI models download only when first needed (once only, then they are kept):
 - Splitting the singer from the ad-libs:  about 1 GB
 - Writing lyrics from the audio:          0.5 GB to 3 GB (depends on your PC)


SPEED
-----
 - PC with a recent NVIDIA graphics card: fast (about 15 seconds per song).
 - PC without one (or with an old one): works, but each song takes several minutes,
   and splitting the ad-libs or writing lyrics can take much longer.
   Kairokee tells you which mode it is using when it starts.
 - If you have an NVIDIA card but Kairokee uses the CPU, update the graphics
   driver from nvidia.com/drivers, then open Kairokee-Repair.bat.


USING IT FROM A PHONE (same Wi-Fi)
----------------------------------
Open Kairokee-Phone.bat instead. The black window shows an address like
http://192.168.1.19:8000 - type it in your phone's browser.
If the phone cannot open it:
 - In Windows, set your home Wi-Fi to "Private":
   Settings > Network & Internet > Wi-Fi > your network > Network profile: Private.
 - If Windows asks to allow access, tick "Private networks" and click Allow.
Only use phone mode on a Wi-Fi you trust: anyone on that Wi-Fi can open the app.
The normal Kairokee.bat can only be opened from this PC.


IF SOMETHING GOES WRONG
-----------------------
 - Open Kairokee-Repair.bat (keeps your songs and downloaded models).
 - If YouTube search or download stops working, just open Kairokee again.
   It updates the YouTube downloader (yt-dlp) automatically once a day.
 - A message about "Visual C++" means Windows needs a free Microsoft component.
   The message gives the download link.
 - Logs are saved in:  %LOCALAPPDATA%\Kairokee\logs
   (If your Windows user name has non-English letters: C:\Kairokee\logs)


WHERE THINGS ARE STORED
-----------------------
 - The program (this folder): small, you can move or replace it.
 - Installed parts, models and your songs:  %LOCALAPPDATA%\Kairokee
   (songs are in the "data\output" folder inside it)

To remove Kairokee completely: delete this folder AND the folder above.


WHAT KAIROKEE DOWNLOADS, AND FROM WHERE
---------------------------------------
Only well-known official sources: GitHub (the installer tool "uv", a private
copy of Python, and Deno - a small helper YouTube downloads need), pytorch.org and pypi.org (the programs), gyan.dev or GitHub
(ffmpeg), Hugging Face and GitHub (the AI models), YouTube (the songs you pick,
through yt-dlp), LRCLIB (lyrics) and Apple iTunes search (cover pictures).
