import os
from fastapi import Request
import requests
from config import BITRIX_WEBHOOK

def get_deal_stage_semantics(deal_id):
    url = f"{BITRIX_WEBHOOK}crm.deal.get.json"
    response = requests.get(url, params={"id": deal_id})
    result = response.json().get("result", {})

    stage_id = result.get("STAGE_ID")
    category_id = result.get("CATEGORY_ID")  # if you use multiple pipelines

    if not stage_id:
        return None

    # Fetch stage details
    payload = {
        "filter": {
            "ENTITY_ID": f"DEAL_STAGE_{category_id}" if category_id else "DEAL_STAGE",
            "STATUS_ID": stage_id
        }
    }
    res = requests.post(
        f"{BITRIX_WEBHOOK}crm.status.list.json",
        json=payload
    )

    stage_list = res.json().get("result", [])

    if stage_list:
        return stage_list[0].get("SEMANTICS")  # process / success / failure
    
    return None






def find_deal_for_lead(lead_id):
    url = f"{BITRIX_WEBHOOK}crm.deal.list.json"

    payload = {
        "filter": {"LEAD_ID": lead_id},
        "select": ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "OPPORTUNITY"],
        "order": {"ID": "DESC"}   # get newest deal
    }

    res = requests.post(url, json=payload)
    deals = res.json().get("result", [])

    if deals:
        return deals[0]["ID"]   # newest deal

    return None
