# process_retries.py
from helpers.retry_manager import   process_call_now_leads,process_due_retries
import time

if __name__ == "__main__":
    print("🔁 Retry worker started")

    # You can run continuously (recommended)
    while True:
        
        print("📞 Checking Call Now leads")
        process_call_now_leads(limit=50)
        
        print("⏳ Checking retry queue...")
        results = process_due_retries()
        
        print("📌 Results:", results)
        time.sleep(60)   # check every 1 minute
