import re
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from playwright.sync_api import Playwright, sync_playwright, TimeoutError as PlaywrightTimeoutError
import argparse

JSON_FILE = "suno-songs.json"

def load_songs() -> List[Dict]:
    if not os.path.exists(JSON_FILE):
        return []
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []

def save_songs(songs: List[Dict]) -> None:
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)

def find_by_id(songs: List[Dict], song_id: str) -> Optional[Dict]:
    if not song_id:
        return None
    for s in songs:
        if str(s.get("id", "")) == str(song_id):
            return s
    return None

def upsert_song(songs: List[Dict], song: Dict) -> None:
    # Primary: match by unique ID if present
    sid = str(song.get("id", ""))
    if sid:
        for idx, s in enumerate(songs):
            if str(s.get("id", "")) == sid:
                songs[idx] = song
                return

    songs.append(song)

def get_id(card) -> str:
    """
    Extract the song GUID from the card by finding the song anchor.
    Expected format in DOM: a href="/song/<uuid>".
    Returns the lowercase UUID string if found, else empty string.
    """
    a = card.locator("a[href^='/song/']").first
    if a.count() == 0:
        return ""
    href = (a.get_attribute("href") or "").strip()
    if not href:
        return ""
    m = re.search(r"/song/([0-9a-fA-F-]{36})", href)
    if m:
        return m.group(1).lower()
    # Fallback: try to capture everything after /song/ up to delimiter
    m2 = re.search(r"/song/([^?/#]+)", href)
    return (m2.group(1).lower() if m2 else "")

def get_title(card) -> str:
    # Strict extraction: use only the primary selector; fail hard if missing/invalid.
    # Title: span.line-clamp-1[title] -> attribute 'title'
    title_loc = card.locator("span.line-clamp-1[title]").first
    if title_loc.count() == 0:
        raise ValueError("Title element not found using primary selector: span.line-clamp-1[title]")
    title_raw = (title_loc.get_attribute("title") or "").strip()
    if not title_raw:
        raise ValueError("Title attribute empty on primary selector element")
    return re.sub(r"\s+", " ", title_raw)

def get_duration(card) -> str:
    # Strict: use only the primary overlay selector; if missing or invalid, raise.
    sel = "[data-testid=\"song-row-play-button\"] div.relative > span.font-mono"
    loc = card.locator(sel)
    if loc.count() == 0:
        raise ValueError("Duration badge not found using primary selector: " + sel)
    txt = (loc.first.inner_text() or "").strip()
    if not re.match(r"^[0-5]?\d:[0-5]\d$", txt):
        raise ValueError(f"Duration text invalid (expected mm:ss), got: '{txt}'")
    return txt

def get_lyrics(page, card) -> str:
    # Open Song Details -> Edit Displayed Lyrics -> read textbox value, then close.
    card.click(button='right')

    details_btn = page.get_by_role("button", name="Song Details")
    details_btn.wait_for(state="visible", timeout=5000)
    details_btn.click()

    edit_btn = page.get_by_role("button", name="Edit Displayed Lyrics")
    edit_btn.wait_for(state="visible", timeout=5000)
    edit_btn.click()

    tb = page.get_by_role("textbox", name="Add lyrics")
    tb.wait_for(state="visible", timeout=5000)
    text = (tb.input_value() or "").strip()

    page.keyboard.press("Escape")
    return text

def get_style(card) -> str:
    """
    Extract the style/description text from a song card.

    Primary strategy: read the `title` attribute of the compact description block,
    which appears as a `div` with small text classes (e.g. `text-xs line-clamp-1`).

    Fallback: scan all elements with a `title` attribute inside the card and pick
    a reasonably long, comma-separated string (the description is typically a
    comma-separated list of descriptors/phrases).
    """
    # Primary selector observed in the DOM: a small text block with a title attr
    primary_sel = "div.text-xs.line-clamp-1[title]"
    loc = card.locator(primary_sel)
    if loc.count() > 0:
        txt = (loc.first.get_attribute("title") or "").strip()
        if txt:
            return re.sub(r"\s+", " ", txt)

    # Fallback: find any [title] within the card that looks like a style string
    candidates = card.locator("[title]")
    for i in range(candidates.count()):
        t = (candidates.nth(i).get_attribute("title") or "").strip()
        # Heuristic: style is usually long and contains commas separating phrases
        if t and ("," in t) and len(t) >= 20:
            return re.sub(r"\s+", " ", t)

    return ""

def get_persona(card) -> Tuple[str, str]:
    """
    Extract persona name and URL from a song card.

    Strategy:
    - Find the anchor inside the card whose href starts with "/persona/".
    - Persona name: anchor text or `title` attribute.
    """
    # Locate the persona anchor
    a = card.locator("a[href^='/persona/']").first
    if a.count() == 0:
        return "", ""

    name = (a.inner_text() or "").strip()
    if not name:
        name = (a.get_attribute("title") or "").strip()

    href = (a.get_attribute("href") or "").strip()
    if href and href.startswith("/"):
        url = f"https://suno.com{href}"
    else:
        url = href

    return name, url

def get_version(card) -> str:
    """
    Extract the model/version tag from the song card.

    Acceptable examples: v2, v3, v3.4, v4, v4.5, v4.5+, v4.5-all, v5

    Strategy:
    - Scan all span elements in the card and return the first text that matches
      the strict version pattern.
    - As a safety net, also consider short badge-like tokens that start with 'v'
      and are up to 8 characters if the strict pattern fails.
    """
    strict_re = re.compile(r"^v\d+(?:\.\d+)?(?:\+|-all)?$", re.IGNORECASE)

    spans = card.locator("span")
    count = spans.count()

    # First pass: strict regex match
    for i in range(count):
        txt = (spans.nth(i).inner_text() or "").strip()
        if strict_re.match(txt):
            return txt

    if not txt:
        print("No version tag found in card.")
        return "N/A"

def _sanitize_filename(name: str) -> str:
    # Remove or replace characters not allowed on common file systems
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def download_song(page, card, download_dir, format_button: str, final_basename: Optional[str] = None) -> str:
    """
    Download the song using the given submenu button name and ensure the saved
    file extension matches the chosen button.

    format_button must be one of: "MP3 Audio", "WAV Audio", "Video".
    """
    # Map the submenu button to desired file extension
    ext_map = {
        "WAV Audio": ".wav",
        "MP3 Audio": ".mp3",
        "Video": ".mp4",
    }
    if format_button not in ext_map:
        raise ValueError(f"Unsupported format_button: {format_button}")
    desired_ext = ext_map[format_button]

    # Open context menu and hover Download to show submenu
    card.click(button='right')

    download_button_in_menu = page.get_by_role("button", name="Download")
    download_button_in_menu.wait_for(state="visible", timeout=250)
    download_button_in_menu.hover()
    download_button_in_menu.click()

    # Click the specific sub-option locator (do not click yet for MP3/Video)
    format_btn = page.get_by_role("button", name=format_button)
    format_btn.wait_for(state="visible", timeout=250)

    timeout_ms = 10000 if format_button == "WAV Audio" else 120000
    # Wait for the download according to how each format triggers it
    start_time = time.perf_counter()
    try:
        if format_button == "WAV Audio":
            # WAV shows a modal with a secondary confirm button
            format_btn.click()
            with page.expect_download(timeout=timeout_ms) as download_info:
                dl_btn = page.get_by_role("button", name="Download File")
                dl_btn.wait_for(state="visible", timeout=5000)
                dl_btn.click()
            # Best-effort wait for the modal to close (it may already be gone)
            try:
                dl_btn.wait_for(state="hidden", timeout=5000)
            except Exception:
                pass
        else:
            # MP3 and Video trigger the download immediately upon submenu click
            with page.expect_download(timeout=timeout_ms) as download_info:
                format_btn.click()
                
    except PlaywrightTimeoutError as e:
        raise PlaywrightTimeoutError(f"Timed out waiting for {format_button} download after {timeout_ms} ms. ") from e

    download = download_info.value

    suggested = download.suggested_filename or "download"

    # Build final filename: keep provided basename or suggested name, but enforce extension by button
    if final_basename:
        base = _sanitize_filename(final_basename)
    else:
        base = _sanitize_filename(os.path.splitext(suggested)[0])

    # Ensure the filename has exactly the desired extension
    filename = f"{base}{desired_ext}"

    filepath = os.path.join(download_dir, filename)
    # Overwrite if exists to ensure completeness (files are named with stable id)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        print(f"Warning: failed to remove existing file before overwrite: {filepath} ({e})")
    download.save_as(filepath)

    elapsed = time.perf_counter() - start_time
    print(f"Downloaded ({format_button}): {filename} - {elapsed:.2f} seconds")

    return os.path.basename(filepath)

def _process_current_page(page, songs_json: List[Dict], download_dir: str, download_video: bool = False, page_index: int = 1) -> int:
    # Find song cards on the current page and process them
    song_cards = page.get_by_role("button", name=re.compile(r"^Play Song"))
    try:
        song_cards.first.wait_for(timeout=30000)
    except Exception as e:
        print(f"Couldn't find any songs on this page. Error: {e}")
        return 0

    song_count = song_cards.count()
    print(f"Found {song_count} songs on this page. Processing...")

    processed = 0
    for i in range(song_count):
        print(f"--- Processing song {i + 1} of {song_count} (page {page_index}) ---")
        card = song_cards.nth(i)

        id = get_id(card)
        existing = find_by_id(songs_json, id)
        if existing:
            # Special case: if --videos flag is used but the existing entry has no .mp4 in localFiles,
            # download the video now and update the JSON store.
            if download_video:
                local_files = existing.get("localFiles")
                has_mp4 = any(str(local_file).lower().endswith(".mp4") for local_file in local_files)
                if not has_mp4:
                    video_file = download_song(page, card, download_dir, "Video")
                    local_files.append(video_file)
                    existing["localFiles"] = local_files
                    upsert_song(songs_json, existing)
                    save_songs(songs_json)
            # In all cases, skip further processing for this card
            print(f"Song {id} already exists in JSON store. Skipping.")
            continue

        title = get_title(card)
        duration = get_duration(card)
        print(f"Title: {title} [{duration}]")

        lyrics = get_lyrics(page, card)
        style = get_style(card)
        persona_name, persona_url = get_persona(card)
        print(f"Persona: {persona_name}")

        version = get_version(card)
        print(f"Version: {version}")

        # Use stable id in filenames to make overwriting safe and deterministic
        # Pattern: "<persona> - <title> - <id>" (persona may be empty)
        final_basename = f"{persona_name} - {title} - {id}" if persona_name else f"{title} - {id}"
        localFiles = []
        wav_filename = None
        btns = ["MP3 Audio", "WAV Audio"]
        if download_video:
            btns.append("Video")
        for btn in btns:
            if btn == "Video":
                try:
                    fn = download_song(page, card, download_dir, btn, final_basename)
                    localFiles.append(fn)
                except Exception as e:
                    print(f"Warning: failed to download Video for new song {id}: {e}")
                    continue
            else:
                fn = download_song(page, card, download_dir, btn, final_basename)
                localFiles.append(fn)

        entry = {
            "id": id,
            "title": title,
            "duration": duration,
            "version": version,
            "lyrics": lyrics,
            "style": style,
            "persona": persona_name,
            "personaUrl": persona_url,
            "songUrl": f"https://suno.com/song/{id}",
            "localFiles": localFiles,
        }
        upsert_song(songs_json, entry)
        save_songs(songs_json)
        print("Updated suno-songs.json with current song metadata.")

        page.wait_for_timeout(700)
        processed += 1

    return processed


def run(playwright: Playwright, download_video: bool = False, headless: bool = True, browser_name: str = "firefox") -> None:
    # Ensure metadata file exists (create empty array if missing)
    if not os.path.exists(JSON_FILE):
        save_songs([])

    # Select browser engine
    if browser_name not in ("firefox", "chromium", "webkit"):
        print(f"[WARN] Unknown browser '{browser_name}', defaulting to firefox")
        browser_name = "firefox"
    browser_type = getattr(playwright, browser_name)
    browser = browser_type.launch(headless=headless)

    context = browser.new_context(storage_state="context.json")
    page = context.new_page()

    print("Navigating to https://suno.com/me...")
    page.goto("https://suno.com/me")

    download_dir = "downloads"
    os.makedirs(download_dir, exist_ok=True)
    print(f"Saving files to: {download_dir}")

    print("Looking for songs on the page (to confirm it has loaded)...")
    song_cards = page.get_by_role("button", name=re.compile(r"^Play Song"))

    try:
        song_cards.first.wait_for(timeout=30000)
    except Exception as e:
        print(f"Couldn't find any songs. Is the page loaded? Error: {e}")
        context.close()
        browser.close()
        return

    print("Looking for the 'Close' popup (up to 10s)...")
    try:
        close_button = page.get_by_role("button", name="Close")
        close_button.wait_for(state="visible", timeout=10000)
        close_button.click()
        print("Popup closed.")
    except Exception:
        print("No 'Close' button found (timeout or not present). Continuing...")

    songs_json = load_songs()

    total_processed = 0
    page_index = 1

    # Process first page and then paginate
    while True:
        print(f"\n=== Page {page_index} ===")
        processed = _process_current_page(page, songs_json, download_dir, download_video=download_video, page_index=page_index)
        total_processed += processed
        print(f"Processed {processed} songs on page {page_index} (total so far: {total_processed}).")

        # Locate the Next button using the provided selector
        next_btn = page.locator("div:nth-child(2) > .flex.flex-col.overflow-y-hidden > .px-6 > .flex.flex-1.flex-col > div > .ml-4.flex > .flex.flex-row.items-center.gap-\\[5px\\] > button:nth-child(3)")

        try:
            # Give the UI a moment in case it needs to enable the button
            page.wait_for_timeout(300)
            if next_btn.count() == 0:
                print("Next button not found. Stopping pagination.")
                break
            # Check common disabling patterns
            aria_disabled = next_btn.get_attribute("aria-disabled")
            disabled_attr = next_btn.get_attribute("disabled")
            is_enabled = next_btn.is_enabled()
            is_visible = next_btn.is_visible()
            if (aria_disabled == "true") or (disabled_attr is not None) or (not is_enabled) or (not is_visible):
                print("Next button is not clickable (disabled or invisible). Stopping pagination.")
                break

            # Click Next and wait for the next page to load new cards
            with page.expect_response(lambda r: r.url.startswith("https://suno.com/api/") or r.request.method in ["GET", "POST"], timeout=10000):
                next_btn.click()

            # Wait for card list to refresh; simple settle delay
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"Failed to paginate to next page: {e}. Stopping.")
            break

        page_index += 1

    print(f"\nDone. Total songs processed this session: {total_processed}")
    context.close()
    browser.close()

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Suno songs (MP3/WAV by default). Optionally include videos.")
    parser.add_argument(
        "--videos",
        action="store_true",
        help="Also download video (.mp4) files. By default, videos are skipped.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run with a visible browser. Default is headless.",
    )
    parser.add_argument(
        "--browser",
        choices=["firefox", "chromium", "webkit"],
        default="firefox",
        help="Browser engine to use (default: firefox).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    with sync_playwright() as playwright:
        run(
            playwright,
            download_video=args.videos,
            headless=(not args.headed),
            browser_name=args.browser,
        )
