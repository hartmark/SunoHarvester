# Suno Downloader - Playwright

Super-simple steps to save your Suno session and download your songs (MP3, WAV by default; optional Video) with metadata.

## Quick start
1) Install requirements

```bash
python -m pip install --upgrade pip
pip install playwright
playwright install firefox
```

On Arch Linux, you can install playwright using the package `python-playwright`.

2) Save your login session

```bash
python save-login.py
```
- A Firefox window opens at https://suno.com
- Login to your Suno account. I have only tested Google and Facebook login.
- Do NOT use Google login (Google blocks Playwright logins).
- Once you see you are logged in, return to the terminal and press Enter. This creates `context.json` in this folder.

3) Download your songs

Default (MP3 + WAV only):
```bash
python download-songs.py
```

Also download videos (adds MP4):
```bash
python download-songs.py --videos
```

Behavior with `--videos`:
- For new songs on the page, the script will attempt to download MP3, WAV, and the Video. If the video download fails or times out, it logs a warning and continues — MP3/WAV and metadata are still saved. You can re-run with `--videos` to retry videos later.
- For songs that already exist in `suno-songs.json`, if their `localFiles` do not contain any `.mp4`, the script will attempt to download the video only (best effort). Failures are warnings only and do not stop the run.

General:
- The script opens `https://suno.com/me`, goes through your songs and downloads:
  - MP3 (`.mp3`)
  - WAV (`.wav`)
  - Video (`.mp4`) — only when `--videos` is used
- Files go to `downloads/`
- Files are named with a stable id-based pattern:
  - `<persona> - <title> - <id>.<ext>` (persona may be empty)
- A metadata catalog is maintained in `suno-songs.json`

That’s it.

## What you get
- `downloads/` — your MP3/WAV files (MP4 videos only when `--videos` is used)
- `suno-songs.json` — metadata per song, including title, duration, persona, lyrics, and the local filenames

Example entry:
```json
{
  "id": "<suno-song-id>",
  "title": "Song Title",
  "duration": "2:23",
  "version": "v3",
  "lyrics": "...",
  "style": "...",
  "persona": "Artist Persona",
  "personaUrl": "https://suno.com/user/...",
  "songUrl": "https://suno.com/song/<id>",
  "localFiles": [
    "Artist Persona - Song Title - <id>.mp3",
    "Artist Persona - Song Title - <id>.wav",
    "Artist Persona - Song Title - <id>.mp4"
  ]
}
```

## Notes
- Browser: Runs headless by default for automation. Use `--headed` to run with a visible Firefox window when needed.
- Session: `context.json` must exist in the project folder (created by `save-login.py`). If it expires, just run `save-login.py` again.
- Filenames: Files are named `<persona> - <title> - <id>.<ext>` (persona may be empty). Re-runs will skip files already downloaded with the same id.

## Troubleshooting
- Can’t log in: Use the Facebook button. Avoid Google login.
- Video timeouts: Video (.mp4) downloads often time out, even with a 60-second timeout. Just re-run `python download-songs.py --videos` and it will continue; already-downloaded songs are skipped. You may need to retry a few times if Suno is slow.
- General timeouts: On slow networks or when Suno is busy, any format may time out. Re-run the script — it skips what’s already saved.

## Safety & Legal
Only download content you own rights to or are allowed to download. Keep `context.json` private.
