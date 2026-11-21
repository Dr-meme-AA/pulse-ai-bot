"""Offline integration tests that dispatch real PTB 22.8 updates to the bot.

Telegram HTTP, OpenAI and Postgres are test doubles. No platform accounts or
credentials are used and no real private messages or media are sent.
"""

import json
import logging
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock

from telegram import Update
from telegram.ext import Application, ApplicationBuilder
from telegram.request import BaseRequest

FIXTURE_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456:offlineFixture",
    "OPENAI_API_KEY": "offlineFixture",
    "DATABASE_URL": "postgresql://offline.invalid/fixture",
    "NO_PROXY": "*", "no_proxy": "*",
    "all_proxy": "", "ALL_PROXY": "", "HTTP_PROXY": "", "HTTPS_PROXY": "",
    "http_proxy": "", "https_proxy": "",
}
with mock.patch.dict(os.environ, FIXTURE_ENV):
    import main

from pulse_routing import (
    EMERGENCY_MESSAGE, MEDIA_NOT_ASSESSED, PREPROCESSING_NOTICE, QUESTION_NOT_ASSESSED,
    has_named_place, reports_urgent_sign,
)


class RoutingTests(TestCase):
    def test_finite_emergency_phrase_examples(self):
        reports = [
            "I can’t breathe", "She is choking", "My baby stopped breathing",
            "I have severe chest pain", "I have pain in my chest", "My face is drooping",
            "I am having a seizure", "My mother is unconscious", "The bleeding won't stop",
            "No chest pain, but I can't breathe", "I can't breathe; what should I do?",
            "I am bleeding heavily", "I think I'm having a stroke", "I just took an overdose",
            "I am about to end my life", "I suddenly cannot breathe",
            "ما أقدر أتنفس", "أنا مو قادرة أتنفس", "ولدي ما يتنفس", "طفلي يختنق",
            "أبوي طاح وما يرد", "عندي ألم شديد في صدري", "بنتي فاقدة الوعي",
            "الدم ما يوقف", "ما عندي ألم لكن ما أقدر أتنفس", "أخذت جرعة زائدة", "أبي أنهي حياتي",
        ]
        for report in reports:
            with self.subTest(report=report):
                self.assertTrue(reports_urgent_sign(report))

    def test_negation_hypothetical_and_education(self):
        ordinary = [
            "", "I have no chest pain", "I'm not having chest pain", "I can breathe normally",
            "What are the causes of severe chest pain?", "How do I say I can't breathe in Arabic?",
            "What if my baby stopped breathing?", "I can't breathe through my nose",
            "I have a seizure disorder", "Signs of stroke?", "I do not want to end my life",
            "I had chest pain years ago", "No severe bleeding", "ما عندي ألم شديد في الصدر",
            "ما أسباب ضيق النفس؟", "ماذا لو طفلي يختنق؟", "مش عندي ألم في الصدر",
            "أقدر أتنفس عادي", "ما أقدر أتنفس من خشمي", "ما أبي أنهي حياتي",
        ]
        for question in ordinary:
            with self.subTest(question=question):
                self.assertFalse(reports_urgent_sign(question))

    def test_family_report_synonyms_and_nonurgent_context(self):
        current = [
            "My child is struggling to breathe and turning blue",
            "ولدي شفايفه مزرقة ومو قادر ياخذ نفس",
            "My wife's face suddenly drooped and she cannot lift one arm",
            "أبوي فجأة اعوج فمه وما يقدر يحرك إيده",
            "There is crushing pressure in my chest right now",
            "عندي ألم ضاغط وشديد في صدري الحين",
            "My sister collapsed and won't wake up",
            "أختي طاحت وما ترد علينا الحين",
            "شَفايفِه مُزْرَقّة", "Her lips are turning blue",
        ]
        history_or_education = [
            "تنفسي طبيعي وما عندي ألم في الصدر. شنو ضغط الدم؟",
            "What does 'severe chest pain' mean in my first-aid textbook? I feel well.",
            "ماذا تعني عبارة «ألم شديد في الصدر» في كتاب الإسعافات؟ أنا بخير.",
            "My wife's face suddenly drooped years ago. What is an MRI?",
            "طفلي شفايفه مزرقة أمس", "My baby is not turning blue",
            "If my partner is struggling to breathe, what does first aid involve?",
        ]
        for text in current:
            with self.subTest(text=text):
                self.assertTrue(reports_urgent_sign(text))
        for text in history_or_education:
            with self.subTest(text=text):
                self.assertFalse(reports_urgent_sign(text))

    def test_english_word_boundaries_and_named_places(self):
        for query in ["find entertainment", "find a patent", "find this content", "patient in Doha"]:
            with self.subTest(query=query):
                self.assertFalse(main.looks_like_care_search(query))
        for query in ["find ENT in Doha", "dentists near Doha", "nearest pharmacies in Manama"]:
            with self.subTest(query=query):
                self.assertTrue(main.looks_like_care_search(query))
        for query in ["My pillow is uncomfortable", "I spilled water", "Is the capillary visible?"]:
            with self.subTest(query=query):
                self.assertFalse(main.looks_like_medication_question(query))
        for query in ["pills", "can I take ibuprofen?", "دوائي"]:
            with self.subTest(query=query):
                self.assertTrue(main.looks_like_medication_question(query))
        for given in ["nearest pharmacy near Doha", "ENT in London", "صيدلية قرب الدوحة", "عيادة في الوكرة"]:
            with self.subTest(given=given):
                self.assertTrue(has_named_place(given))
        for relative in ["near me", "closest pharmacy", "pharmacy near my location", "صيدلية في منطقتي"]:
            with self.subTest(relative=relative):
                self.assertFalse(has_named_place(relative))

    def test_http_loggers_hide_urls(self):
        for name in ("httpx", "httpcore"):
            self.assertFalse(logging.getLogger(name).isEnabledFor(logging.INFO))


class OfflineTelegram(BaseRequest):
    """Only implements the Bot API operations expected by the offline suite."""

    def __init__(self):
        self.sent = []
        self.file_fetches = 0
        self.notices_at_fetch = []

    @property
    def read_timeout(self):
        return 5.0

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **timeouts):
        name = url.rsplit("/", 1)[-1]
        if name == "getMe":
            response = {"id": 77, "is_bot": True, "first_name": "Pulse", "username": "PulseTestBot"}
        elif name == "sendMessage":
            params = request_data.parameters
            self.sent.append(params)
            response = {
                "message_id": len(self.sent) + 100, "date": 1000,
                "chat": {"id": params["chat_id"], "type": "private"}, "text": params["text"],
            }
        elif name == "getFile":
            self.file_fetches += 1
            self.notices_at_fetch.append(sum(m["text"] == PREPROCESSING_NOTICE for m in self.sent))
            response = {"file_id": "fixture", "file_unique_id": "unit", "file_path": "voice/test.ogg"}
        elif "/file/bot" in url:
            return 200, b"offline voice fixture"
        elif name == "sendChatAction":
            response = True
        else:
            raise AssertionError(f"Unexpected Telegram method {name}")
        return 200, json.dumps({"ok": True, "result": response}).encode()


class DispatcherTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(mock.patch.dict(os.environ, FIXTURE_ENV))
        self.transport = OfflineTelegram()
        self.sequence = 0
        builder = ApplicationBuilder().request(self.transport).get_updates_request(OfflineTelegram())
        with mock.patch.object(Application, "builder", return_value=builder), \
             mock.patch.object(Application, "run_polling", autospec=True) as run:
            main.main()
        self.app = run.call_args.args[0]
        self.errors = []

        async def capture_error(update, context):
            self.errors.append(context.error)

        self.app.add_error_handler(capture_error)
        await self.app.initialize()
        self.addAsyncCleanup(self.app.shutdown)
        self.access = self.patch(main, "get_plan_and_usage", return_value=("FREE", 20, 20))
        self.db_location = self.patch(main.db, "get_location", return_value=None)
        self.saved_location = self.patch(main.db, "save_location")
        self.model = self.patch(main.client.responses, "create", return_value=SimpleNamespace(output_text="fixture"))
        self.transcribe = self.patch(main.client.audio.transcriptions, "create")
        self.invoice = self.patch(main, "create_plus_invoice", return_value="https://unit.invalid/offer")
        self.usage = self.patch(main, "record_and_show_usage")

    def patch(self, target, name, **kwargs):
        stub = mock.AsyncMock(**kwargs)
        self.enterContext(mock.patch.object(target, name, stub))
        return stub

    async def dispatch(self, *, chat_type="private", update_type="message", text=None, caption=None, media=None):
        self.sequence += 1
        message = {
            "message_id": self.sequence, "date": 1000,
            "chat": {"id": 42 if chat_type == "private" else -100123, "type": chat_type},
            "from": {"id": 42, "is_bot": False, "first_name": "Fixture"},
        }
        if text is not None:
            message["text"] = text
            if text.startswith("/"):
                message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if caption is not None:
            message["caption"] = caption
        file_spec = {"file_id": "fixture", "file_unique_id": "unit", "file_size": 20}
        if media == "photo":
            message["photo"] = [dict(file_spec, width=16, height=16)]
        if media == "voice":
            message["voice"] = dict(file_spec, duration=2)
        if media == "location":
            message["location"] = {"latitude": 0.25, "longitude": 0.5}
        update = Update.de_json({"update_id": self.sequence, update_type: message}, self.app.bot)
        await self.app.process_update(update)
        self.assertEqual(self.errors, [])

    def assert_no_patient_requests(self):
        self.access.assert_not_awaited()
        self.db_location.assert_not_awaited()
        self.saved_location.assert_not_awaited()
        self.model.assert_not_awaited()
        self.transcribe.assert_not_awaited()
        self.usage.assert_not_awaited()
        self.invoice.assert_not_awaited()
        self.assertEqual(self.transport.file_fetches, 0)

    async def test_group_mentions_navigate_without_quote_or_model(self):
        for chat_type in ("group", "supergroup"):
            for message in [
                {"text": "@pulsetestbot my private symptom"},
                {"text": "@PulseTestBot"}, {"text": "/emergency@pulsetestbot"},
                {"media": "photo", "caption": "@pulsetestbot clinic near me"},
                {"media": "voice", "caption": "@pulsetestbot"},
            ]:
                with self.subTest(message=message, chat_type=chat_type):
                    before = len(self.transport.sent)
                    await self.dispatch(chat_type=chat_type, **message)
                    self.assertEqual(len(self.transport.sent), before + 1)
                    params = self.transport.sent[-1]
                    self.assertIn("privately", params["text"])
                    self.assertNotIn("reply_parameters", params)
                    self.assertNotIn("my private symptom", str(params))
                    self.assertIn("https://t.me/PulseTestBot", str(params["reply_markup"]))
        self.assert_no_patient_requests()

    async def test_ignored_public_and_edited_updates_have_no_side_effects(self):
        for message in [
            {"text": "my symptom"}, {"media": "photo"}, {"media": "voice"}, {"media": "location"},
            {"text": "@PulseTestBot123 symptom"}, {"text": "sample@PulseTestBot"},
        ]:
            await self.dispatch(chat_type="group", **message)
        for kind in ("edited_message", "channel_post", "edited_channel_post"):
            await self.dispatch(update_type=kind, chat_type="channel" if "channel" in kind else "private",
                                text="@PulseTestBot I can't breathe")
        self.assertEqual(self.transport.sent, [])
        self.assert_no_patient_requests()

    async def test_urgent_before_quota_care_and_media_download(self):
        for message in [
            {"text": "/emergency"}, {"text": "I can't breathe, find a clinic near me"},
            {"text": "/findcare hospital I have severe chest pain"},
            {"media": "photo", "caption": "ما أقدر أتنفس"},
            {"media": "voice", "caption": "My mother is unconscious"},
        ]:
            previous = len(self.transport.sent)
            await self.dispatch(**message)
            self.assertEqual(len(self.transport.sent), previous + 1)
            self.assertEqual(self.transport.sent[-1]["text"], EMERGENCY_MESSAGE)
            self.assertNotIn("reply_markup", self.transport.sent[-1])
        self.assert_no_patient_requests()

    async def test_draft_and_pending_care_are_ended_before_an_urgent_message(self):
        await self.dispatch(text="/remindmedicine")
        self.app.user_data[42].update(pending_care_query="clinic", awaiting_care_location=True)
        await self.dispatch(text="طفلي يختنق")
        self.assertEqual(self.transport.sent[-1]["text"], EMERGENCY_MESSAGE)
        self.assertNotIn("med_reminder_draft", self.app.user_data[42])
        self.assertNotIn("pending_care_query", self.app.user_data[42])
        self.assert_no_patient_requests()
        await self.dispatch(text="What causes seasonal allergies?")
        self.access.assert_awaited_once()
        self.assertNotIn("strength", self.transport.sent[-1]["text"].lower())

    async def test_urgent_picture_caption_can_end_reminder_strength_state(self):
        await self.dispatch(text="/remindmedicine")
        await self.dispatch(text="fixture medicine")
        self.assertEqual(self.app.user_data[42]["med_reminder_draft"]["medicine_name"], "fixture medicine")
        await self.dispatch(media="photo", caption="أبوي طاح وما يرد")
        self.assertEqual(self.transport.sent[-1]["text"], EMERGENCY_MESSAGE)
        self.assertNotIn("med_reminder_draft", self.app.user_data[42])
        self.assert_no_patient_requests()

    async def test_edited_text_in_an_active_reminder_is_ignored(self):
        await self.dispatch(text="/remindmedicine")
        count = len(self.transport.sent)
        await self.dispatch(update_type="edited_message", text="private edit of a previous question")
        await self.dispatch(update_type="edited_message", text="/cancel")
        self.assertEqual(len(self.transport.sent), count)
        self.assertEqual(self.app.user_data[42]["med_reminder_draft"], {})
        self.assert_no_patient_requests()

    async def test_private_voice_transcript_exits_draft_before_care_and_ai(self):
        self.access.return_value = ("FREE", 0, 20)
        self.transcribe.return_value = SimpleNamespace(text="I can't breathe, find a doctor near me")
        await self.dispatch(text="/remindmedicine")
        await self.dispatch(media="voice")
        self.assertEqual(self.transport.sent[-1]["text"], EMERGENCY_MESSAGE)
        self.assertNotIn("med_reminder_draft", self.app.user_data[42])
        self.assertEqual(self.transport.file_fetches, 1)
        self.assertIn(PREPROCESSING_NOTICE, [m["text"] for m in self.transport.sent[:-1]])
        self.transcribe.assert_awaited_once()
        self.db_location.assert_not_awaited()
        self.model.assert_not_awaited()
        self.usage.assert_not_awaited()
        await self.dispatch(text="What causes seasonal allergies?")
        self.model.assert_awaited_once()

    async def test_media_at_both_limits_shows_bilingual_urgent_first(self):
        for plan, limit in (("FREE", 20), ("PLUS", 100)):
            for medium in ("photo", "voice"):
                self.transport.sent.clear()
                self.access.return_value = (plan, limit, limit)
                await self.dispatch(media=medium)
                self.assertEqual(self.transport.sent[0]["text"], MEDIA_NOT_ASSESSED)
                self.assertIn("/emergency", self.transport.sent[0]["text"])
                self.assertIn("الطوارئ", self.transport.sent[0]["text"])
                self.assertNotIn(PREPROCESSING_NOTICE, [m["text"] for m in self.transport.sent])
        self.assertEqual(self.transport.file_fetches, 0)
        self.model.assert_not_awaited()
        self.transcribe.assert_not_awaited()
        self.invoice.assert_not_awaited()

    async def test_unrecognized_text_at_limit_gets_action_before_allowance_and_no_invoice(self):
        for plan, limit in (("FREE", 20), ("PLUS", 100)):
            self.transport.sent.clear()
            self.access.return_value = (plan, limit, limit)
            await self.dispatch(text="I feel something unusual")
            texts = [message["text"] for message in self.transport.sent]
            self.assertEqual(texts[0], QUESTION_NOT_ASSESSED)
            self.assertIn("call your local emergency number now", texts[0])
            self.assertIn("الطوارئ", texts[0])
            self.assertNotIn("/upgrade", texts[0])
            self.assertEqual("/upgrade" in texts[1], plan == "FREE")
        self.invoice.assert_not_awaited()
        self.model.assert_not_awaited()

    async def test_pending_input_at_limit_never_sends_search_prompt_first(self):
        await self.dispatch(text="/help")
        for fields in ({"text": "Doha"}, {"media": "location"}):
            self.transport.sent.clear()
            self.app.user_data[42].update(awaiting_care_location=True, pending_care_query="dentist near me")
            await self.dispatch(**fields)
            self.assertEqual(self.transport.sent[0]["text"], QUESTION_NOT_ASSESSED)
        self.saved_location.assert_not_awaited()
        self.invoice.assert_not_awaited()
        self.model.assert_not_awaited()

    async def test_one_notice_precedes_general_medication_vision_and_transcription_providers(self):
        self.access.return_value = ("FREE", 0, 20)
        for fields in (
            {"text": "What causes seasonal allergies?"}, {"text": "Tell me about ibuprofen."},
            {"media": "photo"}, {"media": "voice"},
        ):
            with self.subTest(fields=fields):
                self.transport.sent.clear()
                self.transport.notices_at_fetch.clear()
                self.usage.reset_mock()
                self.model.reset_mock()
                self.transcribe.reset_mock()

                def assert_notice_first(**kwargs):
                    self.assertEqual([m["text"] for m in self.transport.sent], [PREPROCESSING_NOTICE])
                    return SimpleNamespace(output_text="fixture explanation")

                def transcribe_after_notice(**kwargs):
                    assert_notice_first()
                    return SimpleNamespace(text="What causes seasonal allergies?")

                self.model.side_effect = assert_notice_first
                self.transcribe.side_effect = transcribe_after_notice
                await self.dispatch(**fields)
                texts = [m["text"] for m in self.transport.sent]
                self.assertEqual(texts.count(PREPROCESSING_NOTICE), 1)
                self.assertIn("fixture explanation", texts[-1])
                self.model.assert_awaited_once()
                self.usage.assert_awaited_once()
                self.assertEqual(self.transport.notices_at_fetch, [1] if "media" in fields else [])
                self.assertEqual(self.transcribe.await_count, int(fields.get("media") == "voice"))

    async def test_transcribed_directory_request_has_one_notice_for_both_checks(self):
        self.access.return_value = ("PLUS", 7, 100)
        self.transcribe.return_value = SimpleNamespace(text="find ENT near Doha")

        def directory_after_notice(*args, **kwargs):
            self.assertEqual([m["text"] for m in self.transport.sent], [PREPROCESSING_NOTICE])
            return []

        directory = self.patch(main, "google_places_search", side_effect=directory_after_notice)
        with mock.patch.object(main, "GOOGLE_MAPS_API_KEY", "fixture"):
            await self.dispatch(media="voice")
        self.assertEqual([m["text"] for m in self.transport.sent].count(PREPROCESSING_NOTICE), 1)
        self.assertEqual(self.transport.notices_at_fetch, [1])
        self.transcribe.assert_awaited_once()
        directory.assert_awaited_once()
        self.model.assert_not_awaited()
        self.usage.assert_not_awaited()

    async def test_named_area_precedes_saved_gps_relative_search_requests_location(self):
        self.access.return_value = ("FREE", 0, 20)
        places = self.patch(main, "google_places_search", return_value=[])
        with mock.patch.object(main, "GOOGLE_MAPS_API_KEY", "test-only"):
            await self.dispatch(text="nearest dentist near Doha")
            places.assert_awaited_once()
            query, latitude, longitude = places.await_args.args
            self.assertEqual(query.area, "Doha")
            self.assertEqual((latitude, longitude), (None, None))
            self.db_location.assert_not_awaited()
            await self.dispatch(text="nearest dentist near me")
            self.db_location.assert_awaited_once_with(42)
            self.assertIn("Share My Location", str(self.transport.sent[-1]["reply_markup"]))
            self.assertEqual(places.await_count, 1)

    async def test_care_log_omits_identity_and_user_input(self):
        self.access.return_value = ("FREE", 0, 20)
        self.patch(main, "google_places_search", return_value=[])
        with mock.patch.object(main, "GOOGLE_MAPS_API_KEY", "test-only"), \
             self.assertLogs(main.logger, level=logging.INFO) as observed:
            await self.dispatch(text="find ENT near Doha")
        self.assertFalse(any("42" in line or "Doha" in line for line in observed.output))
