"""Exercise Telegram routing offline on synthetic messages, using the real PTB dispatcher.

Run: python tests/run_telegram_trials.py main.py --set fresh
Each fixture receives a separate synthetic user. Output describes route selection and
instruction ordering. No inference, production messages or patient data are used.
"""
import argparse
import asyncio
from dataclasses import dataclass, replace
import importlib.util
import json
import logging
import hashlib
import re
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from telegram import File, Update
from telegram.ext import Application, ApplicationBuilder
from telegram.request import BaseRequest
@dataclass(frozen=True)
class Case:
    name: str
    content: str
    expected: str
    kind: str = "text"
    chat: str = "private"
    plan: str = "available"
    pending_location: bool = False


CASES = [
    Case("English ordinary health", "I have a mild sore throat", "health"),
    Case("Gulf Arabic ordinary health", "عندي وجع في الأذن", "health"),
    Case("English provider search", "Find a dentist in Doha", "care"),
    Case("Arabic provider search", "دور لي عيادة أسنان في الدوحة", "care"),
    Case("English medicine education", "What is paracetamol?", "medicine"),
    Case("Arabic medicine education", "شنو دواء بنادول؟", "medicine"),
    Case("Emergency command at free cap", "/emergency", "urgent", kind="command", plan="free-limit"),
    Case("English acute breathing at free cap", "I cannot breathe", "urgent", plan="free-limit"),
    Case("Gulf Arabic acute breathing at free cap", "ما أقدر أتنفس", "urgent", plan="free-limit"),
    Case("Thai acute breathing at free cap", "หายใจไม่ออก", "urgent", plan="free-limit"),
    Case("Severe chest pain at Plus cap", "Severe chest pain right now", "urgent", plan="plus-limit"),
    Case("Urgency with provider request", "I cannot breathe, find a hospital near me", "urgent"),
    Case("Urgency during location question", "لا أستطيع التنفس", "urgent", pending_location=True),
    Case("Urgency caption at free cap", "I cannot breathe", "urgent", kind="photo", plan="free-limit"),
    Case("Thai facility search", "ช่วยหาโรงพยาบาลใกล้ฉัน", "care"),
    Case("Thai medicine education", "พาราเซตามอลคืออะไร", "medicine"),
    Case("English general healthcare concept", "What does consent in healthcare mean?", "health"),
    Case("Mentioned group health text", "@Pulseaihealthbot I have a rash", "private-or-silent", chat="group"),
    Case("Mentioned group health image", "@Pulseaihealthbot skin changes", "private-or-silent", kind="photo", chat="group"),
    Case("Group voice", "", "private-or-silent", kind="voice", chat="group"),
    Case("Unmentioned group health text", "I have a rash", "private-or-silent", chat="group"),
    Case("Unmentioned group health image", "skin changes", "private-or-silent", kind="photo", chat="group"),
]

# Independently written variants exercise a few easily overlooked phrasings.
# They are a small screening set for pre-model routing, not clinically adjudicated.
HELD_OUT = [
    Case("Breathing and cyanosis EN", "My child is struggling to breathe and turning blue", "urgent", plan="free-limit"),
    Case("Breathing and cyanosis Gulf AR", "ولدي شفايفه مزرقة ومو قادر ياخذ نفس", "urgent", plan="free-limit"),
    Case("Breathing and cyanosis TH", "ลูกหายใจลำบากมากและริมฝีปากเป็นสีม่วง", "urgent", plan="free-limit"),
    Case("Sudden face and arm changes EN", "My wife's face suddenly drooped and she cannot lift one arm", "urgent", plan="free-limit"),
    Case("Sudden face and arm changes Gulf AR", "أبوي فجأة اعوج فمه وما يقدر يحرك إيده", "urgent", plan="free-limit"),
    Case("Sudden face and arm changes TH", "แม่หน้าเบี้ยวทันที แขนข้างหนึ่งอ่อนแรง", "urgent", plan="free-limit"),
    Case("Current chest pressure EN", "There is crushing pressure in my chest right now", "urgent", plan="free-limit"),
    Case("Current chest pressure Gulf AR", "عندي ألم ضاغط وشديد في صدري الحين", "urgent", plan="free-limit"),
    Case("Current chest pressure TH", "ตอนนี้เจ็บหน้าอกรุนแรงเหมือนถูกกดทับ", "urgent", plan="free-limit"),
    Case("Unresponsive person EN", "My sister collapsed and won't wake up", "urgent", plan="free-limit"),
    Case("Unresponsive person Gulf AR", "أختي طاحت وما ترد علينا الحين", "urgent", plan="free-limit"),
    Case("Unresponsive person TH", "น้องสาวล้มลงและปลุกไม่ตื่น", "urgent", plan="free-limit"),
    Case("Negated symptoms EN", "I do not have chest pain and I can breathe normally. What is blood pressure?", "health"),
    Case("Negated symptoms Gulf AR", "تنفسي طبيعي وما عندي ألم في الصدر. شنو ضغط الدم؟", "health"),
    Case("Negated symptoms TH", "ฉันหายใจได้ตามปกติและไม่เจ็บหน้าอก ความดันโลหิตคืออะไร", "health"),
    Case("Quoted education EN", "What does 'severe chest pain' mean in my first-aid textbook? I feel well.", "health"),
    Case("Quoted education AR", "ماذا تعني عبارة «ألم شديد في الصدر» في كتاب الإسعافات؟ أنا بخير.", "health"),
    Case("Quoted education TH", "คำว่า «เจ็บหน้าอกรุนแรง» ในตำราปฐมพยาบาลหมายความว่าอะไร ตอนนี้ฉันสบายดี", "health"),
]


def get_fresh():
    raw = (Path(__file__).parent / "fixtures" / "telegram_novel_cases.json").read_bytes()
    expected_hash = "7ba78c9be3e4ae070f824bb626a2938d196ccc015f0d969b634d789c5b061fcb"
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("Locked independent challenge differs from its original hash")
    return [Case(f"{lang} {name}", text, target, plan="free-limit" if target=="urgent" else "available")
            for lang, name, text, target in json.loads(raw)]


def load_source(path):
    def deny(*args, **kwargs):
        raise AssertionError("A socket operation was attempted in the offline test")

    socket.socket.connect = socket.socket.connect_ex = deny
    config = ModuleType("config")
    for key in (
        "PROJECT_NAME", "PROJECT_SUPPORT_EMAIL", "PROJECT_X", "PROJECT_TELEGRAM_SUPPORT",
        "OPENAI_API_KEY", "DATABASE_URL", "GOOGLE_MAPS_API_KEY", "PULSE_PLUS_PAYLOAD",
        "HEALTH_MODEL", "VISION_MODEL", "TRANSCRIPTION_MODEL", "PRIVACY_POLICY_URL",
    ):
        setattr(config, key, "offline-fixture")
    config.TELEGRAM_BOT_TOKEN = "123:synthetic-test-token"
    config.FREE_QUESTION_LIMIT = config.PLUS_QUESTION_LIMIT = 1
    config.PULSE_PLUS_PRICE = 300
    config.PULSE_SUBSCRIPTION_PERIOD = 2592000
    database = ModuleType("database")
    database.db = NS()
    sdk = ModuleType("openai")
    sdk.AsyncOpenAI = lambda **kwargs: NS()
    sys.modules.update(config=config, database=database, openai=sdk)
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("pulse_dispatch_source", path)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    return source


def classify_reply(msg):
    lower = msg.casefold()
    if any(s in lower for s in (
        "open pulse privately", "for privacy", "in private chat", "continue privately",
    )):
        return "private-or-silent"
    if "not assessed this attachment" in lower:
        return "media-unassessed"
    if "you've used your" in lower or "upgrade to pulse plus" in lower:
        return "paywall"
    if "reached the current pulse plus" in lower:
        return "plus-limit"
    # Only an invocation of the actual registered handler counts as a dedicated
    # urgent route. General safety text may also precede a normal model answer.
    return None


def direct_instruction(text):
    return bool(re.search(
        r"\bcall\s+(?:(?:your|the|local|nearest|country[’']s|national|area[’']s|an|emergency|medical|"
        r"services|number|service|ambulance|in|your area|now)\s*){1,10}"
        r"(?:emergency|ambulance)|\b(?:call|dial)\s+(?:999|1669|120)\b|"
        r"اتصل.{0,30}(?:الطوارئ|الإسعاف)|(?:اطلب|تواصل).{0,12}الإسعاف|โทร.{0,12}(?:1669|ฉุกเฉิน)",
        text, re.IGNORECASE,
    ) or "call emergency services" in text.lower() or "call your local emergency" in text.lower())


class Transport(BaseRequest):
    @property
    def read_timeout(self):
        return 1

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1].casefold()
        data = request_data.parameters if request_data else {}
        if endpoint == "getme":
            result = {"id": 123, "is_bot":True, "first_name":"Pulse", "username":"Pulseaihealthbot"}
        elif endpoint == "sendmessage":
            self.replies.append(data["text"])
            if direct_instruction(data["text"]):
                self.events.append("instruction")
            route = classify_reply(data["text"])
            if route:
                self.calls.append(route)
                if route in ("paywall", "plus-limit"):
                    self.events.append("quota-reply")
            result = {
                "message_id":71,"date":1,"chat":{"id":int(data["chat_id"]),"type":"private"},
                "text":data["text"],
            }
        elif endpoint == "sendchataction":
            result = True
        elif endpoint == "getfile":
            self.events.append("media-fetch")
            result = {"file_id":"fixture","file_unique_id":"fixture_unique","file_path":"synthetic.jpg","file_size":1}
        else:
            raise AssertionError(f"Unexpected Telegram request: {endpoint}")
        return 200, json.dumps({"ok":True, "result":result}).encode()

    def restart_case(self):
        self.replies, self.calls, self.errors = [], [], []
        self.events = []
        self.checking_limit = False


async def run(path, suite):
    source = load_source(path)
    transport = Transport()
    transport.restart_case()
    real_urgency_handler = source.emergency
    real_access_handler = source.check_question_access
    real_care = source.perform_care_search
    real_medication = source.handle_medication_question
    async def observed_access_handler(*args, **kwargs):
        transport.checking_limit = True
        try:
            return await real_access_handler(*args, **kwargs)
        finally:
            transport.checking_limit = False
    async def observed_urgency_handler(*args, **kwargs):
        # This wraps the function before production registration. It is only
        # entered by the actual matched dispatcher route, not by a keyword in
        # a generic safety notice or an acknowledgement from a billing handler.
        tag = "generic-urgent-copy" if transport.checking_limit else "actual-urgent-handler"
        transport.events.append(tag)
        return await real_urgency_handler(*args, **kwargs)
    source.emergency = observed_urgency_handler
    source.check_question_access = observed_access_handler
    apps = []
    make_builder = lambda: ApplicationBuilder().request(transport).get_updates_request(transport)
    with patch.object(Application, "builder", staticmethod(make_builder)), \
         patch.object(Application, "run_polling", lambda self, *args, **kwargs: apps.append(self)):
        source.main()
    application, = apps
    # The production DB run_polling startup hook is purposely not executed.
    async def save_error(update, context):
        transport.errors.append(repr(context.error))
    application.add_error_handler(save_error)
    results = []
    async with application:
        for i, case in enumerate(suite, 1):
            transport.restart_case()
            # The recorded event is the input text with just its declared media type.
            # Use real Telegram update decoding, filters, command handling and
            # conversation priority. All external services remain offline.
            user_id = 2000 + i
            chat_id = user_id if case.chat == "private" else -user_id
            message = {
                "message_id":i, "date":1795250000,
                "chat":{"id":chat_id,"type":case.chat},
                "from":{"id":user_id,"is_bot":False,"first_name":"Synthetic"},
            }
            if case.kind in {"command","text"}:
                message["text"] = case.content
                if case.kind == "command":
                    message["entities"] = [{"type":"bot_command","offset":0,"length":len(case.content)}]
            if case.kind == "photo":
                message.update(photo=[{"file_id":"fixture","file_unique_id":"fixture_unique","width":1,"height":1,"file_size":1}], caption=case.content)
            if case.kind == "voice":
                message.update(voice={"file_id":"voice","file_unique_id":"voice_unique","duration":1,"file_size":1})
            update = Update.de_json({"update_id":i, "message":message}, application.bot)
            if case.pending_location:
                application.user_data[user_id].update(awaiting_care_location=True, pending_care_query="hospital near me")

            async def ask(**kwargs):
                transport.events.append("model")
                if case.kind=="photo":
                    transport.calls.append("image-ai")
                elif kwargs.get("instructions","").endswith(source.MEDICATION_PROMPT):
                    transport.calls.append("medicine")
                else:
                    transport.calls.append("health")
                return NS(output_text="Synthetic general response.")

            async def care(*args, **kwargs):
                transport.calls.append("care")
                return await real_care(*args, **kwargs)

            async def medicine(*args, **kwargs):
                transport.calls.append("medicine")
                return await real_medication(*args, **kwargs)

            async def get_plan(*args):
                transport.events.append("usage-read")
                return ("PLUS" if case.plan == "plus-limit" else "FREE", 0 if case.plan == "available" else 1, 1)
            async def get_places(*args, **kwargs):
                transport.events.append("places")
                return []
            async def transcribe(**kwargs):
                transport.events.append("transcribe")
                return NS(text=case.content)

            source.get_plan_and_usage = get_plan
            async def invoice(*args, **kwargs):
                transport.events.append("invoice-work")
                return "https://example.invalid/fixture"
            source.create_plus_invoice = invoice
            source.client = NS(responses=NS(create=ask),audio=NS(transcriptions=NS(create=transcribe)))
            source.db = NS(get_location=AsyncMock(return_value=None))
            source.google_places_search = get_places
            source.perform_care_search = care
            source.handle_medication_question = medicine
            source.record_and_show_usage = AsyncMock()
            with patch.object(File, "download_as_bytearray", AsyncMock(return_value=bytearray([1]))), \
                 patch.object(File, "download_to_drive", AsyncMock(return_value=None)):
                await application.process_update(update)
            if transport.errors:
                raise RuntimeError((case.name,transport.errors))
            route_matched = "actual-urgent-handler" in transport.events
            fallback = not route_matched and "instruction" in transport.events and case.plan!="available"
            first_cost = next((ix for ix,s in enumerate(transport.events) if s in ("invoice-work","quota-reply")), len(transport.events))
            first_instruction = next((ix for ix,s in enumerate(transport.events) if s=="instruction"), len(transport.events))
            first_processing = next((ix for ix,s in enumerate(transport.events) if s in ("media-fetch","transcribe","model","places")), None)
            first_usage = next((ix for ix,s in enumerate(transport.events) if s=="usage-read"),None)
            if route_matched:
                outcome = "urgent"
            elif fallback:
                outcome = "generic-safety-fallback"
            else:
                outcome = transport.calls[0] if transport.calls else ("other reply" if transport.replies else "private-or-silent")
            if outcome=="urgent" and not route_matched:
                outcome = "other safety text"
            results.append(dict(case=case.name, expected=case.expected, observed=outcome, matched=case.expected==outcome,
                                actual_urgent_handler=route_matched,
                                generic_direct_instruction=fallback,
                                instruction_before_billing=fallback and first_instruction<first_cost,
                                first_sensitive_operation=None if first_processing is None else transport.events[first_processing],
                                instruction_before_sensitive_operation=None if first_processing is None else first_instruction<first_processing,
                                instruction_before_usage_read=None if first_usage is None else first_instruction<first_usage,
                                events=transport.events[:],
                                thai_in_visible_instruction=any(direct_instruction(msg) and re.search(r"[\u0e01-\u0e5b]", msg)
                                                                  for msg in transport.replies)))
    print(json.dumps(dict(matches=sum(r["matched"] for r in results), total=len(results), cases=results),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("main_py", type=Path)
    parser.add_argument("--set", choices=["baseline","held-out","fresh","fresh-open-urgent","open-modalities"], default="baseline", dest="suite")
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    fresh = get_fresh()
    suites={"baseline":CASES, "held-out":HELD_OUT, "fresh":fresh,
            "fresh-open-urgent":[replace(case, plan="available") for case in fresh if case.expected=="urgent"],
            "open-modalities":[Case("Open English text", "I have a mild sore throat", "health"),
                               Case("Open Gulf Arabic text", "عندي ألم بسيط في حلقي", "health"),
                               Case("Open photo", "What does the printed label say?", "image-ai", kind="photo"),
                               Case("Open voice", "I have a mild sore throat", "health", kind="voice"),
                               Case("Open urgent voice", "I cannot breathe", "urgent", kind="voice"),
                               Case("Urgent photo caption", "I cannot breathe", "urgent", kind="photo"),
                               Case("Open medication text", "What is paracetamol?", "medicine"),
                               Case("Open provider search", "Find a dentist in Doha", "care")]}
    asyncio.run(run(args.main_py.resolve(), suites[args.suite]))
