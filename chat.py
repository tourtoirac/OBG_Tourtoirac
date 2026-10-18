"""
Text chat of a session: a general channel that everybody at the table reads
and writes in, and a private channel between each pair of players.

A message is a dict:
    {"id": 12, "from": "alice", "to": None, "text": "hello", "time": "..."}
"to" is None on the general channel, the nickname of the other player on a
private one. The ids grow with each message of the session, whatever its
channel: the client merges the channels it reads in that order.

The history is saved with the state of the session, so a resumed game keeps
its conversations.
"""
from datetime import datetime, timezone

# the longest message a player may send, in characters
MAX_TEXT_LENGTH = 500
# the messages kept on each channel: the older ones are forgotten
MAX_HISTORY = 200


def read_text(raw) -> str | None:
    """
    The text of a message as a player sent it, without the blanks around it.
    :return: None when it is not a string, is empty or too long
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > MAX_TEXT_LENGTH:
        return None
    return text


def read_message(raw) -> dict | None:
    """
    A message of a saved history, or None when it is malformed.
    """
    if not isinstance(raw, dict):
        return None
    message_id = raw.get('id')
    sender = raw.get('from')
    to = raw.get('to')
    text = raw.get('text')
    if not isinstance(message_id, int) or isinstance(message_id, bool):
        return None
    if not isinstance(sender, str) or not isinstance(text, str):
        return None
    if to is not None and not isinstance(to, str):
        return None
    time = raw.get('time')
    return {
        "id": message_id,
        "from": sender,
        "to": to,
        "text": text,
        "time": time if isinstance(time, str) else None,
    }


class ChatHistory:
    def __init__(self):
        self.general = []
        # by pair of nicknames, sorted: both players read the same channel
        self.private = {}
        self.next_id = 1

    @staticmethod
    def pair(first: str, second: str) -> tuple:
        return tuple(sorted((first, second)))

    @classmethod
    def from_json(cls, raw) -> 'ChatHistory':
        """
        Reads back the history saved by state_json(). Anything malformed is
        dropped: a broken history never prevents a game from resuming.
        """
        chat = cls()
        if not isinstance(raw, dict):
            return chat
        general = raw.get('general')
        if isinstance(general, list):
            chat.general = [m for m in map(read_message, general) if m is not None and m['to'] is None]
        private = raw.get('private')
        if isinstance(private, list):
            for channel in private:
                if not isinstance(channel, dict):
                    continue
                players = channel.get('players')
                messages = channel.get('messages')
                if (not isinstance(players, list) or len(players) != 2
                        or not all(isinstance(name, str) for name in players)
                        or players[0] == players[1] or not isinstance(messages, list)):
                    continue
                pair = cls.pair(*players)
                kept = [
                    m for m in map(read_message, messages)
                    if m is not None and m['to'] is not None and cls.pair(m['from'], m['to']) == pair
                ]
                if kept:
                    chat.private[pair] = kept[-MAX_HISTORY:]
        chat.general = chat.general[-MAX_HISTORY:]
        known = [m['id'] for m in chat.all_messages()]
        chat.next_id = max(known, default=0) + 1
        return chat

    def all_messages(self) -> list:
        messages = list(self.general)
        for channel in self.private.values():
            messages.extend(channel)
        return messages

    def post(self, sender: str, text: str, to: str | None = None) -> dict:
        """
        Adds a message to the general channel, or to the private channel of
        sender and to.
        :return: the message, as it is sent to the clients
        """
        message = {
            "id": self.next_id,
            "from": sender,
            "to": to,
            "text": text,
            "time": datetime.now(timezone.utc).isoformat(timespec='seconds'),
        }
        self.next_id += 1
        if to is None:
            channel = self.general
        else:
            channel = self.private.setdefault(self.pair(sender, to), [])
        channel.append(message)
        del channel[:-MAX_HISTORY]
        return message

    def readable_by(self, nickname: str | None) -> list:
        """
        The messages a reader may read, oldest first: the general channel,
        and the private channels it takes part in.
        :param nickname: None reads the general channel only
        """
        messages = list(self.general)
        if nickname is not None:
            for pair, channel in self.private.items():
                if nickname in pair:
                    messages.extend(channel)
        return sorted(messages, key=lambda message: message['id'])

    def is_empty(self) -> bool:
        return not self.general and not self.private

    def state_json(self) -> dict:
        return {
            "general": [dict(message) for message in self.general],
            "private": [
                {"players": list(pair), "messages": [dict(message) for message in channel]}
                for pair, channel in self.private.items()
            ],
        }
