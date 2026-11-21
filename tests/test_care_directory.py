"""Care Finder minimisation tests with local fixtures for Telegram, Maps and OpenAI."""
import json
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock

import httpx

from care_directory import confirmed_area, project_request

with mock.patch.dict(os.environ, {
    "TELEGRAM_BOT_TOKEN": "123456:offlineFixture", "OPENAI_API_KEY": "offlineFixture",
    "DATABASE_URL": "postgresql://offline.invalid/fixture", "NO_PROXY": "*",
    "no_proxy": "*", "ALL_PROXY": "", "all_proxy": "", "HTTP_PROXY": "",
    "http_proxy": "", "HTTPS_PROXY": "", "https_proxy": "",
}):
    import main


class DirectoryProjectionTests(TestCase):
    def test_canonical_categories_and_clean_areas(self):
        cases = [
            ("nearest dentist near Doha", "dentist, Doha", "en"),
            ("find cardiologists in Manchester", "cardiologist, Manchester", "en"),
            ("عيادة جلدية في الدوحة", "طبيب جلدية, Doha", "ar"),
            ("أبي دكتور أنف وأذن في الوكرة", "طبيب أنف وأذن وحنجرة, Al Wakrah", "ar"),
            ("أفضل دكتور تقويم اسنان في الوكرة", "طبيب تقويم أسنان, Al Wakrah", "ar"),
            ("أقرب صيدلية في أبو ظبي", "صيدلية, Abu Dhabi", "ar"),
            ("I have private symptoms. Find a hospital in Hong Kong", "hospital, Hong Kong", "en"),
            ("find pharmacies in Bangkok, Thailand", "pharmacy, Bangkok, Thailand", "en"),
            ("My cardiologist in London suggested a rest. Find a pharmacy in Doha; my history is private.", "pharmacy, Doha", "en"),
            ("كان عندي موعد في الدوحة. أبي صيدلية في المنامة", "صيدلية, Manama", "ar"),
        ]
        for raw, wanted, language in cases:
            with self.subTest(raw=raw):
                lookup = project_request(raw)
                self.assertEqual(lookup.text_query, wanted)
                self.assertEqual(lookup.language, language)

    def test_unknown_or_ambiguous_areas_require_confirmation(self):
        for raw in ("my left hand", "I have a rash", "thanks", "yes", "Doha 99123456",
                    "48 Private Street", "عندي ألم", "عمان", "Doha, unknown narrative"):
            with self.subTest(raw=raw):
                self.assertIsNone(confirmed_area(raw))
        for raw in ("clinic in my left knee", "hospital in Unknownville", "صيدلية في مكان خاص"):
            with self.subTest(raw=raw):
                lookup = project_request(raw)
                self.assertIsNone(lookup.area)
                self.assertTrue(lookup.area_requested)
        self.assertIsNone(project_request("I have pain in my left hand"))
        self.assertEqual(confirmed_area("الدوحة، قطر"), "Doha, Qatar")
        self.assertEqual(confirmed_area("مدينة عمان"), "Amman")
        self.assertEqual(confirmed_area("سلطنة عمان"), "Oman")
        self.assertFalse(project_request("find a clinic near me").area_requested)


class DirectoryIntegrationTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.chat = SimpleNamespace(type="private", send_action=mock.AsyncMock())
        self.message = SimpleNamespace(text="", chat=self.chat, reply_text=mock.AsyncMock())
        self.update = SimpleNamespace(message=self.message, effective_message=self.message,
                                      effective_user=SimpleNamespace(id=42), effective_chat=self.chat)
        self.context = SimpleNamespace(user_data={})
        for name, value in (
            ("GOOGLE_MAPS_API_KEY", "offlineFixture"), ("record_and_show_usage", mock.AsyncMock()),
            ("check_question_access", mock.AsyncMock(return_value=True)),
        ):
            self.enterContext(mock.patch.object(main, name, value))

    async def test_outbound_maps_payload_is_projected(self):
        captured = []
        def respondent(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"places": []})
        local_http = httpx.AsyncClient(transport=httpx.MockTransport(respondent))
        fixture = project_request("Private conditions. Find a clinic in Doha")
        with mock.patch.object(main.httpx, "AsyncClient", return_value=local_http):
            await main.google_places_search(fixture)
        self.assertEqual(captured, [{"textQuery": "clinic, Doha", "pageSize": 5, "languageCode": "en"}])

    async def test_no_lookup_without_confirmed_area_or_shared_coordinate(self):
        with mock.patch.object(main.httpx, "AsyncClient") as network:
            with self.assertRaises(ValueError):
                await main.google_places_search(project_request("clinic in my hand"))
            network.assert_not_called()

    async def test_private_coordinate_search_has_no_raw_query(self):
        captured = []
        def respondent(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"places": []})
        local_http = httpx.AsyncClient(transport=httpx.MockTransport(respondent))
        with mock.patch.object(main.httpx, "AsyncClient", return_value=local_http):
            await main.google_places_search(project_request("Please find a clinic near me because of private conditions"), 25.0, 50.0)
        self.assertEqual(captured[0]["textQuery"], "clinic")
        self.assertEqual(captured[0]["locationBias"]["circle"]["center"], {"latitude": 25.0, "longitude": 50.0})

    async def test_unknown_named_place_never_falls_back_to_stored_location(self):
        db_location = mock.AsyncMock(return_value={"latitude": 25.0, "longitude": 50.0})
        with mock.patch.object(main.db, "get_location", db_location), \
             mock.patch.object(main, "google_places_search", new_callable=mock.AsyncMock) as google:
            await main.perform_care_search(self.update, self.context, "find a doctor in private narrative")
        db_location.assert_not_awaited()
        google.assert_not_awaited()
        self.assertEqual(self.context.user_data["pending_care_query"].facility, "doctor")
        self.assertNotIn("private narrative", repr(self.context.user_data))

    async def test_pending_city_and_changing_topic(self):
        for text, expected_maps in [("Doha", "clinic, Doha"), ("I have a cough", None), ("thanks", None)]:
            self.context.user_data.update(awaiting_care_location=True, pending_care_query=project_request("clinic near me"))
            self.message.text = text
            with self.subTest(text=text), \
                 mock.patch.object(main, "google_places_search", new_callable=mock.AsyncMock, return_value=[]) as google, \
                 mock.patch.object(main.client.responses, "create", new_callable=mock.AsyncMock,
                                   return_value=SimpleNamespace(output_text="Health fixture")) as model:
                await main.handle_message(self.update, self.context)
                if expected_maps:
                    self.assertEqual(google.await_args.args[0].text_query, expected_maps)
                else:
                    google.assert_not_awaited()
                self.assertEqual(model.await_count, 1 if text == "I have a cough" else 0)

    async def test_web_verification_only_sends_public_facility_fields(self):
        place = {"displayName": {"text": "Example Clinic"}, "formattedAddress": "Public address",
                 "websiteUri": "https://example.org", "_distance_km": 1.9,
                 "user_message": "private question", "location": {"latitude": 25, "longitude": 50}}
        with mock.patch.object(main.client.responses, "create", new_callable=mock.AsyncMock,
                               return_value=SimpleNamespace(output_text="checked")) as create:
            self.assertEqual(await main.verify_places_on_web([place], language="ar"), "checked")
        data = create.await_args.kwargs
        self.assertEqual(json.loads(data["input"]), [{
            "name": "Example Clinic", "address": "Public address", "website": "https://example.org"
        }])
        self.assertIn("Arabic", data["instructions"])
        self.assertFalse(data["store"])

    def test_visible_attribution_in_both_languages(self):
        place = {"displayName": {"text": "Example"}, "attributions": [{"provider": "Example directory", "providerUri": "https://example.org"}]}
        for facility in ("clinic", "عيادة"):
            with self.subTest(facility=facility):
                message, _ = main.format_care_results([place], facility)
                self.assertIn("Google Maps", message)
                self.assertIn("Example directory (https://example.org)", message)
