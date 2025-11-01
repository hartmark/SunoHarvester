import time
from playwright.sync_api import sync_playwright
import os

def run():
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=False)
        context = browser.new_context()

        page = context.new_page()
        page.goto('https://suno.com')
        
        print("\n-------------------------------------------------")
        print("A Firefox window has been opened.")
        print("Make sure you're logged into suno.com.")
        print("-------------------------------------------------")
        
        input("\nPress any key to SAVE the context into context.json...")
        context.storage_state(path="context.json")
        context.close()
        browser.close()

if __name__ == "__main__":
    run()
