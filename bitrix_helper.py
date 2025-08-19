import requests

# Replace with your webhook URL (no method at the end, just base)
BITRIX_WEBHOOK_URL = "https://finideas.bitrix24.in/rest/24/hdyr7b0qithutnpx/"

def get_lead_statuses():
    """Fetch and print all lead statuses from Bitrix24."""
    url = BITRIX_WEBHOOK_URL + "crm.status.list.json"
    response = requests.get(url)

    if response.status_code != 200:
        print("Error fetching data:", response.text)
        return None

    data = response.json()
    if "result" not in data:
        print("Unexpected response:", data)
        return None

    statuses = data["result"]
    print("Lead Statuses in Bitrix:")
    for status in statuses:
        if status["ENTITY_ID"] == "STATUS":  # only lead statuses
            print(f"{status['STATUS_ID']} - {status['NAME']}")

    return statuses

if __name__ == "__main__":
    get_lead_statuses()
