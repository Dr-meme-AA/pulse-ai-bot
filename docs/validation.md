# Pulse AI offline validation

Tests use synthetic people and messages. The messaging gateway uses local HTTP and mocked outbound sends. The Telegram matrix decodes real Telegram updates with python-telegram-bot 22.8 and intercepts billing, database, Maps, model and messaging boundaries. It records the selected handler and when a user sees an instruction. These runs do not measure medical accuracy.

| Check | Result on this branch |
|---|---|
| Repository tests | 72 passed: 44 messaging gateway, 19 Telegram routing, 9 Care Finder. Also passed with PTB_TIMEDELTA=1. |
| Gateway replay | 300 synthetic events, each repeated once. All 600 local HTTP callbacks returned 200. Exactly 200 WhatsApp/LINE submissions reached the mock sender. Weixin returned 200 decryptable passive responses, one per request, including repeats. No provider network calls. |
| Original 22 Telegram routing scenarios | 19 expected routes, up from 10 on the September source. Three remaining cases request Thai medical handling. |
| Second 18-scenario set | 14 expected routes. All eight English/Arabic urgent examples matched after revision. Four urgent Thai examples received a general English/Arabic instruction. These scenarios informed the revision. |
| Independently fixed 24-scenario set | 15 urgent messages at the allowance cap, 9 nonurgent controls. Direct emergency routing matched 3/5 English, 1/5 Gulf Arabic and 0/5 Thai urgent items. The other 11 urgent items received the separate emergency instruction before billing. Three nonurgent messages triggered the emergency route. |
| Same 15 urgent messages with allowance available | Four took the emergency shortcut. All 11 others received an immediate English/Arabic safety instruction before general model processing. They still count as detection misses. |
| Eight modality scenarios | All eight followed the expected route. All seven paths that accessed a model, transcription, media or Maps first sent the bilingual safety instruction. The urgent image caption completed without fetching the image. |

There are two different protections. Recognized urgent input immediately produces emergency guidance. An unrecognized question with available allowance can proceed to a model after a generic instruction, and an exhausted account gets that instruction before its allowance notice. The normal quota database read can precede the generic message. Thai and Mandarin fixed support menus do not imply clinical language coverage.

The novel fixture file was fixed before the final evaluation. Its SHA-256 is `7ba78c9be3e4ae070f824bb626a2938d196ccc015f0d969b634d789c5b061fcb`. It is now available for regression tests. Future estimates require newly held-out cases and licensed, native-language reviewers.

## Reproduce

Run from the repository root with Python 3.12 and the dependencies in both requirements files. Tests set up their own fixture configuration.

```sh
python -m unittest discover -s tests
PYTHONPATH=. python tests/run_channel_trials.py
python tests/run_telegram_trials.py main.py --set baseline
python tests/run_telegram_trials.py main.py --set held-out
python tests/run_telegram_trials.py main.py --set fresh
python tests/run_telegram_trials.py main.py --set fresh-open-urgent
python tests/run_telegram_trials.py main.py --set open-modalities
```

The Telegram runner prints a JSON outcome per case. A deliberate mismatch is evidence about the product. Review counts and outcome fields separately from the script’s execution status.

## Release evidence still needed

Obtain provider account approval, complete the real account callback handshake, test an authorized device in each target market, and verify a human can receive and answer the handoff. Clinical reviewers, a tested consent and erasure workflow, and a restored staging database are needed before enabling a health service for users. The gateway runs only static support.

The existing Telegram scheduler and payments are outside the gateway change. Separate baseline trials found subsequent reminder times could drift after snooze/retry, a snoozed notification could remain due after Taken, and a preview could disclose medication details. Test occurrence IDs, reminder cancellation, payment-event deduplication and subscription restoration before reactivating those features.
