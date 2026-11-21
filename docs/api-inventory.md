# Pulse AI API inventory

This inventory covers the Telegram beta, the three business support adapters, and the platform operations an operator may need to enable them. The [OpenAPI contract](openapi.channels.yaml) describes the HTTP routes provided by Pulse. The [gateway guide](channel-gateway.md) covers account settings, configuration, testing and delivery behavior.

## Telegram health beta

| Interface | Operations used | What goes through it |
|---|---|---|
| Telegram Bot API | `getUpdates`, `sendMessage`, `sendChatAction`, `getFile`, `answerCallbackQuery` | Telegram hosts the bot API at `https://api.telegram.org/bot<TOKEN>/<method>`. Media downloads use `https://api.telegram.org/file/bot<TOKEN>/<file_path>`. Keep full URLs out of logs. The Python bot polls for updates. |
| Telegram Stars | `createInvoiceLink`, `answerPreCheckoutQuery`, incoming `successful_payment` | Currency `XTR`, one price item for 300 Stars, 2,592,000-second subscription period, empty provider token. Free allowance 20. Plus allowance 100 per 30 days. Reconcile provider-confirmed payments before enabling live subscriptions. |
| OpenAI Responses | `POST https://api.openai.com/v1/responses` | Bearer `OPENAI_API_KEY`. The beta sends `model`, `instructions`, `input`, `max_output_tokens` and `store:false`. Photo input includes `input_text` plus `input_image`. Facility verification uses `tools:[{"type":"web_search"}]`. |
| OpenAI transcription | `POST https://api.openai.com/v1/audio/transcriptions` | Bearer token, multipart `model` and `file`; the Telegram bot uses the returned transcript. Limits in this bot are 120 seconds and 18 MiB. |
| Google Places | `POST https://places.googleapis.com/v1/places:searchText` | Supply `X-Goog-Api-Key`, `X-Goog-FieldMask`, `Content-Type: application/json`. Use a facility type and city or a consented location bias. Render Google Maps attribution. Selected fields determine the billed SKU. |
| Database | PostgreSQL protocol via `asyncpg` | `DATABASE_URL` points to the application database. `pulse_users` and `medication_reminders` are internal tables. There is no public patient or database HTTP endpoint. |

The September source configures `gpt-5.6-luna` for text and web, `gpt-5.6-terra` for vision, and `gpt-4o-mini-transcribe` for transcription. Model authorization, billing and region apply to the actual API organization.

Example Places request body in the draft for a public city search. The September worker used the older `maxResultCount` field.

```json
{"textQuery":"pharmacy in Doha","languageCode":"en","pageSize":5}
```

[Telegram methods](https://core.telegram.org/bots/api) · [Stars](https://core.telegram.org/bots/payments-stars) · [Responses](https://developers.openai.com/api/reference/resources/responses/methods/create) · [Transcription](https://developers.openai.com/api/docs/guides/speech-to-text) · [Places](https://developers.google.com/maps/documentation/places/web-service/text-search) · [Attribution](https://developers.google.com/maps/documentation/places/web-service/policies)

## WhatsApp Cloud API

Use `https://graph.facebook.com/{GRAPH_VERSION}` with a system-user bearer token scoped to the approved business assets. Use current Graph versions supported by the Meta app. Each URL below is relative to that base.

| Method and route | Use |
|---|---|
| `POST /{PHONE_NUMBER_ID}/register` | Onboard the number. Complete the registration and business checks required by the account. |
| `POST /{WABA_ID}/subscribed_apps` | Subscribe the app to the business account after configuring its callback and messages field. |
| `POST /{PHONE_NUMBER_ID}/messages` | The included adapter replies to an eligible user within the service window. Use `recipient` for a BSUID, or `to` for an available phone number. |
| `GET /{WABA_ID}/message_templates`, `POST /{WABA_ID}/message_templates` | List or submit templates. Sending one outside the normal service window needs the right opt-in and approved template. Template sends are outside the included adapter. |
| `messages` webhook | Incoming messages contain provider IDs and recipient metadata. Status notifications can signal sent, delivered, read or failed. The support adapter acknowledges status-only events. It does not persist receipt analytics. |

Example user-initiated support response addressed to a BSUID

```json
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "recipient": "<BUSINESS_SCOPED_USER_ID>",
  "type": "text",
  "text": {"body":"Welcome to Pulse support. Type Account, Privacy or Human.","preview_url":false}
}
```

[Meta setup](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started) · [BSUID](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/) · [Sending](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages) · [Templates](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview/) · [Policy](https://business.whatsapp.com/policy)

## LINE Messaging API

Use a LINE channel access token against `https://api.line.me`. Use the channel secret separately to verify incoming messages. The support gateway calls the reply endpoint for active one-to-one events.

| Method and route | Use |
|---|---|
| `GET /v2/bot/info` | Retrieve the bot user ID for `LINE_DESTINATION_ID`. |
| `PUT /v2/bot/channel/webhook/endpoint`, `GET /v2/bot/channel/webhook/endpoint`, `POST /v2/bot/channel/webhook/test` | Configure, inspect and test the callback after enabling webhooks in OA Manager. |
| `POST /v2/bot/message/reply` | One-time reply token; use it promptly. Empty signed test callbacks receive HTTP 200. |
| `POST /v2/bot/message/validate/reply`, `POST /v2/bot/message/validate/push` | Validate the `messages` objects without a send. An API account is required. |
| `POST /v2/bot/message/push` | Optional future neutral reminders after opt-in. Supported push retries use an initial stable `X-Line-Retry-Key`. Do not put that header on ordinary reply calls. |
| `GET /v2/bot/message/quota`, `GET /v2/bot/message/quota/consumption` | Check the account quota and counted sends. |
| `POST /v2/bot/user/{userId}/linkToken` | Optional future account linking. A friend receives a one-time token with a ten-minute lifetime. Authenticate separately at Pulse and bind via the signed accountLink event and nonce. |

Example reply payload

```json
{
  "replyToken": "<REPLY_TOKEN_FROM_SIGNED_EVENT>",
  "messages": [{"type":"text","text":"Welcome to Pulse support. Type Account, Privacy or Human."}]
}
```

[Messaging API reference](https://developers.line.biz/en/reference/messaging-api/nojs/) · [Thai API terms](https://terms2.line.me/official_account_api_terms_th) · [Verifying signatures](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/) · [Linking accounts](https://developers.line.biz/en/docs/messaging-api/linking-accounts/)

## Weixin Service Account

The included adapter handles the initial verification GET and subsequent AES-safe XML events from the official account. It checks the receiving original account ID and the AppID in the encrypted envelope. A passive response travels in the incoming HTTP response, so no server access token or outbound API call is needed. Keep each support contact local to the intended deployment. Treat OpenID and 64-bit message IDs as opaque strings.

| Additional provider capability | When it applies |
|---|---|
| `POST https://api.weixin.qq.com/cgi-bin/stable_token` | For approved features that need an access token. JSON keys `grant_type: client_credential`, `appid`, `secret`. Cache and refresh server-side. |
| `POST https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token=<SERVER_TOKEN>` | Optional later customer-service reply. Body contains `touser` (scoped OpenID), `msgtype` and the corresponding message object. Apply trigger-specific windows and message caps. |

[Callback setup](https://developers.weixin.qq.com/doc/service/guide/dev/push/) · [Encryption](https://developers.weixin.qq.com/doc/service/guide/dev/push/encryption) · [Passive replies](https://developers.weixin.qq.com/doc/service/guide/product/message/Passive_user_reply_message) · [Server token](https://developers.weixin.qq.com/doc/service/api/base/api_getstableaccesstoken) · [Customer service sends](https://developers.weixin.qq.com/doc/service/api/customer/message/api_sendcustommessage)

## WeChat Customer Service through WeCom

This alternate design requires a separate WeCom tenant and service authorization. Its encrypted callback signals available work. It cannot be placed behind the Service Account adapter above.

| Method and route on `https://qyapi.weixin.qq.com` | Purpose |
|---|---|
| `GET /cgi-bin/gettoken` | Server-side token with the authorized corporate ID and customer-service secret. Keep credential-bearing URLs out of ingress and HTTP logs. |
| `POST /cgi-bin/kf/sync_msg?access_token=…` | On the `kf_msg_or_event` callback, pull from the durable cursor. Process `has_more` even if a page is empty. Deduplicate provider IDs and advance the cursor transactionally. |
| `POST /cgi-bin/kf/send_msg?access_token=…` | Use `touser`, `open_kfid`, `msgtype` and the content object under permitted conversation states. Monitor failure events. |

Normal personal WeChat groups and a company’s internal WeCom group robots are different products. The included support gateway does not read personal groups or run a personal-account automation client.

[WeCom callback](https://developer.work.weixin.qq.com/document/path/90930) · [Pull customer-service messages](https://developer.work.weixin.qq.com/document/path/94670) · [Send service messages](https://developer.work.weixin.qq.com/document/path/94677)
