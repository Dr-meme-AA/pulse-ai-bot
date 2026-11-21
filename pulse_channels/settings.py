import base64
import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, repr=False)
class Settings:
    enabled: frozenset[str]
    state_dir: Path | None = None
    state_key: bytes = b""
    support_url: str = ""
    line_secret: str = ""
    line_access_token: str = ""
    line_destination: str = ""
    wa_app_secret: str = ""
    wa_verify_token: str = ""
    wa_access_token: str = ""
    wa_phone_id: str = ""
    wa_business_id: str = ""
    wa_graph_version: str = ""
    wx_token: str = ""
    wx_app_id: str = ""
    wx_original_id: str = ""
    wx_aes_key: bytes = b""
    wx_contact: str = ""
    wx_portal: str = ""

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env

        def required(name):
            value = env.get(name, "").strip()
            if not value:
                raise ValueError(f"Missing required setting {name}")
            return value

        def portal(name):
            value = required(name)
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            try:
                public_host = ipaddress.ip_address(host).is_global
            except ValueError:
                labels = host.rstrip(".").split(".")
                public_host = (len(labels) > 1 and labels[-1] not in {"localhost", "local", "internal"}
                               and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                                       for part in labels))
            if (parsed.scheme != "https" or not public_host or parsed.port == 0
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or any(c.isspace() for c in value)
                    or "\\" in value):
                raise ValueError(f"{name} must be a public HTTPS page without credentials, query, or fragment")
            return value

        def contact(name):
            value = required(name)
            email = re.fullmatch(r"[\w.+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", value)
            phone = re.fullmatch(r"\+?[0-9][0-9 ()-]{4,30}", value)
            if len(value) > 128 or not (email or phone):
                raise ValueError(f"{name} must be a monitored support phone number or email address")
            return value

        def key(name, bare=False):
            value = required(name)
            try:
                raw = base64.b64decode(value + ("=" if bare else ""), validate=True)
            except ValueError:
                raw = b""
            if len(raw) != 32 or (bare and len(value) != 43):
                raise ValueError(f"{name} must encode 32 bytes")
            return raw

        enabled = set()
        for channel in ("line", "whatsapp", "wechat"):
            name = f"PULSE_{channel.upper()}_ENABLED"
            value = env.get(name, "false").strip().lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{name} expects true or false")
            if value == "true":
                enabled.add(channel)
        args = {"enabled": frozenset(enabled)}
        if enabled & {"whatsapp", "line"}:
            path = Path(required("PULSE_CHANNEL_STATE_DIR"))
            if not path.is_absolute():
                raise ValueError("PULSE_CHANNEL_STATE_DIR must be an absolute persistent directory")
            args.update(state_dir=path, state_key=key("PULSE_CHANNEL_STATE_KEY"),
                        support_url=(portal("PULSE_SUPPORT_URL") if env.get("PULSE_SUPPORT_URL")
                                     else contact("PULSE_SUPPORT_CONTACT")))
        if "line" in enabled:
            args.update(line_secret=required("LINE_CHANNEL_SECRET"),
                        line_access_token=required("LINE_CHANNEL_ACCESS_TOKEN"),
                        line_destination=required("LINE_DESTINATION_ID"))
        if "whatsapp" in enabled:
            version = required("WA_GRAPH_VERSION")
            phone, business = required("WA_PHONE_NUMBER_ID"), required("WA_BUSINESS_ACCOUNT_ID")
            if not re.fullmatch(r"v\d+\.\d+", version) or not all(re.fullmatch("[0-9]+", v) for v in (phone, business)):
                raise ValueError("Invalid WhatsApp Graph version or account identifier")
            args.update(wa_app_secret=required("WA_APP_SECRET"), wa_verify_token=required("WA_VERIFY_TOKEN"),
                        wa_access_token=required("WA_ACCESS_TOKEN"), wa_phone_id=phone,
                        wa_business_id=business, wa_graph_version=version)
        if "wechat" in enabled:
            args.update(wx_token=required("WECHAT_CALLBACK_TOKEN"), wx_app_id=required("WECHAT_APP_ID"),
                        wx_original_id=required("WECHAT_ORIGINAL_ID"), wx_aes_key=key("WECHAT_ENCODING_AES_KEY", True),
                        wx_contact=contact("WECHAT_SUPPORT_CONTACT"),
                        wx_portal=portal("WECHAT_APPROVED_LOCAL_SUPPORT_URL") if env.get("WECHAT_APPROVED_LOCAL_SUPPORT_URL") else "")
        return cls(**args)
