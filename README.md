# Pulse AI

Pulse AI is an English and Arabic Telegram health-information beta with Care Finder, confirmed medication reminders and Telegram Stars subscriptions.

The optional WhatsApp, LINE and Weixin Service Account adapters provide fixed account, privacy and human-support replies. Each adapter is disabled by default and runs in a separate process from the Telegram bot.

- [API inventory and provider examples](docs/api-inventory.md)
- [Channel setup, credentials and delivery behavior](docs/channel-gateway.md)
- [OpenAPI 3.1 callback contract](docs/openapi.channels.yaml)
- [Privacy notice](PRIVACY.md)

For the Telegram worker, install `requirements.txt`, configure its environment and start `python main.py`. Starting this worker connects to its database and begins polling for messages and due reminders. Use a dedicated test bot and synthetic database for staging. The separate support service runs with `python -m pulse_channels` and the dependencies in `pulse-channels-requirements.txt`.
