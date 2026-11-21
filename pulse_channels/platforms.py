import base64
import hmac
import re

from .delivery import Reply
from .menu import opted_out, selection


class BadCallback(ValueError):
    pass


class Unauthorized(BadCallback):
    pass


def verify_hmac(raw, supplied, secret, channel):
    mac = hmac.digest(secret.encode(), raw, "sha256")
    expected = base64.b64encode(mac).decode() if channel == "line" else "sha256=" + mac.hex()
    if not supplied or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise Unauthorized


def objects(data, name):
    records = data.get(name, [])
    if not isinstance(records, list):
        raise BadCallback
    return (r for r in records if isinstance(r, dict))


def obj(data, name):
    result = data.get(name)
    return result if isinstance(result, dict) else {}


def value_id(value):
    return isinstance(value, str) and 0 < len(value) <= 512 and not any(c.isspace() for c in value)


def timestamp(value, scale=1):
    try:
        return int(value) / scale
    except (ValueError, TypeError, OverflowError):
        return 0


def line_replies(data, config, now):
    if data.get("destination") != config.line_destination:
        raise Unauthorized
    for event in objects(data, "events"):
        if (obj(event, "source").get("type") != "user" or event.get("mode") != "active"
                or event.get("type") not in {"message", "follow", "postback"}):
            continue
        event_id, token = event.get("webhookEventId"), event.get("replyToken")
        sent = timestamp(event.get("timestamp"), 1000)
        redelivery = obj(event, "deliveryContext").get("isRedelivery") is True
        if not value_id(event_id) or not value_id(token) or sent > now + 60 or sent <= 0:
            continue
        # Ordinary replies use the event time conservatively. For a redelivery the
        # provider allows one minute after receipt, up to 20 minutes after the event.
        deadline = min(now + 55, sent + (1195 if redelivery else 55))
        if deadline <= now:
            continue
        message = obj(event, "message")
        if event.get("type") == "follow":
            language, action = "th", "menu"
        else:
            text = (obj(event, "postback").get("data") if event["type"] == "postback" else
                    message.get("text") if message.get("type") == "text" else None)
            if opted_out(text):
                continue
            language, action = selection(text, "th")
        yield Reply(event_id, {"replyToken": token}, language, action, deadline)


def whatsapp_replies(data, config, now):
    if data.get("object") != "whatsapp_business_account":
        raise BadCallback
    for entry in objects(data, "entry"):
        if entry.get("id") != config.wa_business_id:
            continue
        for change in objects(entry, "changes"):
            value = obj(change, "value")
            if (change.get("field") != "messages" or value.get("messaging_product") != "whatsapp"
                    or obj(value, "metadata").get("phone_number_id") != config.wa_phone_id
                    or value.get("group_id")):
                continue
            for msg in objects(value, "messages"):
                # The group webhook's group_id lives on the message object.
                if msg.get("group_id") or obj(msg, "context").get("group_id"):
                    continue
                kind = msg.get("type")
                if kind not in {"text", "image", "audio", "video", "document", "sticker", "location", "contacts",
                                "interactive", "button"}:
                    continue
                msg_id, phone, bsuid = msg.get("id"), msg.get("from"), msg.get("from_user_id")
                sent = timestamp(msg.get("timestamp"))
                deadline = sent + 86400 - 60
                if not value_id(msg_id) or sent <= 0 or sent > now + 60 or deadline <= now:
                    continue
                if value_id(bsuid):
                    route = {"recipient": bsuid}
                elif isinstance(phone, str) and re.fullmatch(r"\+?[0-9]{5,20}", phone):
                    route = {"to": phone}
                else:
                    continue
                if kind == "interactive":
                    interactive = obj(msg, "interactive")
                    text = obj(interactive, "button_reply").get("id") or obj(interactive, "list_reply").get("id")
                elif kind == "button":
                    text = obj(msg, "button").get("payload")
                else:
                    text = obj(msg, "text").get("body") if kind == "text" else None
                if opted_out(text):
                    continue
                language, action = selection(text, "en")
                yield Reply(msg_id, route, language, action, deadline)


def outbound_request(channel, job, text, config):
    if channel == "line":
        return ("https://api.line.me/v2/bot/message/reply", config.line_access_token,
                {**job.route, "messages": [{"type": "text", "text": text}]})
    return (f"https://graph.facebook.com/{config.wa_graph_version}/{config.wa_phone_id}/messages",
            config.wa_access_token,
            {"messaging_product": "whatsapp", "recipient_type": "individual", **job.route,
             "type": "text", "text": {"body": text, "preview_url": False}})
