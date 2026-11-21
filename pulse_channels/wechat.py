"""Weixin Official Account callback, safe mode. No proactive send or access token."""
import base64
import hashlib
import hmac
import os
import secrets
import struct
from xml.etree import ElementTree as ET

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .menu import opted_out, render, selection
from .platforms import BadCallback, Unauthorized


def parse_xml(raw):
    try:
        text = raw.decode("utf-8-sig")
        # Official XML has no DTD or entity declarations.
        if "<!" in text.replace("<![CDATA[", ""):
            raise BadCallback
        doc = ET.fromstring(text)
    except (UnicodeDecodeError, ET.ParseError):
        raise BadCallback from None
    if doc.tag != "xml":
        raise BadCallback
    return doc


def xml_bytes(items):
    root = ET.Element("xml")
    for key, value in items.items():
        ET.SubElement(root, key).text = str(value)
    return ET.tostring(root, encoding="utf-8")


def signature(token, stamp, nonce, *payload):
    values = sorted((token, stamp, nonce, *payload))
    return hashlib.sha1("".join(values).encode()).hexdigest()  # Tencent's protocol signature


def verify(supplied, expected):
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise Unauthorized


def echo_parameters(query, now):
    stamp, nonce = query.get("timestamp", ""), query.get("nonce", "")
    if not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12 or not 1 <= len(nonce) <= 128:
        raise BadCallback
    if abs(now - int(stamp)) > 300:
        raise Unauthorized
    return stamp, nonce


def encrypt(data, config):
    packet = os.urandom(16) + struct.pack("!I", len(data)) + data + config.wx_app_id.encode()
    padder = padding.PKCS7(256).padder()  # Tencent specifies a 32-byte padding boundary.
    padded = padder.update(packet) + padder.finalize()
    worker = Cipher(algorithms.AES(config.wx_aes_key), modes.CBC(config.wx_aes_key[:16])).encryptor()
    return base64.b64encode(worker.update(padded) + worker.finalize()).decode()


def decrypt(encoded, config):
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
        worker = Cipher(algorithms.AES(config.wx_aes_key), modes.CBC(config.wx_aes_key[:16])).decryptor()
        padded = worker.update(ciphertext) + worker.finalize()
        unpadder = padding.PKCS7(256).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        if len(plain) < 20:
            raise ValueError
        length = struct.unpack("!I", plain[16:20])[0]
        if not hmac.compare_digest(plain[20 + length:], config.wx_app_id.encode()):
            raise ValueError
        return plain[20:20 + length]
    except (ValueError, UnicodeError):
        raise Unauthorized from None


def challenge(query, config, now):
    stamp, nonce = echo_parameters(query, now)
    echo = query.get("echostr", "")
    if not 1 <= len(echo) <= 4096:
        raise BadCallback
    if query.get("msg_signature") or query.get("encrypt_type") == "aes":
        verify(query.get("msg_signature"), signature(config.wx_token, stamp, nonce, echo))
        return decrypt(echo, config)
    # Official Account ownership checks can use the three-parameter SHA-1
    # signature even when subsequent message callbacks require safe mode.
    verify(query.get("signature"), signature(config.wx_token, stamp, nonce))
    return echo.encode()


def passive_reply(raw, query, config, now):
    stamp, nonce = echo_parameters(query, now)
    if query.get("encrypt_type") != "aes":
        raise Unauthorized
    outer = parse_xml(raw)
    encoded = outer.findtext("Encrypt") or ""
    if not encoded:
        raise BadCallback
    verify(query.get("msg_signature"), signature(config.wx_token, stamp, nonce, encoded))
    message = parse_xml(decrypt(encoded, config))
    if message.findtext("ToUserName") != config.wx_original_id:
        raise Unauthorized
    sender = message.findtext("FromUserName")
    if not sender or len(sender) > 256:
        raise BadCallback
    kind = message.findtext("MsgType")
    if kind == "event":
        event = (message.findtext("Event") or "").lower()
        if event == "subscribe":
            language, action = "zh", "menu"
        elif event == "click":
            value = message.findtext("EventKey")
            if opted_out(value):
                return None
            language, action = selection(value, "zh")
        else:
            return None
    elif kind in {"text", "image", "voice", "video", "shortvideo", "location", "link", "file"}:
        value = message.findtext("Content") if kind == "text" else None
        if opted_out(value):
            return None
        language, action = selection(value, "zh")
    else:
        return None
    contact = config.wx_contact
    if config.wx_portal:
        contact += " · " + config.wx_portal
    content = render(language, action, contact)
    reply = xml_bytes({"ToUserName": sender, "FromUserName": config.wx_original_id,
                       "CreateTime": int(now), "MsgType": "text", "Content": content})
    cipher = encrypt(reply, config)
    reply_nonce, reply_stamp = secrets.token_hex(12), str(int(now))
    return xml_bytes({"Encrypt": cipher, "MsgSignature": signature(config.wx_token, reply_stamp, reply_nonce, cipher),
                      "TimeStamp": reply_stamp, "Nonce": reply_nonce})
