import os

from aiohttp import web

from .app import create_app
from .settings import Settings


if __name__ == "__main__":
    app = create_app(Settings.from_env())
    # Both official URL-verification protocols put verification data in the query.
    # Hosted ingress/observability must also disable query-string and body logging.
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), access_log=None, print=None)
