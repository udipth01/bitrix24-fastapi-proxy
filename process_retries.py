# process_retries.py
from helpers.retry_manager import process_due_retries
import time

if __name__ == "__main__":
    print("🔁 Retry worker started")

    # You can run continuously (recommended)
    while True:
        print("⏳ Checking retry queue...")
        results = process_due_retries()
        print("📌 Results:", results)
        time.sleep(60)   # check every 1 minute
