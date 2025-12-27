#post_call_webhook.py
import os
import json
from fastapi import APIRouter,Request
router = APIRouter()
import requests
from helpers.parsing_utils import parse_custom_extractions,parse_budget_to_number
from helpers.time_utils import parse_rm_meeting_time,compute_busy_call_datetime
from config import BITRIX_WEBHOOK, BOLNA_TOKEN, supabase
from datetime import datetime, timedelta
from helpers.deal_utils import find_deal_for_lead,get_deal_stage_semantics
from helpers.retry_manager import (
    insert_or_increment_retry,
    cancel_retry_for_lead,
    mark_retry_attempt,
    apply_busy_call_override
)
from helpers.email_sender import send_manual_retry_email
from dateutil.parser import isoparse


# ---------- Post-call webhook (Bolna → Supabase + Bitrix lead + deal+activity) ----------

@router.post("/post-call-webhook")
async def post_call_webhook(request: Request):
    """Receives post-call status from Bolna.ai and updates Supabase + Bitrix"""
    data = await request.json()
    print("📥 Post-call webhook received:", data)

    # Extract lead info safely
    context = data.get("context_details") or {}
    recipient_data = context.get("recipient_data") or {}
    lead_id = recipient_data.get("lead_id")
    lead_name = recipient_data.get("lead_name")
    recipient_phone = context.get("recipient_phone_number")

    # Extracted tags
    extracted_data = data.get("extracted_data", {}) or {}
    user_name = extracted_data.get("user_name", "Unknown")
    interested = extracted_data.get("interested", "NA")

    # ---- Custom Extractions ----
    custom_extractions_raw = data.get("custom_extractions")
    ce = parse_custom_extractions(custom_extractions_raw)
    rm_meeting_time_raw = ce.get("RM_meeting_time")
    webinar_attended = ce.get("Webinar_attended") or ce.get("webinar_attended")
    investment_budget_raw = (
        ce.get("Investment_amount")      # numeric value like "5500000"
        or ce.get("Investment_Budget")
        or ce.get("investment_budget")
        or ce.get("Investment_Category")  # fallback description "over 10 Lakh"
    )
    busy_call_next = ce.get("busy_call_next")


    # ============================================================
    # 🔥 New Custom Extraction Logic: Lead Hotness + Availability
    # ============================================================

    lead_hotness = ce.get("Lead_hotness", "").strip().upper()
    user_availability = ce.get("user_availability", "").strip().lower()

    webinar_attended_norm = (webinar_attended or "").strip().lower()

    # Parse budget to numeric INR
    investment_budget_value = parse_budget_to_number(investment_budget_raw)

    print(
        f"🔎 Parsed custom_extractions → "
        f"RM_meeting_time={rm_meeting_time_raw}, "
        f"Webinar_attended={webinar_attended}, "
        f"Investment_Budget={investment_budget_raw} "
        f"(parsed={investment_budget_value})",
        f"Lead Hotness = {lead_hotness}", 
        f"User Availability = {user_availability}", 
        f"busy call next = {busy_call_next}"
    )

    # Call metadata
    call_summary = data.get("summary", "")
    transcript = data.get("transcript", "")
    status = data.get("status")
    conversation_duration = data.get("conversation_duration")
    total_cost = data.get("total_cost")

    # Telephony details
    telephony_data = data.get("telephony_data", {}) or {}
    to_number = telephony_data.get("to_number")
    from_number = telephony_data.get("from_number")
    recording_url = telephony_data.get("recording_url")
    provider_call_id = telephony_data.get("provider_call_id")
    call_type = telephony_data.get("call_type")
    telephony_provider = telephony_data.get("provider")

    # -------------- Call Status --------------
    status = data.get("status")
    call_summary = data.get("summary", "")
    transcript = data.get("transcript", "")

    bolna_id = data.get("id")

    br = requests.get(f"{BITRIX_WEBHOOK}crm.lead.get.json", params={"id": lead_id}, timeout=10)
    lead_data = br.json().get("result", {})

    first_name = lead_data.get("NAME")

    lead_email = None
    emails = lead_data.get("EMAIL") or []

    for item in emails:
        if item.get("VALUE"):
            lead_email = item["VALUE"]
            break

    # ============================================================
    # 🚫 HARD STOP: LEAD HOTNESS = JUNK → Kill Lead + Deal
    # ============================================================

    if lead_hotness == "JUNK" or user_availability == "junk" :
        print(f"🗑️ Lead hotness = JUNK → moving lead & deal to JUNK")

        # --------------------------------------------------------
        # 1. Move LEAD to JUNK
        # --------------------------------------------------------
        requests.post(
            f"{BITRIX_WEBHOOK}crm.lead.update.json",
            json={
                "id": lead_id,
                "fields": {
                    "STATUS_ID": "JUNK",
                    "COMMENTS": "AI classified lead hotness as JUNK"
                }
            }
        )

        # --------------------------------------------------------
        # 2. Find linked DEAL (if exists)
        # --------------------------------------------------------
        deal_id = find_deal_for_lead(lead_id)
        print("🔎 Linked deal for junk lead:", deal_id)

        # --------------------------------------------------------
        # 3. Move DEAL to LOST / JUNK stage
        # --------------------------------------------------------
        if deal_id:
            DEAL_JUNK_STAGE_ID = "LOSE"  # 🔴 CHANGE if needed

            requests.post(
                f"{BITRIX_WEBHOOK}crm.deal.update.json",
                json={
                    "id": deal_id,
                    "fields": {
                        "STAGE_ID": DEAL_JUNK_STAGE_ID
                    }
                }
            )

            # Optional: add timeline comment
            requests.post(
                f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                json={
                    "fields": {
                        "ENTITY_ID": deal_id,
                        "ENTITY_TYPE": "deal",
                        "COMMENT": "🗑️ Deal marked as LOST — AI classified lead as JUNK"
                    }
                }
            )

        # --------------------------------------------------------
        # 4. Cancel retries & future calls
        # --------------------------------------------------------
        cancel_retry_for_lead(lead_id, reason="lead_hotness_junk")

        return {
            "status": "junk_lead_and_deal_closed",
            "lead_id": lead_id,
            "deal_id": deal_id
        }



 
    # --- CASE 2: user_availability = busy → treat like failure state ---
    if user_availability == "busy" or user_availability == "not_interpretable":
        print(f"📵 User busy → scheduling retry for lead {lead_id}")

        insert_or_increment_retry(
            lead_id=lead_id,
            phone=recipient_phone or to_number,
            lead_name=lead_name,
            lead_first_name=first_name,
            reason="busy"
        )
        busy_raw = busy_call_next

        if isinstance(busy_raw, str):
            try:
                busy_call_next = json.loads(busy_raw)
            except Exception:
                print("❌ Invalid busy_call_next JSON:", busy_raw)
                busy_call_next = None

        busy_dt = compute_busy_call_datetime(busy_call_next)

        if busy_dt:
            applied = apply_busy_call_override(
                lead_id=lead_id,
                busy_call_next=busy_dt.isoformat()
            )

            if applied:
                requests.post(
                    f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                    json={
                        "fields": {
                            "ENTITY_ID": lead_id,
                            "ENTITY_TYPE": "lead",
                            "COMMENT": f"⏰ User requested callback at {busy_dt.strftime('%d %b %Y %I:%M %p IST')}"
                        }
                    }
                )

                return {"status": "busy_override_scheduled"}

    # 🔁 CASE B: no explicit time → normal retry flow

        mark_retry_attempt(lead_id, bolna_call_id=bolna_id, status="busy")

        # Log on timeline
        requests.post(
            f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
            json={
                "fields": {
                    "ENTITY_ID": lead_id,
                    "ENTITY_TYPE": "lead",
                    "COMMENT": "📵 User said they are BUSY — retry scheduled automatically"
                }
            }
        )

        return {"status": "retry_scheduled_busy"}


    # ==============================================================================
    # 🔥🔥🔥 1. HANDLE FAILED CALLS (busy / failed / no-answer / not-reachable)
    # ==============================================================================
    FAILURE_STATES = ["busy", "failed", "no_answer", "no-answer", "not_reachable"]

    if status in FAILURE_STATES :
        print(f"⚠️ Call failed ({status}) → scheduling retry for lead {lead_id}")

        # Create or increment retry
        insert_or_increment_retry(
            lead_id=lead_id,
            phone=recipient_phone or to_number,
            lead_name=lead_name,
            lead_first_name=first_name,
            reason=status,
        )

        # Store bolna call ID
        mark_retry_attempt(
            lead_id=lead_id,
            bolna_call_id=bolna_id,
            status=status
        )

        # Add comment on Bitrix lead
        requests.post(
            f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
            json={
                "fields": {
                    "ENTITY_ID": lead_id,
                    "ENTITY_TYPE": "lead",
                    "COMMENT": f"❌ Auto-call failed with status: {status}. Retry has been scheduled."
                }
            }
        )

        send_manual_retry_email(lead_id=lead_id,lead_name=first_name,lead_phone=recipient_phone,lead_email=lead_email)

        # END — Do **NOT** continue with ILTS logic
        return {"status": "retry_scheduled"}


    if status == "completed":
        # ✅ Save in Supabase (bolna_call_logs table)
        cancel_retry_for_lead(lead_id, reason="call_completed")

        print(f"✅ Completed call — retry entry cleared for lead {lead_id}")
        try:
            payload = {
                "bolna_id": data.get("id"),
                "agent_id": data.get("agent_id"),
                "batch_id": data.get("batch_id"),
                "campaign_id": data.get("campaign_id"),

                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "scheduled_at": data.get("scheduled_at"),
                "rescheduled_at": data.get("rescheduled_at"),

                "status": status,
                "answered_by_voice_mail": data.get("answered_by_voice_mail"),
                "conversation_duration": conversation_duration,
                "total_cost": total_cost,
                "transcript": transcript,
                "summary": call_summary,
                "error_message": data.get("error_message"),

                # extracted tags
                "user_name": user_name,
                "interested": interested,

                # custom_extractions
                "rm_meeting_time": rm_meeting_time_raw,
                "webinar_attended": webinar_attended,
                "investment_budget_raw": investment_budget_raw,
                "investment_budget_value": investment_budget_value,

                # telephony data
                "telephony_duration": telephony_data.get("duration"),
                "to_number": to_number,
                "from_number": from_number,
                "recording_url": recording_url,
                "hosted_telephony": telephony_data.get("hosted_telephony"),
                "provider_call_id": provider_call_id,
                "call_type": call_type,
                "telephony_provider": telephony_provider,
                "hangup_by": telephony_data.get("hangup_by"),
                "hangup_reason": telephony_data.get("hangup_reason"),
                "hangup_provider_code": telephony_data.get("hangup_provider_code"),

                # lead mapping
                "lead_id": lead_id,
                "lead_name": lead_name,
                "recipient_phone_number": recipient_phone,

                # breakdowns
                "usage_breakdown": data.get("usage_breakdown"),
                "cost_breakdown": data.get("cost_breakdown"),

                # metadata
                "provider": data.get("provider"),
                "raw_payload": data,
            }

            res = supabase.table("bolna_call_logs").insert(payload).execute()
            print("✅ Supabase insert success:", res)
        except Exception as e:
            print("❌ Supabase insert error:", str(e))

        # ✅ Update Bitrix comments log on LEAD
        if lead_id:
            get_res = requests.get(
                f"{BITRIX_WEBHOOK}crm.lead.get.json",
                params={"id": lead_id},
                timeout=10
            )
            lead_data = get_res.json().get("result", {})
            existing_comments = lead_data.get("COMMENTS") or ""

            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            new_entry = f"<p><b>Post-call Update ({timestamp}):</b></p>"
            new_entry += f"<p>Transcript: {transcript}</p>"
            new_entry += f"<p>Interest: {interested}</p>"

            if webinar_attended is not None:
                new_entry += f"<p>Webinar attended: {webinar_attended}</p>"
            if investment_budget_raw:
                new_entry += f"<p>Investment Budget: {investment_budget_raw}</p>"

            # 🔗 Wrap recording link in HTML so Bitrix doesn't truncate
            if recording_url:
                new_entry += (
                    f'<p>Recording: <a href="{recording_url}" target="_blank">'
                    f"{recording_url}</a> </p>"
                )

            if call_summary:
                new_entry += f"<p>Summary: {call_summary}</p>"

            updated_comments = existing_comments + new_entry

            # Base fields for lead update
            update_fields = {
                "COMMENTS": updated_comments,
                "UF_CRM_1586952775435": "136"   # ⭐ Required for deal creation
            }

            # Find deal created by automation
            deal_id = find_deal_for_lead(lead_id)
            print("Deal_id:", deal_id)

            semantics = get_deal_stage_semantics(deal_id)
            print("Deal_id:", deal_id)

            # ------------------------------------------------------------
            # CASE A: DEAL ALREADY EXISTS (UPDATE DEAL ONLY)
            # ------------------------------------------------------------
            if deal_id:
                print("♻️ Existing deal found → updating DEAL (not lead). Deal_ID:", deal_id)

                # --- 1. Add Transcript ---
                requests.post(
                    f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                    json={
                        "fields": {
                            "ENTITY_ID": deal_id,
                            "ENTITY_TYPE": "deal",
                            "COMMENT": f"<b>Transcript</b><br>{transcript}"
                        }
                    }
                )

                # --- 2. Add Summary ---
                if call_summary:
                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                        json={
                            "fields": {
                                "ENTITY_ID": deal_id,
                                "ENTITY_TYPE": "deal",
                                "COMMENT": f"<b>Summary</b><br>{call_summary}"
                            }
                        }
                    )

                # --- 3. Add Recording ---
                if recording_url:
                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                        json={
                            "fields": {
                                "ENTITY_ID": deal_id,
                                "ENTITY_TYPE": "deal",
                                "COMMENT": (
                                    f"<b>Call Recording</b><br>"
                                    f'<a href="{recording_url}" target="_blank">Click</a>'
                                )
                            }
                        }
                    )

                # --- 4. Update opportunity ---
                if investment_budget_value:
                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.deal.update.json",
                        json={
                            "id": deal_id,
                            "fields": {
                                "OPPORTUNITY": investment_budget_value,
                                "CURRENCY_ID": "INR",
                                "IS_MANUAL_OPPORTUNITY": "Y"
                            }
                        }
                    )

                # ============================================================
                # 🔥 Lead Hotness → Move Deal Stage
                # ============================================================

                if lead_hotness in ("COLD", "WARM", "HOT"):
                    stage_map = {
                        "COLD": "4",
                        "WARM": "6",
                        "HOT": "8"
                    }

                    new_stage = stage_map.get(lead_hotness)

                    print(f"🔥 Updating deal {deal_id} to stage {new_stage} based on hotness = {lead_hotness}")

                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.deal.update.json",
                        json={
                            "id": deal_id,
                            "fields": {
                                "STAGE_ID": new_stage
                            }
                        }
                    )


                # --- 5. Create RM Meeting only on DEAL ---
                start_time, date_only = parse_rm_meeting_time(rm_meeting_time_raw)
                if start_time:
                    dt_start = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S") - timedelta(minutes=150)
                    dt_end = dt_start + timedelta(minutes=30)

                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.activity.add.json",
                        json={
                            "fields": {
                                "OWNER_TYPE_ID": 2,
                                "OWNER_ID": deal_id,
                                "TYPE_ID": 2,
                                "SUBJECT": "RM Meeting – Auto-created from Voicebot",
                                "START_TIME": dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                                "END_TIME": dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
                                "DESCRIPTION": (
                                    f"RM Meeting scheduled.\n"
                                    f"Raw: {rm_meeting_time_raw}\n"
                                    f"Parsed: {date_only}\n"
                                    f"Budget: {investment_budget_raw}"
                                ),
                                "DIRECTION": 2,
                                "COMMUNICATIONS": [
                                    {
                                        "VALUE": recipient_phone or to_number,
                                        "ENTITY_TYPE_ID": 2,
                                        "ENTITY_ID": deal_id,
                                    }
                                ],
                            }
                        }
                    )

                return {"status": "success", "flow": "deal_updated_existing"}

            # ------------------------------------------------------------
            #              FINAL FLOW BASED ON webinar_attended_norm
            # ------------------------------------------------------------

            # ---------- CASE 1: Webinar attended → YES ----------
            if webinar_attended_norm == "yes" and investment_budget_value is not None and investment_budget_value >=1000000:
                if deal_id is None:
                    print("🎉 Webinar attended = YES → Create deal + RM meeting + comments")

                    # Mark attended
                    update_fields["UF_CRM_1764239159240"] = "Y"
                    update_fields["STATUS_ID"] = "PROCESSED"

                    # Update lead FIRST
                    lead_update_payload = {"id": lead_id, "fields": update_fields}
                    print("📤 Sending lead update to Bitrix:", lead_update_payload)

                    resp = requests.post(
                        f"{BITRIX_WEBHOOK}crm.lead.update.json",
                        json=lead_update_payload
                    )


                    print("🔴 Bitrix lead.update response:", resp.text)



                    # Allow Bitrix automation to create deal (1–2 sec)
                    import time
                    time.sleep(2)
                

                # Find deal created by automation
                deal_id = find_deal_for_lead(lead_id)
                print("Deal_id:", deal_id)

                # ---------- Add timeline comments inside the deal ----------
                if deal_id:

                    # Transcript
                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                        json={
                            "fields": {
                                "ENTITY_ID": deal_id,
                                "ENTITY_TYPE": "deal",
                                "COMMENT": f"<b>Transcript</b><br>{transcript}"
                            }
                        }
                    )

                    # Summary
                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                        json={
                            "fields": {
                                "ENTITY_ID": deal_id,
                                "ENTITY_TYPE": "deal",
                                "COMMENT": f"<b>Summary</b><br>{call_summary}"
                            }
                        }
                    )

                    # Call recording link
                    if recording_url:
                        requests.post(
                            f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                            json={
                                "fields": {
                                    "ENTITY_ID": deal_id,
                                    "ENTITY_TYPE": "deal",
                                    "COMMENT": (
                                        f"<b>Call Recording</b><br>"
                                        f'<a href="{recording_url}" target="_blank">Click to Listen</a>'
                                    )
                                }
                            }
                        )

                    # ---------- Update Deal Opportunity ----------
                    if investment_budget_value:
                        requests.post(
                            f"{BITRIX_WEBHOOK}crm.deal.update.json",
                            json={
                                "id": deal_id,
                                "fields": {
                                    "OPPORTUNITY": investment_budget_value,
                                    "CURRENCY_ID": "INR",
                                    "IS_MANUAL_OPPORTUNITY": "Y"
                                }
                            }
                        )

                    # ---------- Create RM Meeting Activity ----------
                    start_time, date_only = parse_rm_meeting_time(rm_meeting_time_raw)

                    if start_time:
                        dt_start = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S")
                        dt_start = dt_start - timedelta(minutes=150)
                        dt_end = dt_start + timedelta(minutes=30)

                        act = {
                            "fields": {
                                "OWNER_TYPE_ID": 2,  # deal
                                "OWNER_ID": deal_id,
                                "TYPE_ID": 2,
                                "SUBJECT": "Scheduled RM Call – Auto-created from Voicebot",
                                "START_TIME": dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                                "END_TIME": dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
                                "DESCRIPTION": (
                                    f"RM Meeting scheduled from call.\n"
                                    f"RM_meeting_time_raw: {rm_meeting_time_raw}\n"
                                    f"Parsed date: {date_only}\n"
                                    f"Investment Budget: {investment_budget_raw}\n"
                                ),
                                "DIRECTION": 2,
                                "COMMUNICATIONS": [
                                    {
                                        "VALUE": recipient_phone or to_number,
                                        "ENTITY_TYPE_ID": 2,
                                        "ENTITY_ID": deal_id,
                                    }
                                ],
                            }
                        }

                        requests.post(
                            f"{BITRIX_WEBHOOK}crm.activity.add.json",
                            json=act
                        )

                return {"status": "success", "flow": "deal_created"}

            # ------------------------------------------------------------
            #         CASE 2: Webinar attended != YES → update LEAD only
            # ------------------------------------------------------------
            print("⚠️ Webinar attended != YES → Update lead, DO NOT create deal")

            # Prevent overwrite if already processed
            

            update_fields["UF_CRM_1764323136141"] = "Y"
            if lead_data.get("STATUS_ID") != "CONVERTED":
                if investment_budget_value is not None and 0 < investment_budget_value < 1000000:
                    update_fields["STATUS_ID"] = "JUNK"   # Move to Junk
                else:
                    update_fields["STATUS_ID"] = "14"   # Move to Unanswered to trigger automation

            # ---------- Put Opportunity inside LEAD (NOT DEAL) ----------
            if investment_budget_value:
                update_fields["OPPORTUNITY"] = investment_budget_value
                update_fields["CURRENCY_ID"] = "INR"

            # ---------- Create RM Meeting Activity directly under LEAD ----------
            start_time, date_only = parse_rm_meeting_time(rm_meeting_time_raw)

            if start_time:
                dt_start = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S")
                dt_start = dt_start - timedelta(minutes=150)
                dt_end = dt_start + timedelta(minutes=30)

                lead_activity = {
                    "fields": {
                        "OWNER_TYPE_ID": 1,  # Lead
                        "OWNER_ID": lead_id,
                        "TYPE_ID": 2,
                        "SUBJECT": "Scheduled RM Call – Auto-created from Voicebot",
                        "START_TIME": dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "END_TIME": dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "DESCRIPTION": (
                            f"RM Meeting scheduled from call.\n"
                            f"RM_meeting_time_raw: {rm_meeting_time_raw}\n"
                            f"Parsed date: {date_only}\n"
                            f"Investment Budget: {investment_budget_raw}\n"
                        ),
                        "DIRECTION": 2,
                        "COMMUNICATIONS": [
                            {
                                "VALUE": recipient_phone or to_number,
                                "ENTITY_TYPE_ID": 1,
                                "ENTITY_ID": lead_id,
                            }
                        ],
                    }
                }

                requests.post(
                    f"{BITRIX_WEBHOOK}crm.activity.add.json",
                    json=lead_activity
                )

            # ---------- Update LEAD ----------
            lead_update_payload = {"id": lead_id, "fields": update_fields}

            requests.post(
                f"{BITRIX_WEBHOOK}crm.lead.update.json",
                json=lead_update_payload
            )

            return {"status": "success", "flow": "lead_updated_only"}

        return {"status": "success"}

    return {"status": status}

