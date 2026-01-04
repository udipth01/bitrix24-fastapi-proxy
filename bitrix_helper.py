import requests
import os

# Replace with your webhook URL (no method at the end, just base)
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK")

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

#https://finideas.bitrix24.in/rest/24/hdyr7b0qithutnpx/crm.status.list.json?filter[ENTITY_ID]=STATUS
#{"result":[{"ID":"1","ENTITY_ID":"STATUS","STATUS_ID":"NEW","NAME":"Unassigned","NAME_INIT":"Unassigned","SORT":"10","SYSTEM":"Y","CATEGORY_ID":null,"COLOR":"#2FC6F6","SEMANTICS":null,"EXTRA":{"SEMANTICS":"process","COLOR":"#2FC6F6"}},{"ID":"3","ENTITY_ID":"STATUS","STATUS_ID":"IN_PROCESS","NAME":"Generated","NAME_INIT":"","SORT":"20","SYSTEM":"N","CATEGORY_ID":null,"COLOR":"#FFA801","SEMANTICS":null,"EXTRA":{"SEMANTICS":"process","COLOR":"#FFA801"}},{"ID":"298","ENTITY_ID":"STATUS","STATUS_ID":"14","NAME":"Unanswered","NAME_INIT":"","SORT":"30","SYSTEM":"N","CATEGORY_ID":"0","COLOR":"#FFDD99","SEMANTICS":"","EXTRA":{"SEMANTICS":"process","COLOR":"#FFDD99"}},{"ID":"267","ENTITY_ID":"STATUS","STATUS_ID":"11","NAME":"A1","NAME_INIT":"","SORT":"40","SYSTEM":"N","CATEGORY_ID":"0","COLOR":"#2FC6F6","SEMANTICS":"","EXTRA":{"SEMANTICS":"process","COLOR":"#2FC6F6"}},{"ID":"5","ENTITY_ID":"STATUS","STATUS_ID":"PROCESSED","NAME":"Active Inquiry","NAME_INIT":"","SORT":"50","SYSTEM":"N","CATEGORY_ID":null,"COLOR":"#A6DC00","SEMANTICS":null,"EXTRA":{"SEMANTICS":"process","COLOR":"#A6DC00"}},{"ID":"166","ENTITY_ID":"STATUS","STATUS_ID":"1","NAME":"M 1","NAME_INIT":"","SORT":"60","SYSTEM":"N","CATEGORY_ID":null,"COLOR":"#C2C2C2","SEMANTICS":null,"EXTRA":{"SEMANTICS":"process","COLOR":"#C2C2C2"}},{"ID":"168","ENTITY_ID":"STATUS","STATUS_ID":"2","NAME":"M 2","NAME_INIT":"","SORT":"70","SYSTEM":"N","CATEGORY_ID":null,"COLOR":"#C2C2C2","SEMANTICS":null,"EXTRA":{"SEMANTICS":"process","COLOR":"#C2C2C2"}},{"ID":"7","ENTITY_ID":"STATUS","STATUS_ID":"CONVERTED","NAME":"Good lead","NAME_INIT":"Good lead","SORT":"80","SYSTEM":"Y","CATEGORY_ID":null,"COLOR":"#7BD500","SEMANTICS":"S","EXTRA":{"SEMANTICS":"success","COLOR":"#7BD500"}},{"ID":"9","ENTITY_ID":"STATUS","STATUS_ID":"JUNK","NAME":"Junk Lead","NAME_INIT":"Junk Lead","SORT":"90","SYSTEM":"Y","CATEGORY_ID":null,"COLOR":"#FF5752","SEMANTICS":"F","EXTRA":{"SEMANTICS":"failure","COLOR":"#FF5752"}}],"total":9,"time":{"start":1764141594,"finish":1764141594.12751,"duration":0.12751007080078125,"processing":0,"date_start":"2025-11-26T10:19:54+03:00","date_finish":"2025-11-26T10:19:54+03:00","operating_reset_at":1764142194,"operating":0}}

#https://finideas.bitrix24.in/rest/24/hdyr7b0qithutnpx/crm.lead.fields.json
#https://finideas.bitrix24.in/rest/24/hdyr7b0qithutnpx/crm.status.list.json
#curl -X GET "https://finideas.bitrix24.in/rest/24/hdyr7b0qithutnpx/crm.lead.get.json?id=577572"
