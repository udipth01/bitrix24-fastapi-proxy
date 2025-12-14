import requests
from config import BITRIX_WEBHOOK

def send_manual_retry_email(lead_id, lead_name, lead_phone, lead_email):
    """
    Sends an email to lead via Bitrix REST API without changing the lead stage.
    """

    if not lead_email:
        print(f"⚠️ Lead {lead_id} has no email, skipping email send.")
        return {"status": "no_email"}

    # Prepare email content
    subject = "Unable to Connect – Finideas"
    
    body = f"""Dear {lead_name}ji,<br><br>

    Greetings from Finideas!<br><br>

    We’ve been trying to connect with you regarding your interest in our 
    <b>Index Long-Term Strategy</b> investment but were unable to reach you on 
    <b>{lead_phone}</b>.<br><br>

    Kindly let us know a convenient time for a quick call, 
    or feel free to share an alternate contact number.<br><br>

    You can also reach us directly on WhatsApp:<br>
    ➡️ <a href="https://wa.me/7434985600">https://wa.me/7434985600</a><br><br>

    To better understand our strategy and its long-term benefits, 
    we invite you to watch this short video:<br>
    ➡️ <b><a href="https://youtu.be/example">Investment Overview – Click to Watch</a></b><br><br>

    We look forward to assisting you in your investment journey.<br><br>

    Best regards,<br>
    Team Finideas
    """


    payload = {
        "fields": {
            "OWNER_ID": lead_id,
            "OWNER_TYPE_ID": 1,            # Lead
            "TYPE_ID": 4,                  # EMAIL activity
            "SUBJECT": subject,
            "DESCRIPTION": body,
            "DESCRIPTION_TYPE": 2,         # 2 = HTML

            # Required for EMAIL activity
            "COMMUNICATIONS": [
                {
                    "VALUE": lead_email,
                    "ENTITY_ID": lead_id,
                    "ENTITY_TYPE_ID": 1,
                    "TYPE": "EMAIL"
                }
            ],

            # Email settings
            "SETTINGS": {
                "MESSAGE_FROM": "updates@finideas.com",
                "MESSAGE_TO": lead_email
            }
        }
    }


    url = f"{BITRIX_WEBHOOK}crm.activity.add.json"

    print("📧 Sending retry email:", payload)

    res = requests.post(url, json=payload)
    print("📩 Bitrix email response:", res.text)

    return res.json()
