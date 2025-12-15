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

    sender_string = "Finideas Investment Advisor Pvt. Ltd. <updates@finideas.com>"
    payload = {
    "fields": {
        "OWNER_ID": lead_id,
        "OWNER_TYPE_ID": 1,           # 1 = Lead
        "TYPE_ID": 4,                 # Activity type = Email
        "PROVIDER_ID": "email",       # REQUIRED
        "PROVIDER_TYPE_ID": "EMAIL",  # REQUIRED
        "SUBJECT": subject,
        "DESCRIPTION": body,
        "DESCRIPTION_TYPE": 2,        # HTML
        "IS_INCOMING": "N",
        "COMPLETED": "Y",
         "DIRECTION": 2,
      "COMMUNICATIONS": [
        {
          "ENTITY_ID": lead_id,
          "ENTITY_TYPE_ID": 1,
          "TYPE": "EMAIL",
          "VALUE": lead_email,
          "FROM": sender_string
        }
      ],



        # REQUIRED for Bitrix to actually SEND the email
        "BINDINGS": [
            {
                "OWNER_ID": lead_id,
                "OWNER_TYPE_ID": 1
            }
        ],


        # REQUIRED: Who is sending the email
        "AUTHOR_ID": 1,
        "RESPONSIBLE_ID": 1,


        # MUST MATCH your mailbox name in Bitrix
        "SETTINGS": {
        "MESSAGE_FROM": sender_string,
            "MESSAGE_TO": lead_email
        }
    }
}



    url = f"{BITRIX_WEBHOOK}crm.activity.add.json"

    print("📧 Sending retry email:", payload)

    res = requests.post(url, json=payload)
    print("📩 Bitrix email response:", res.text)

    return res.json()
