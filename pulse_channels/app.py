import asyncio
import contextlib
import hmac
import json
import logging
import sqlite3
import time
from email.utils import parsedate_to_datetime

import aiohttp
from aiohttp import web

from .delivery import Outbox
from .menu import render
from .platforms import BadCallback, Unauthorized, line_replies, outbound_request, verify_hmac, whatsapp_replies
from . import wechat

LOG = logging.getLogger(__name__)
GATEWAY = web.AppKey("gateway", object)
CALLBACK_LIMIT = 256 * 1024


def retry_delay(header, now):
    try:
        if header is not None and header.isascii() and header.isdigit():
            return max(0, int(header))
        if header:
            return max(0, parsedate_to_datetime(header).timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        pass
    return 0


class Gateway:
    def __init__(self, settings, sender=None, clock=time.time, workers=True):
        self.settings, self.sender, self.clock = settings, sender, clock
        self.run_workers = workers
        self.outboxes = {
            channel: Outbox(channel, settings.state_dir, settings.state_key)
            for channel in ("whatsapp", "line") if channel in settings.enabled
        }
        self.wakes = {channel: asyncio.Event() for channel in self.outboxes}
        self.client = None

    async def lifecycle(self, app):
        if self.outboxes and self.sender is None:
            self.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5, connect=2))
        tasks = [asyncio.create_task(self.worker(ch)) for ch in self.outboxes] if self.run_workers else []
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if self.client:
                await self.client.close()

    async def post(self, url, token, payload):
        try:
            async with self.client.post(url, headers={"Authorization": "Bearer " + token},
                                        json=payload, allow_redirects=False) as response:
                # Do not retain provider bodies, which may contain identifiers.
                if 200 <= response.status < 300:
                    return "accepted", 0
                if response.status == 429:
                    return "retry", retry_delay(response.headers.get("Retry-After"), self.clock())
                if response.status >= 500 or response.status == 408:
                    return "unknown", 0
                return "rejected", 0
        except (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError):
            # A connection could not be established. The provider has no request.
            return "retry", 0
        except (aiohttp.ClientError, asyncio.TimeoutError):
            # A response was lost after handoff. Resending could duplicate a message.
            return "unknown", 0

    async def process_one(self, channel):
        box = self.outboxes[channel]
        job = await asyncio.to_thread(box.claim, self.clock())
        if job is None:
            return False
        if self.clock() >= job.deadline:
            result, delay = "expired", 0
        else:
            text = render(job.language, job.action, self.settings.support_url)
            send = self.sender or self.post
            result, delay = await send(*outbound_request(channel, job, text, self.settings))
        await asyncio.to_thread(box.complete, job, result, self.clock(), delay)
        return True

    async def worker(self, channel):
        wake = self.wakes[channel]
        while True:
            wake.clear()
            try:
                while await self.process_one(channel):
                    pass
            except Exception:
                # Includes storage or configuration failures, never callback text.
                LOG.error("%s reply worker paused; inspect configuration and durable storage", channel)
            try:
                await asyncio.wait_for(wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    async def handle(self, request):
        channel = request.match_info["channel"]
        if channel not in self.settings.enabled:
            raise web.HTTPNotFound
        try:
            if request.method == "GET":
                return self.handshake(channel, request.query)
            if request.method != "POST":
                raise web.HTTPMethodNotAllowed(request.method, ["POST"])
            # Reject unsupported signing data before interpreting any message content.
            if request.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise BadCallback
            raw = await request.read()
            if channel == "wechat":
                reply = wechat.passive_reply(raw, request.query, self.settings, self.clock())
                return (web.Response(body=reply, content_type="application/xml") if reply is not None
                        else web.Response(text="success"))
            header, secret, parser = (("X-Line-Signature", self.settings.line_secret, line_replies)
                                       if channel == "line" else
                                       ("X-Hub-Signature-256", self.settings.wa_app_secret, whatsapp_replies))
            verify_hmac(raw, request.headers.get(header), secret, channel)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise BadCallback
            now = self.clock()
            replies = list(parser(data, self.settings, now))
            if replies:
                await asyncio.to_thread(self.outboxes[channel].add, replies, now)
                self.wakes[channel].set()
            return web.Response(text="OK")
        except Unauthorized:
            raise web.HTTPForbidden(text="Invalid callback authentication") from None
        except (BadCallback, json.JSONDecodeError, UnicodeError, RecursionError):
            raise web.HTTPBadRequest(text="Invalid callback") from None
        except (sqlite3.Error, OSError):
            raise web.HTTPServiceUnavailable(text="Storage unavailable") from None

    def handshake(self, channel, query):
        if channel == "wechat":
            return web.Response(body=wechat.challenge(query, self.settings, self.clock()), content_type="text/plain")
        if channel == "whatsapp":
            token, challenge = query.get("hub.verify_token", ""), query.get("hub.challenge", "")
            if query.get("hub.mode") != "subscribe" or not hmac.compare_digest(
                    token.encode(), self.settings.wa_verify_token.encode()):
                raise Unauthorized
            if not 1 <= len(challenge) <= 1024:
                raise BadCallback
            return web.Response(text=challenge)
        raise web.HTTPMethodNotAllowed("GET", ["POST"])


async def private_headers(request, response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


async def health(request):
    return web.json_response({"ok": True})


def create_app(settings, *, sender=None, clock=time.time, workers=True):
    app = web.Application(client_max_size=CALLBACK_LIMIT)
    handler = Gateway(settings, sender=sender, clock=clock, workers=workers)
    app[GATEWAY] = handler
    app.cleanup_ctx.append(handler.lifecycle)
    app.on_response_prepare.append(private_headers)
    app.router.add_get("/healthz", health)
    app.router.add_route("*", "/webhooks/{channel:whatsapp|line|wechat}", handler.handle)
    return app
