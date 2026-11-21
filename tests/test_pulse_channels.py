"""Synthetic, offline checks. No real provider tokens, health records, or external hosts."""
import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from xml.etree import ElementTree as ET

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from pulse_channels import wechat
from pulse_channels.app import CALLBACK_LIMIT, GATEWAY, Gateway, create_app, retry_delay
from pulse_channels.delivery import Outbox, Reply, RETAIN_SECONDS
from pulse_channels.menu import selection
from pulse_channels.platforms import Unauthorized
from pulse_channels.settings import Settings

NOW = 1800000000
KEY = bytes(range(32))
KEY64 = base64.b64encode(KEY).decode()
STATE_KEY = base64.b64encode(bytes(reversed(range(32)))).decode()
CONTACT = "https://pulse.example/support"
PRIVATE = "private-secret-example"


def env(directory):
    return {
        "PULSE_WHATSAPP_ENABLED": "true", "PULSE_LINE_ENABLED": "true", "PULSE_WECHAT_ENABLED": "true",
        "PULSE_CHANNEL_STATE_DIR": str(directory), "PULSE_CHANNEL_STATE_KEY": STATE_KEY,
        "PULSE_SUPPORT_URL": CONTACT, "WA_APP_SECRET": "fake-wa-secret", "WA_VERIFY_TOKEN": "synthetic",
        "WA_ACCESS_TOKEN": "fake-wa-token", "WA_PHONE_NUMBER_ID": "123", "WA_BUSINESS_ACCOUNT_ID": "456",
        "WA_GRAPH_VERSION": "v9.9", "LINE_CHANNEL_SECRET": "fake-line-secret",
        "LINE_CHANNEL_ACCESS_TOKEN": "fake-line-token", "LINE_DESTINATION_ID": "U-test-account",
        "WECHAT_CALLBACK_TOKEN": "fake-wx-token", "WECHAT_APP_ID": "wx-test-app",
        "WECHAT_ORIGINAL_ID": "gh-test-account", "WECHAT_ENCODING_AES_KEY": KEY64.rstrip("="),
        "WECHAT_SUPPORT_CONTACT": "+00 0000000",
    }


def wa_message(event_id="wamid.fake", **changes):
    msg = {"id": event_id, "timestamp": str(NOW), "from": "15550001111",
           "type": "text", "text": {"body": "hello"}}
    msg.update(changes)
    return msg


def wa_payload(*messages):
    value = {"metadata": {"phone_number_id": "123"}, "messaging_product": "whatsapp", "messages": list(messages)}
    return {"object": "whatsapp_business_account", "entry": [{"id": "456", "changes": [{"field": "messages", "value": value}]}]}


def line_event(event_id="line-event", **changes):
    event = {"type": "message", "source": {"type": "user", "userId": "U-test-user"}, "mode": "active",
             "replyToken": "reply-token-private", "webhookEventId": event_id, "timestamp": NOW * 1000,
             "message": {"type": "text", "text": "menu"}}
    event.update(changes)
    return event


def line_payload(*events):
    return {"destination": "U-test-account", "events": list(events)}


def wx_request(config, *, original="gh-test-account", kind="text", content="菜单", **fields):
    message = {"ToUserName": original, "FromUserName": "test-OpenID", "CreateTime": NOW,
               "MsgType": kind, "MsgId": "fake-wx-message", "Content": content, **fields}
    encoded = wechat.encrypt(wechat.xml_bytes(message), config)
    nonce, stamp = "synthetic-nonce", str(NOW)
    sign = wechat.signature(config.wx_token, stamp, nonce, encoded)
    return wechat.xml_bytes({"Encrypt": encoded}), {
        "nonce": nonce, "timestamp": stamp, "msg_signature": sign, "encrypt_type": "aes",
    }


def status_rows(box):
    with sqlite3.connect(box.path) as db:
        return db.execute("SELECT status, sealed, id, attempts FROM replies ORDER BY id").fetchall()


class SettingsTests(unittest.TestCase):
    def test_default_off(self):
        self.assertFalse(Settings.from_env({}).enabled)

    def test_independent_flags(self):
        config = env("/tmp/test-only")
        cfg = Settings.from_env({k: v for k, v in config.items() if k.startswith("WECHAT_") or k == "PULSE_WECHAT_ENABLED"})
        self.assertEqual(cfg.enabled, {"wechat"})
        self.assertIsNone(cfg.state_dir)
        self.assertEqual(cfg.wx_portal, "")

    def test_enabled_requires_credentials(self):
        with self.assertRaises(ValueError):
            Settings.from_env({"PULSE_LINE_ENABLED": "true"})

    def test_unsupported_flag_rejected(self):
        with self.assertRaises(ValueError):
            Settings.from_env({"PULSE_LINE_ENABLED": "automatic"})

    def test_linkless_support_route(self):
        config = env("/tmp/test-only")
        config.pop("PULSE_SUPPORT_URL")
        config["PULSE_SUPPORT_CONTACT"] = "+00 00000"
        self.assertEqual(Settings.from_env(config).support_url, "+00 00000")

    def test_invalid_settings(self):
        invalid = {"PULSE_SUPPORT_URL": "http://pulse.example/support", "WA_GRAPH_VERSION": "latest",
                   "PULSE_CHANNEL_STATE_KEY": "short", "WECHAT_ENCODING_AES_KEY": "Z" * 42,
                   "WECHAT_SUPPORT_CONTACT": "https://external.invalid", "PULSE_CHANNEL_STATE_DIR": "relative",
                   "WA_PHONE_NUMBER_ID": "phone", "WA_BUSINESS_ACCOUNT_ID": "tenant"}
        for key, value in invalid.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                Settings.from_env({**env("/tmp/test-only"), key: value})

    def test_menu_languages_and_unstructured_content(self):
        for text, expected in {"Account": ("en", "account"), "الخصوصية": ("ar", "privacy"),
                               "เจ้าหน้าที่": ("th", "human"), "菜单": ("zh", "menu"),
                               PRIVATE: ("en", "other"), "ألم": ("ar", "other")}.items():
            with self.subTest(text=text):
                self.assertEqual(selection(text, "en"), expected)


class CallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Settings.from_env(env(self.temp.name))
        self.calls = []

        async def fake_send(url, token, payload):
            self.calls.append((url, token, payload))
            return "accepted", 0

        self.app = create_app(self.config, clock=lambda: NOW, sender=fake_send, workers=False)
        self.handler = self.app[GATEWAY]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def send_json(self, channel, data, bad=False):
        raw = json.dumps(data, ensure_ascii=False).encode()
        if channel == "line":
            mac = base64.b64encode(hmac.digest(self.config.line_secret.encode(), raw, "sha256")).decode()
            headers = {"X-Line-Signature": mac}
        else:
            mac = hmac.new(self.config.wa_app_secret.encode(), raw, hashlib.sha256).hexdigest()
            headers = {"X-Hub-Signature-256": "sha256=" + mac}
        if bad:
            raw += b" "
        return await self.client.post("/webhooks/" + channel, data=raw, headers=headers)

    async def drain(self, channel):
        while await self.handler.process_one(channel):
            pass

    async def test_health_and_disabled_routes(self):
        self.assertEqual(await (await self.client.get("/healthz")).json(), {"ok": True})
        client = TestClient(TestServer(create_app(Settings.from_env({}), workers=False)))
        await client.start_server()
        try:
            for name in ("whatsapp", "line", "wechat", "unknown"):
                with self.subTest(name=name):
                    self.assertEqual((await client.post("/webhooks/" + name)).status, 404)
        finally:
            await client.close()

    async def test_wa_handshake(self):
        params = {"hub.mode": "subscribe", "hub.verify_token": "synthetic", "hub.challenge": "98765"}
        response = await self.client.get("/webhooks/whatsapp", params=params)
        self.assertEqual((response.status, await response.text()), (200, "98765"))
        params["hub.verify_token"] = "wrong"
        self.assertEqual((await self.client.get("/webhooks/whatsapp", params=params)).status, 403)

    async def test_wa_recipients_and_envelope(self):
        only_bsuid = wa_message("bsuid", from_user_id="US.0001")
        del only_bsuid["from"]
        both = wa_message("both", from_user_id="US.0002")
        response = await self.send_json("whatsapp", wa_payload(only_bsuid, wa_message("phone"), both))
        self.assertEqual((response.status, await response.text()), (200, "OK"))
        self.assertFalse(self.calls)
        await self.drain("whatsapp")
        payloads = [p for _, _, p in self.calls]
        self.assertEqual({p.get("recipient", p.get("to")) for p in payloads}, {"US.0001", "US.0002", "15550001111"})
        for url, token, p in self.calls:
            self.assertEqual(url, "https://graph.facebook.com/v9.9/123/messages")
            self.assertEqual(token, "fake-wa-token")
            self.assertEqual(p["recipient_type"], "individual")
            self.assertNotEqual("to" in p, "recipient" in p)
            self.assertFalse(p["text"]["preview_url"])

    async def test_wa_duplicate_batches_and_redelivery(self):
        data = wa_payload(wa_message("one"), wa_message("two"), wa_message("one"))
        for _ in range(2):
            self.assertEqual((await self.send_json("whatsapp", data)).status, 200)
        await self.drain("whatsapp")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((await self.send_json("whatsapp", data)).status, 200)
        await self.drain("whatsapp")
        self.assertEqual(len(self.calls), 2)

    async def test_wa_wrong_tenant_and_phone(self):
        a, b = wa_payload(wa_message()), wa_payload(wa_message())
        a["entry"][0]["id"] = "0000"
        b["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "9999"
        for data in (a, b):
            self.assertEqual((await self.send_json("whatsapp", data)).status, 200)
        self.assertFalse(status_rows(self.handler.outboxes["whatsapp"]))

    async def test_wa_groups_statuses_unsupported_stale_and_optout(self):
        data = wa_payload(wa_message("group", group_id="group"),
                          wa_message("old", timestamp=str(NOW - 86400)),
                          wa_message("stop", text={"body": "إلغاء الاشتراك"}),
                          wa_message("future", timestamp=str(NOW + 700)), wa_message("status", type="system"))
        status = {"statuses": [{"status": "delivered"}], "messaging_product": "whatsapp", "metadata": {"phone_number_id": "123"}}
        data["entry"][0]["changes"].append({"field": "messages", "value": status})
        self.assertEqual((await self.send_json("whatsapp", data)).status, 200)
        self.assertFalse(status_rows(self.handler.outboxes["whatsapp"]))

    async def test_wa_health_text_media_minimization(self):
        data = wa_payload(wa_message("example-text", text={"body": PRIVATE}),
                          wa_message("media", type="image", image={"id": PRIVATE, "caption": PRIVATE}))
        await self.send_json("whatsapp", data)
        with sqlite3.connect(self.handler.outboxes["whatsapp"].path) as db:
            dump = "".join(db.iterdump())
            for forbidden in (PRIVATE, "15550001111", "example-text"):
                self.assertNotIn(forbidden, dump)
        await self.drain("whatsapp")
        self.assertEqual(len(self.calls), 2)
        for _, _, payload in self.calls:
            self.assertNotIn(PRIVATE, str(payload))
            self.assertIn("general support", payload["text"]["body"])

    async def test_line_empty_and_signed_receipt(self):
        response = await self.send_json("line", line_payload())
        self.assertEqual((response.status, await response.text()), (200, "OK"))
        self.assertFalse(self.calls)
        self.assertEqual((await self.send_json("line", {"destination": "other", "events": []})).status, 403)

    async def test_line_reply_once_and_redelivery(self):
        data = line_payload(line_event(message={"type": "text", "text": "الخصوصية"}))
        for _ in range(2):
            self.assertEqual((await self.send_json("line", data)).status, 200)
        await self.drain("line")
        self.assertEqual(len(self.calls), 1)
        url, token, payload = self.calls[0]
        self.assertEqual((url, token), ("https://api.line.me/v2/bot/message/reply", "fake-line-token"))
        self.assertEqual(payload["replyToken"], "reply-token-private")
        self.assertIn("الدعم", payload["messages"][0]["text"])

    async def test_line_groups_standby_stale_and_optout(self):
        results = [line_event("g", source={"type": "group"}), line_event("r", source={"type": "room"}),
                   line_event("off", mode="standby"), line_event("late", timestamp=(NOW - 80) * 1000),
                   line_event("stop", message={"type": "text", "text": "หยุด"}),
                   line_event("unknown", type="unsend")]
        self.assertEqual((await self.send_json("line", line_payload(*results))).status, 200)
        self.assertFalse(status_rows(self.handler.outboxes["line"]))

    async def test_line_redelivery_timing(self):
        yes = line_event("yes", timestamp=(NOW - 100) * 1000, deliveryContext={"isRedelivery": True})
        no = line_event("no", timestamp=(NOW - 1201) * 1000, deliveryContext={"isRedelivery": True})
        await self.send_json("line", line_payload(yes, no))
        self.assertEqual(len(status_rows(self.handler.outboxes["line"])), 1)

    async def test_line_media_sends_fixed_text(self):
        await self.send_json("line", line_payload(line_event(message={"type": "audio", "id": PRIVATE})))
        await self.drain("line")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("ข้อมูล", self.calls[0][2]["messages"][0]["text"])
        self.assertNotIn(PRIVATE, str(self.calls))

    async def test_hmac_checks_exact_bytes(self):
        for name, data in [("whatsapp", wa_payload(wa_message())), ("line", line_payload(line_event()))]:
            with self.subTest(name=name):
                self.assertEqual((await self.send_json(name, data, bad=True)).status, 403)
                self.assertFalse(status_rows(self.handler.outboxes[name]))

    async def test_malformed_signed_and_oversized_request(self):
        self.assertEqual((await self.send_json("line", ["not", "object"])).status, 400)
        self.assertEqual((await self.send_json("line", {"destination": "U-test-account", "events": {}})).status, 400)
        response = await self.client.post("/webhooks/whatsapp", data=b"a" * (CALLBACK_LIMIT + 10))
        self.assertEqual(response.status, 413)

    async def test_wechat_standard_get(self):
        stamp, nonce = str(NOW), "n1"
        query = {"timestamp": stamp, "nonce": nonce, "signature": wechat.signature(self.config.wx_token, stamp, nonce),
                 "echostr": "official-account-check"}
        response = await self.client.get("/webhooks/wechat", params=query)
        self.assertEqual((response.status, await response.text()), (200, "official-account-check"))

    async def test_wechat_encrypted_echo_independent_vector(self):
        # Independently generated with OpenSSL AES-256-CBC and the Tencent frame.
        echo = "4j/AuRx71kQlxVlzbpsMWHIVb1v3njpXNPow7sEgKAbZ+uJaLUgDmCw4vySEfo5fp/jE7fgy1tjw/kwd3NuC1Q=="
        stamp, nonce = str(NOW), "n2"
        query = {"timestamp": stamp, "nonce": nonce, "echostr": echo,
                 "msg_signature": wechat.signature(self.config.wx_token, stamp, nonce, echo)}
        response = await self.client.get("/webhooks/wechat", params=query)
        self.assertEqual((response.status, await response.text()), (200, "opaque-challenge"))

    async def test_wechat_encrypted_private_reply_and_replay(self):
        raw, params = wx_request(self.config, content=PRIVATE)
        for _ in range(2):
            response = await self.client.post("/webhooks/wechat", params=params, data=raw)
            self.assertEqual((response.status, response.content_type), (200, "application/xml"))
            reply = ET.fromstring(await response.read())
            cipher = reply.findtext("Encrypt")
            self.assertEqual(reply.findtext("MsgSignature"), wechat.signature(
                self.config.wx_token, reply.findtext("TimeStamp"), reply.findtext("Nonce"), cipher))
            msg = ET.fromstring(wechat.decrypt(cipher, self.config))
            self.assertEqual(msg.findtext("ToUserName"), "test-OpenID")
            self.assertEqual(msg.findtext("FromUserName"), self.config.wx_original_id)
            self.assertIn(self.config.wx_contact, msg.findtext("Content"))
            self.assertNotIn(CONTACT, msg.findtext("Content"))
            self.assertNotIn(PRIVATE, msg.findtext("Content"))
        self.assertFalse(self.calls)

    async def test_wechat_other_receiver_signature_and_old_time(self):
        raw, params = wx_request(self.config, original="wrong-account")
        self.assertEqual((await self.client.post("/webhooks/wechat", params=params, data=raw)).status, 403)
        raw, params = wx_request(self.config)
        for var, value in [("encrypt_type", "raw"), ("timestamp", str(NOW - 350)), ("msg_signature", "bad")]:
            with self.subTest(var=var):
                self.assertEqual((await self.client.post("/webhooks/wechat", params={**params, var: value}, data=raw)).status, 403)

    async def test_wechat_unsubscribe_and_optout_no_reply(self):
        for kind, value, extra in [("text", "停止", {}), ("event", "", {"Event": "unsubscribe"})]:
            raw, params = wx_request(self.config, kind=kind, content=value, **extra)
            response = await self.client.post("/webhooks/wechat", params=params, data=raw)
            self.assertEqual((response.status, await response.text()), (200, "success"))

    async def test_wechat_frame_binds_app_id(self):
        wrong = Settings.from_env({**env(self.temp.name), "WECHAT_APP_ID": "another-app"})
        encoded = wechat.encrypt(b"challenge", wrong)
        with self.assertRaises(Unauthorized):
            wechat.decrypt(encoded, self.config)

    async def test_inbox_unavailable_signals_redelivery(self):
        self.handler.outboxes["line"].add = Mock(side_effect=sqlite3.OperationalError("synthetic"))
        self.assertEqual((await self.send_json("line", line_payload(line_event()))).status, 503)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.box = Outbox("line", Path(self.temp.name), KEY)
        self.item = Reply("private-event-id", {"replyToken": "secret-routing"}, "en", "menu", NOW + 100)

    def test_separate_channel_storage(self):
        other = Outbox("whatsapp", Path(self.temp.name), KEY)
        self.box.add([self.item], NOW)
        other.add([self.item], NOW)
        self.assertNotEqual(status_rows(self.box)[0][2], status_rows(other)[0][2])

    def test_state_survives_restart_and_route_purges(self):
        self.box.add([self.item], NOW)
        reopened = Outbox("line", Path(self.temp.name), KEY)
        job = reopened.claim(NOW)
        self.assertEqual(job.route, self.item.route)
        reopened.complete(job, "accepted", NOW)
        reopened.add([self.item], NOW)
        self.assertIsNone(reopened.claim(NOW))
        self.assertEqual(status_rows(self.box)[0][:2], ("accepted", None))

    def test_changed_key_detected(self):
        self.box.add([self.item], NOW)
        with self.assertRaises(ValueError):
            Outbox("line", Path(self.temp.name), bytes(reversed(KEY)))

    def test_concurrent_claim_has_one_winner(self):
        self.box.add([self.item], NOW)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(self.box.claim, [NOW] * 6))
        self.assertEqual(sum(x is not None for x in results), 1)

    def test_known_rejection_retry_and_backoff(self):
        self.box.add([self.item], NOW)
        job = self.box.claim(NOW)
        self.box.complete(job, "retry", NOW, retry_after=5)
        self.assertIsNone(self.box.claim(NOW + 4))
        retry = self.box.claim(NOW + 5)
        self.assertEqual((retry.route, retry.attempts), (self.item.route, 2))
        self.box.complete(retry, "accepted", NOW + 6)
        self.assertIsNone(self.box.claim(NOW + 10))

    def test_ambiguous_delivery_will_not_duplicate(self):
        self.box.add([self.item], NOW)
        self.box.complete(self.box.claim(NOW), "unknown", NOW)
        self.box.add([self.item], NOW)
        self.assertIsNone(self.box.claim(NOW))
        self.assertEqual(status_rows(self.box)[0][:2], ("unknown", None))

    def test_crash_in_flight_terminal(self):
        self.box.add([self.item], NOW)
        self.assertIsNotNone(self.box.claim(NOW))
        self.assertIsNone(self.box.claim(NOW + 31))
        self.assertEqual(status_rows(self.box)[0][:2], ("unknown", None))

    def test_expired_delivery_and_dedupe_gc(self):
        self.box.add([self.item], NOW)
        self.assertIsNone(self.box.claim(NOW + 101))
        self.assertEqual(status_rows(self.box)[0][:2], ("expired", None))
        self.box.claim(NOW + RETAIN_SECONDS + 1)
        self.assertEqual(status_rows(self.box), [])

    def test_unreadable_job_does_not_block_subsequent_work(self):
        self.box.add([self.item], NOW)
        with sqlite3.connect(self.box.path) as db:
            db.execute("UPDATE replies SET sealed=?", (b"unreadable",))
        self.assertIsNone(self.box.claim(NOW))
        self.assertEqual(status_rows(self.box)[0][:2], ("rejected", None))
        self.box.add([Reply("next", self.item.route, "en", "menu", NOW + 100)], NOW)
        self.assertIsNotNone(self.box.claim(NOW))

    def test_retry_never_outlives_window(self):
        self.box.add([self.item], NOW)
        self.box.complete(self.box.claim(NOW), "retry", NOW, retry_after=180)
        self.assertEqual(status_rows(self.box)[0][:2], ("expired", None))


class HTTPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_status_classes_without_network(self):
        from unittest.mock import MagicMock
        gateway = Gateway(Settings.from_env({}), workers=False, clock=lambda: NOW)
        for status, expected in [(200, "accepted"), (202, "accepted"), (302, "rejected"),
                                 (400, "rejected"), (401, "rejected"), (408, "unknown"),
                                 (429, "retry"), (500, "unknown"), (503, "unknown")]:
            with self.subTest(status=status):
                response = Mock(status=status, headers={"Retry-After": "12"})
                cm = MagicMock()
                cm.__aenter__ = AsyncMock(return_value=response)
                gateway.client = Mock()
                gateway.client.post.return_value = cm
                result, delay = await gateway.post("https://example.invalid", "fake", {"a": "b"})
                self.assertEqual((result, delay), (expected, 12 if expected == "retry" else 0))
                gateway.client.post.assert_called_once_with("https://example.invalid",
                        headers={"Authorization": "Bearer fake"}, json={"a": "b"}, allow_redirects=False)

    async def test_preconnect_and_uncertain_io(self):
        gateway = Gateway(Settings.from_env({}), workers=False)
        for exc, expected in [(aiohttp.ConnectionTimeoutError(), "retry"), (asyncio.TimeoutError(), "unknown"),
                              (aiohttp.ServerDisconnectedError(), "unknown")]:
            gateway.client = Mock()
            gateway.client.post.side_effect = exc
            result, _ = await gateway.post("https://example.invalid", "fake", {})
            self.assertEqual(result, expected)

    async def test_retry_after_httpdate_and_bad(self):
        self.assertEqual(retry_delay("Fri, 15 Jan 2027 08:00:10 GMT", 1800000000), 10)
        self.assertEqual(retry_delay("bad", NOW), 0)

    async def test_real_worker_lifecycle_with_mock_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            seen = asyncio.Event()

            async def capture(url, token, payload):
                seen.set()
                return "accepted", 0

            cfg = Settings.from_env(env(directory))
            app = create_app(cfg, sender=capture, clock=lambda: NOW)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                raw = json.dumps(line_payload(line_event())).encode()
                mac = base64.b64encode(hmac.digest(cfg.line_secret.encode(), raw, "sha256")).decode()
                response = await client.post("/webhooks/line", data=raw, headers={"X-Line-Signature": mac})
                self.assertEqual(response.status, 200)
                await asyncio.wait_for(seen.wait(), timeout=2)
            finally:
                await client.close()


class InteropTests(unittest.TestCase):
    def test_wechat_cdata_and_independent_passive_decode(self):
        import shutil
        import struct
        import subprocess
        if not shutil.which("openssl"):
            self.skipTest("OpenSSL CLI is optional for independent CBC decoding")
        cfg = Settings.from_env(env("/tmp/unit-test"))
        official_xml = b"""<xml><ToUserName><![CDATA[gh-test-account]]></ToUserName>
                <FromUserName><![CDATA[userxy]]></FromUserName><CreateTime>1800000000</CreateTime>
                <MsgType><![CDATA[text]]></MsgType><Content><![CDATA[account]]></Content>
                <MsgId>1234567890123456</MsgId></xml>"""
        encrypted = wechat.encrypt(official_xml, cfg)
        outer = ("<xml><ToUserName><![CDATA[gh-test-account]]></ToUserName>"
                 "<Encrypt><![CDATA[" + encrypted + "]]></Encrypt></xml>").encode()
        query = {"timestamp": str(NOW), "nonce": "plain-nonce", "encrypt_type": "aes",
                 "msg_signature": wechat.signature(cfg.wx_token, str(NOW), "plain-nonce", encrypted)}
        response = ET.fromstring(wechat.passive_reply(outer, query, cfg, NOW))
        decoded = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-K", KEY.hex(),
                                  "-iv", KEY[:16].hex(), "-nopad"], check=True, capture_output=True,
                                 input=base64.b64decode(response.findtext("Encrypt"))).stdout
        pad = decoded[-1]
        self.assertIn(pad, range(1, 33))
        self.assertEqual(decoded[-pad:], bytes([pad]) * pad)
        packet = decoded[:-pad]
        length = struct.unpack("!I", packet[16:20])[0]
        message = ET.fromstring(packet[20:20+length])
        self.assertEqual(message.findtext("ToUserName"), "userxy")
        self.assertIn("billing", message.findtext("Content"))
        self.assertEqual(packet[20+length:], cfg.wx_app_id.encode())

    def test_private_portal_targets_rejected(self):
        for url in ("https://127.0.0.1/", "https://localhost/", "https://node.internal/",
                    "https://pulse.example/health?userid=abc", "https://example.invalid:bad/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Settings.from_env({**env("/tmp/unit-test"), "PULSE_SUPPORT_URL": url})
