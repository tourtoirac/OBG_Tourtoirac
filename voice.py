"""
Voice chat between the players of a session.

The audio never goes through Tourtoirac: the browsers send it to each other
with WebRTC. The server only relays the messages they need to find each other
(offers, answers, ICE candidates: voice_signal) and tells who is in the voice
channel of the session (voice_members).

The browsers also need ICE servers to cross the NATs. They are read from the
environment, so that the TURN relay of the production server is not written
in the code:
- OBG_STUN_URLS: STUN urls, comma separated (a public Google server by default,
  an empty value for none)
- OBG_TURN_URLS: TURN urls, comma separated (none by default)
- OBG_TURN_SECRET: the static-auth-secret of the coturn server; the password
  of each player is derived from it and expires (OBG_TURN_TTL seconds, one day
  by default), so no lasting password is sent to the browsers
"""
import base64
import hashlib
import hmac
import os
import time

DEFAULT_STUN_URLS = "stun:stun.l.google.com:19302"
DEFAULT_TURN_TTL = 24 * 3600

# the largest signal relayed, once encoded: an offer with its candidates is a
# few kilobytes
MAX_SIGNAL_SIZE = 64 * 1024


def _urls(name: str, default: str = "") -> list:
    value = os.environ.get(name, default)
    return [url.strip() for url in value.split(",") if url.strip()]


def turn_credentials(nickname: str, secret: str, ttl: int, now: float | None = None) -> tuple:
    """
    Time-limited TURN credentials, as coturn checks them with use-auth-secret:
    the username is "<expiry timestamp>:<nickname>" and the password the
    base64 HMAC-SHA1 of that username.
    :return: (username, credential)
    """
    expiry = int((time.time() if now is None else now) + ttl)
    username = f"{expiry}:{nickname}"
    digest = hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode("ascii")


def ice_servers(nickname: str) -> list:
    """
    The ICE servers a player's browser connects through, in the shape of
    RTCConfiguration.iceServers.
    """
    servers = []
    stun = _urls("OBG_STUN_URLS", DEFAULT_STUN_URLS)
    if stun:
        servers.append({"urls": stun})
    turn = _urls("OBG_TURN_URLS")
    secret = os.environ.get("OBG_TURN_SECRET", "")
    if turn and secret:
        try:
            ttl = int(os.environ.get("OBG_TURN_TTL", DEFAULT_TURN_TTL))
        except ValueError:
            ttl = DEFAULT_TURN_TTL
        username, credential = turn_credentials(nickname, secret, ttl)
        servers.append({"urls": turn, "username": username, "credential": credential})
    return servers
