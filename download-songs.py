import sys
import re
import os
import json
from datetime import datetime
from typing import List, Dict, Optional
from playwright.sync_api import Playwright, sync_playwright, TimeoutError as PlaywrightTimeoutError
import argparse

JSON_FILE = "suno-songs.json"

class FatalError(Exception):
    """Exception raised for fatal errors that should not show a stacktrace."""
    pass

class SongCard:
    def __init__(self, element):
        self.element = element
        self.id: str = ""
        self.title: str = ""
        self.duration: str = ""
        self.version: str = ""
        self.persona_name: Optional[str] = None
        self.persona_url: Optional[str] = None
        self.style: str = ""
        self.lyrics: str = ""
        self.creation_date: Optional[str] = None
        self._extract()

    def _extract(self):
        # ID and Title from the first link
        song_link = self.element.locator("a[href^='/song/']").first
        try:
            href = song_link.get_attribute("href", timeout=2_000)
        except PlaywrightTimeoutError:
            raise FatalError("Could not find song ID href")

        if not href:
            raise FatalError("Could not find song ID href")
        self.id = href.split("/")[-1]
        self.title = song_link.inner_text(timeout=2_000).strip()

        # Duration
        duration_el = self.element.locator(".css-421ta7").first
        try:
            self.duration = duration_el.inner_text(timeout=2_000).strip()
        except PlaywrightTimeoutError:
            raise FatalError("Duration not found")

        self.version = "N/A"
        version_el = self.element.locator(".css-1o0qssg").first
        try:
            if version_el.count() > 0:
                self.version = version_el.inner_text(timeout=2_000).strip()
            else:
                # Fallback: try to find 'v5' or similar in the text if .css-1o0qssg is missing
                all_text = self.element.inner_text(timeout=2_000)
                lines = [l.strip() for l in all_text.split("\n") if l.strip()]
                for line in lines:
                    if re.match(r"^v\d+(\.\d+)?\+?$", line):
                        self.version = line
                        break
        except PlaywrightTimeoutError:
            pass

        # Voice, previously known as Persona
        persona_link = self.element.locator("a[href^='/voice/']").first
        try:
            if persona_link.count() > 0:
                self.persona_name = persona_link.inner_text(timeout=2_000).strip()
                self.persona_url = persona_link.get_attribute("href", timeout=2_000)
        except PlaywrightTimeoutError:
            pass

    def extract_from_panel(self, page):
        panel_selector = "div[data-panel='true']"
        
        print(f"Opening side panel for lyrics/style (ID: {self.id})...")
        self.element.click(timeout=2_000)
        
        try:
            page.wait_for_selector(panel_selector, timeout=2_000)
        except PlaywrightTimeoutError:
            raise FatalError(f"Side panel not found for song {self.id}")
            
        panel = page.locator(panel_selector).last
        
        show_more = panel.locator("button:has-text('Show More'), div[role='button']:has-text('Show More')").first
        try:
            if show_more.is_visible(timeout=2_000):
                print(f"Found Show More: {show_more.inner_text()}")
                show_more.scroll_into_view_if_needed(timeout=1_000)
                # Click center of the element
                show_more.click(timeout=2_000, force=True)
                print("Clicked Show More")
                page.wait_for_timeout(2_000)
        except Exception as e:
            raise FatalError(f"Show More click failed: {e}")

        show_less = panel.get_by_role("button", name="Show Less")
        if show_less.is_visible(timeout=10_000):
             print("Panel expanded (Show Less visible)")
        else:
             raise FatalError("'Show Less' button not visible, panel not expanded")

        page.screenshot(path="creation_date_hover.png")

        try:
            self.style = (page.locator("div[data-panel='true'] button[aria-label='Copy styles to clipboard']")
                          .locator("xpath=preceding-sibling::div[1]")
                          .inner_text(timeout=2_000))
        except PlaywrightTimeoutError:
            raise FatalError(f"Style not found in panel for song {self.id}")

        lyrics_copy_button = panel.locator("button[aria-label='Copy lyrics to clipboard']")
        try:
            self.lyrics = lyrics_copy_button.locator("xpath=preceding-sibling::div[1]").inner_text(timeout=2_000)
        except PlaywrightTimeoutError:
            print(f"Warning: lyrics not found in panel for song {self.id}, setting as instrumental")
            self.lyrics = "N/A (Instrumental or lyrics not provided)"
            
            print("Warning: set creation date manually as we don't support fetching it yet")
            return

        # Creation date from timestamp hover
        ago_el = lyrics_copy_button.locator("xpath=../following-sibling::div[1]")
        ago_el.hover(timeout=3000)
        
        tooltip = page.locator(
            "div[data-base-ui-focusable].rounded-lg"
        ).last
        
        raw_date = tooltip.inner_text().strip()
        self.creation_date = self._parse_creation_date(raw_date)

    @staticmethod
    def _parse_creation_date(raw_date: str) -> str:
        # We want ISO format: "2026-01-06T16:39:00"
        # Possible formats:
        # "January 6, 2026 at 4:39 PM"
        # "3 February 2026 at 00:29"
        try:
            # Clean up the string
            clean_date = re.sub(r'\s+', ' ', raw_date).strip()
            
            formats = [
                "%B %d, %Y at %I:%M %p",
                "%d %B %Y at %H:%M"
            ]
            
            for fmt in formats:
                try:
                    dt = datetime.strptime(clean_date, fmt)
                    return dt.isoformat()
                except ValueError:
                    continue
            
            raise ValueError(f"No matching format for '{clean_date}'")

        except Exception as e:
            raise FatalError(f"Failed to parse date '{raw_date}': {e}")

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "duration": self.duration,
            "version": self.version,
            "lyrics": self.lyrics,
            "style": self.style,
            "persona": self.persona_name,
            "personaUrl": self.persona_url,
            "creationDate": self.creation_date,
            "songUrl": f"https://suno.com/song/{self.id}",
        }

    def __repr__(self):
        return f"SongCard(id={self.id}, title={self.title})"

def load_songs() -> List[Dict]:
    if not os.path.exists(JSON_FILE):
        return []
    with open(JSON_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        if isinstance(data, list):
            return data
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

def _sanitize_filename(name: str) -> str:
    # Remove or replace characters not allowed on common file systems
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

def download_mp3_file(page, card_element, final_basename: str) -> str | None:
    # noinspection PyBroadException
    # open menu
    card_element.get_by_label("More options").click()

    download_btn = page.get_by_role("button", name="Download")
    download_btn.click()
    page.wait_for_timeout(1_000)

    page.screenshot(path="01_menu_open.png")
    with page.expect_download(timeout=30_000) as download_info:
        page.get_by_role("button", name="MP3 Audio").click()

    download = download_info.value
    filename = final_basename + ".mp3"
    download.save_as(os.path.join("downloads", _sanitize_filename(filename)))
    return filename

    raise FatalError(f"Failed to download MP3 for {final_basename}: {e}")
    return None

def download_wav_file(page, card_element, final_basename: str):
    # open menu
    card_element.get_by_label("More options").click()

    download_btn = page.get_by_role("button", name="Download")
    download_btn.click()

    wav_btn = page.get_by_role("button", name="WAV Audio")
    wav_btn.wait_for(state="visible", timeout=2_000)
    wav_btn.click()

    page.wait_for_timeout(1_000)
    # Pro is needed to download wav
    if page.locator("text=Studio").count() > 0:
        print("WAV download requires Studio subscription. Skipping WAV for this song.")
        
        # dismiss popup by clicking outside
        page.mouse.click(10, 10)
        page.wait_for_timeout(200)
        return None 
    
    with page.expect_download(timeout=60_000) as download_info:
        dl_btn = page.get_by_role("button", name="Download File")
        # Ensure it's not just visible but also enabled (Suno takes time to generate)
        dl_btn.wait_for(state="visible", timeout=30_000)
        # Playwright .click() should wait for it to be enabled, but we increase timeout
        dl_btn.click(timeout=30_000)

    download = download_info.value
    filename = final_basename + ".wav"

    download.save_as(os.path.join("downloads", _sanitize_filename(filename)))
    return filename

def download_video_file(page, card_element, final_basename: str) -> str | None:
    # noinspection PyBroadException
    try:
        card_element.get_by_label("More options").click(timeout=2_000)
        page.get_by_role("button", name="Download").hover(timeout=1_000)

        with page.expect_download(timeout=30_000) as download_info:
            page.get_by_role("button", name="Video").click()
            dl_btn = page.get_by_role("button", name="Download File")
            dl_btn.wait_for(state="visible", timeout=10_000)
            dl_btn.click(timeout=10_000)
        
        download = download_info.value
        filename = final_basename + ".mp4"
        download.save_as(os.path.join("downloads", _sanitize_filename(filename)))
        return filename
    except Exception as e:
        print(f"Failed to download video, try again later.")
        return None

def _process_single_card(page, card: SongCard, songs_json: List[Dict], download_dir: str, do_download_video: bool = False, do_download_mp3: bool = True, processed_ids: Optional[set] = None) -> bool:
    song_id = card.id
    title = card.title
    
    if not song_id:
        raise FatalError("ID not found")
    if not title:
        raise FatalError("Title not found")

    # Session uniqueness check (ID based)
    if processed_ids is not None:
        if song_id in processed_ids:
            return False
        processed_ids.add(song_id)
        
    existing = find_by_id(songs_json, song_id)
    if existing:
        if do_download_video:
            local_files = existing.get("localFiles", [])
            has_mp4 = any(str(local_file).lower().endswith(".mp4") for local_file in local_files)
            if not has_mp4:
                print(f"Song {song_id} ({title}) exists but missing video. Attempting video download...")
                video_file = download_video_file(page, card.element, f"{existing.get('persona', '')} - {title} - {song_id}")
                if video_file:
                    local_files.append(video_file)
                    existing["localFiles"] = local_files
                    upsert_song(songs_json, existing)
                    save_songs(songs_json)

        if "creationDate" not in existing:
            card.extract_from_panel(page)
            print(f"Setting missing Creation Date: {card.creation_date}")
            existing["creationDate"] = card.creation_date
            upsert_song(songs_json, existing)
            save_songs(songs_json)
            return True
        else:
            print(f"--- Skipping existing song (ID: {song_id}) ---")
            return True

    print(f"--- Processing NEW song (ID: {song_id} - {title}) ---")
    card.extract_from_panel(page)

    print(f"Title: {card.title} [{card.duration}]")
    print(f"Persona: {card.persona_name}")
    print(f"Version: {card.version}")
    print(f"Creation Date: {card.creation_date}")
    print(f"Style: {card.style}")
    print(f"Lyrics: {card.lyrics}")

    # Use stable id in filenames to make overwriting safe and deterministic
    final_basename = f"{card.persona_name} - {card.title} - {song_id}" if card.persona_name else f"{card.title} - {song_id}"
    local_files_list = []

    if any(s.endswith(final_basename + ".wav") for s in local_files_list):
        print(f"Song {song_id} already has wav file, skipping download.")
    else:
        wav_file = download_wav_file(page, card.element, final_basename)
        if wav_file:
            local_files_list.append(wav_file)

    if any(s.endswith(final_basename + ".mp3") for s in local_files_list):
        print(f"Song {song_id} already has mp3 file, skipping download.")
    else:
        if do_download_mp3:
            mp3_file = download_mp3_file(page, card.element, final_basename)
            if mp3_file:
                local_files_list.append(mp3_file)
    
    if any(s.endswith(final_basename + ".mp4") for s in local_files_list):
        print(f"Song {song_id} already has mp4 file, skipping download.")
    else:
        if do_download_video:
            video_file = download_video_file(page, card.element, final_basename)
            if video_file:
                local_files_list.append(video_file)

    entry = card.to_dict()
    entry["localFiles"] = local_files_list
    
    upsert_song(songs_json, entry)
    save_songs(songs_json)
    print("Updated suno-songs.json with current song metadata.")

    page.wait_for_timeout(700)
    return True


def run(pw: Playwright, do_download_video: bool = False, headless: bool = True, browser_name: str = "firefox", do_download_mp3: bool = True) -> None:
    if not os.path.exists(JSON_FILE):
        save_songs([])

    if browser_name not in ("firefox", "chromium", "webkit"):
        print(f"[WARN] Unknown browser '{browser_name}', defaulting to firefox")
        browser_name = "firefox"
    browser_type = getattr(pw, browser_name)
    browser = browser_type.launch(headless=headless, args=["--window-size=1920,1080"])

    context = browser.new_context(storage_state="context.json", viewport={"width": 1920, "height": 1080})
    page = context.new_page()

    print("Navigating to https://suno.com/me...")
    page.goto("https://suno.com/me", timeout=10_000)

    download_dir = "downloads"
    os.makedirs(download_dir, exist_ok=True)
    print(f"Saving files to: {download_dir}")

    print("Waiting for page content to load...")
    try:
        page.get_by_text("Library", exact=False).wait_for(timeout=10_000)
    except Exception as exc:
        print(f"FATAL ERROR: Library header not found: {exc}")
        context.close()
        browser.close()
        sys.exit(1)

    songs_json = load_songs()
    processed_ids = set()
    total_processed = 0

    while True:
        try:
            page.get_by_role("rowgroup").wait_for(timeout=5_000)
        except PlaywrightTimeoutError:
            print("No rowgroup found.")
            break

        rowgroup = page.get_by_role("rowgroup")
        children_count = rowgroup.evaluate("el => el.children.length")
        
        if children_count == 0:
            print("No song cards found.")
            break

        for i in range(children_count):
            child_locator = rowgroup.locator(f"xpath=./*[{i+1}]")
            child_locator.scroll_into_view_if_needed(timeout=1_000)
            card = SongCard(child_locator)
            if _process_single_card(page, card, songs_json, download_dir, do_download_video=do_download_video, do_download_mp3=do_download_mp3, processed_ids=processed_ids):
                total_processed += 1

        # Scroll down
        page.keyboard.press("End")
        page.wait_for_timeout(2_000)

        # Check for bottom of page
        bottom_selector = "div.css-fu18b6.e1vgipf84"
        if page.locator(bottom_selector).count() > 0:
            print("Reached the bottom of the page.")
            break

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
    parser.add_argument(
        "--nomp3",
        action="store_true",
        help="Disable downloading MP3 files (download only WAV and optionally Video)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        with sync_playwright() as pw_instance:
            run(
                pw_instance,
                do_download_video=args.videos,
                headless=(not args.headed),
                browser_name=args.browser,
                do_download_mp3=(not args.nomp3),
            )
    except FatalError as ferr:
        print(f"FATAL ERROR: {ferr}")
        sys.exit(1)
