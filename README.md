# Golf reels agent

AI editing team that turns raw golf clips in Google Drive into Instagram reels and queues them in Metricool.
Runs as a scheduled Claude Code cloud session. See `CLAUDE.md` for the full brief the agents follow.

```
Drive "Golf Reels/01 Inbox"  ->  analyze (find impact)  ->  render 9:16 + slow-mo  ->  montage  ->  review  ->  Drive "02 Edited reels" + Metricool
```

## One-time setup (about 15 minutes)

1. **Drive API access for video files.** On your computer:
   ```bash
   python3 pipeline/drive_auth.py CLIENT_ID CLIENT_SECRET
   ```
   Follow the steps in that file's header to get CLIENT_ID and CLIENT_SECRET from Google Cloud Console.
   It prints three values; add them as **secrets** on the cloud environment (environment menu in the session
   title bar, then Edit): `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET`, `GDRIVE_REFRESH_TOKEN`.
2. **Metricool.** In Metricool, link Google Drive (Settings, Integrations) so it can pull the reel files.
3. **Code in GitHub.** Push this folder to a GitHub repo and add that repo to the cloud environment, so every
   scheduled run checks it out.
4. **Music (optional).** Drop royalty-free mp3 tracks in `Golf Reels/music`.

## Daily use

The first run creates a `Golf Reels` folder in your Drive. Drop clips in `Golf Reels/01 Inbox (drop raw clips here)`. Name them with hints when you can:
`7iron_165y.mov`, `driver_range.mov`, `round_frontnine.mp4`, `1v1_hole3_aarav4_luke5.mp4`.
The scheduled task runs every evening, edits everything in the inbox, uploads reels to `02 Edited reels`,
and creates Instagram reel posts in Metricool. With `PUBLISH_MODE: draft` (default) they wait in the
Metricool planner for one tap of approval; set it to `scheduled` in `CLAUDE.md` for fully hands-off posting.

## Run it by hand

```bash
python3 pipeline/analyze.py work/raw/clip.mov --sheet work/sheets
python3 pipeline/render.py work/raw/clip.mov --impact 5.2 --cx 0.55 --title "7 iron" --out work/out/clip.mp4
python3 pipeline/montage.py work/out/a.mp4 work/out/b.mp4 --intro "Sunday range" --out work/out/reel.mp4
```
Or open the repo in Claude Code and say "process the inbox".
