"""Replay 300 unique synthetic messages twice over local HTTP with no external network sends."""
import asyncio
import base64
import hashlib
import hmac
import json
import math
import statistics
import tempfile
import time
from xml.etree import ElementTree as ET

from aiohttp.test_utils import TestClient, TestServer

from pulse_channels import wechat
from pulse_channels.app import GATEWAY, create_app
from pulse_channels.settings import Settings
from test_pulse_channels import NOW, env, line_event, line_payload, wa_message, wa_payload, wx_request


async def run():
    with tempfile.TemporaryDirectory() as directory:
        settings = Settings.from_env(env(directory))
        sent = set()

        async def receive_outbound(url, token, payload):
            destination = payload.get("recipient") or payload.get("replyToken")
            assert destination and destination not in sent, "Duplicate active send"
            sent.add(destination)
            return "accepted", 0

        client = TestClient(TestServer(create_app(settings, sender=receive_outbound,
                                                  clock=lambda: NOW, workers=False)))
        await client.start_server()
        gate = asyncio.Semaphore(6)
        durations = []
        try:
            async def send(channel, event):
                kwargs = {}
                if channel == "wechat":
                    kwargs["data"], kwargs["params"] = wx_request(settings, MsgId=str(event), content="人工")
                elif channel == "line":
                    raw = json.dumps(line_payload(line_event(event_id=f"id-{event}", replyToken=f"reply-{event}"))).encode()
                    signature = base64.b64encode(hmac.digest(settings.line_secret.encode(), raw, "sha256")).decode()
                    kwargs.update(data=raw, headers={"X-Line-Signature": signature})
                else:
                    raw = json.dumps(wa_payload(wa_message(event_id=f"wa-{event}", from_user_id=f"US.{event}"))).encode()
                    signature = hmac.new(settings.wa_app_secret.encode(), raw, hashlib.sha256).hexdigest()
                    kwargs.update(data=raw, headers={"X-Hub-Signature-256": "sha256=" + signature})
                async with gate:
                    start = time.perf_counter()
                    response = await client.post("/webhooks/" + channel, **kwargs)
                    body = await response.read()
                    durations.append((time.perf_counter() - start) * 1000)
                    assert response.status == 200
                    if channel == "wechat":
                        xml = ET.fromstring(wechat.decrypt(ET.fromstring(body).findtext("Encrypt"), settings))
                        assert xml.findtext("ToUserName") == "test-OpenID"
                        assert settings.wx_contact in xml.findtext("Content")
                    else:
                        assert body == b"OK"

            for _ in range(2):
                await asyncio.gather(*(send(channel, number) for number in range(100)
                                       for channel in ("line", "whatsapp", "wechat")))
            gateway = client.app[GATEWAY]
            for channel in ("line", "whatsapp"):
                while await gateway.process_one(channel):
                    pass
            assert len(sent) == 200
            durations.sort()
            print(json.dumps({"unique_events": 300, "loopback_http_callbacks": len(durations),
                              "verified_callbacks_returning_200": len(durations), "mocked_active_sends": len(sent),
                              "encrypted_passive_responses": 200, "provider_network_calls": 0,
                              "concurrent_requests": 6,
                              "callback_p50_ms": round(statistics.median(durations), 1),
                              "callback_p95_ms": round(durations[math.ceil(len(durations) * 0.95) - 1], 1)}))
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(run())
