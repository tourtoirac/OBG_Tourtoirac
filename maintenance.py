"""
Maintenance of the site, announced by the administration of OBG (the Chabanas
/manage/maintenance/ page, or `manage.py maintenance` from deploy.sh) through
the administration API (admin_api.py).

Three phases:

    none       nothing planned
    announced  every screen shows a countdown to starts_at; no game can be
               created nor joined any more, only a player comes back to a seat of
               their game (the games already there go on)
    started    from starts_at: every session in memory is stored in Chabanas,
               every connection is closed (close code CLOSE_CODE), and the new
               ones are refused until the maintenance ends. The pages keep
               reconnecting and resume their game once it is over.

The announcement is the same for Hautefort (hautefort/maintenance.py): both
services get the same starts_at from Chabanas and start on their own.
"""
from datetime import UTC, datetime

NONE, ANNOUNCED, STARTED = "none", "announced", "started"
# the close code of the connections the start of a maintenance closes
CLOSE_CODE = 4012
MESSAGE_MAX = 300
DURATION_MAX = 24 * 60


class MaintenanceError(ValueError):
    """An announcement badly written: the administration API answers 400."""


def parse_starts_at(value) -> datetime:
    """
    :param value: an ISO 8601 date with its time zone, as Chabanas sends it
    :return: the date, in UTC
    :raise MaintenanceError: not such a date
    """
    if not isinstance(value, str):
        raise MaintenanceError("starts_at must be an ISO 8601 date")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MaintenanceError("starts_at must be an ISO 8601 date") from error
    if moment.tzinfo is None:
        raise MaintenanceError("starts_at needs its time zone")
    return moment.astimezone(UTC)


class Maintenance:
    """The maintenance planned, if any: what every screen is told."""

    def __init__(self):
        self.phase = NONE
        self.starts_at: datetime | None = None
        self.duration_minutes = 0
        self.message = ""

    def announce(self, starts_at, duration_minutes, message="") -> None:
        """
        :raise MaintenanceError: a field badly written; nothing changes then
        """
        moment = parse_starts_at(starts_at)
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int) \
                or not 1 <= duration_minutes <= DURATION_MAX:
            raise MaintenanceError(f"duration_minutes must be a number of minutes, 1 to {DURATION_MAX}")
        if message is None:
            message = ""
        if not isinstance(message, str) or len(message.strip()) > MESSAGE_MAX:
            raise MaintenanceError(f"message must be a text of at most {MESSAGE_MAX} characters")
        self.phase = ANNOUNCED
        self.starts_at = moment
        self.duration_minutes = duration_minutes
        self.message = message.strip()

    def start(self) -> None:
        self.phase = STARTED
        if self.starts_at is None or self.starts_at > datetime.now(UTC):
            # started at once, before the time announced (or never announced)
            self.starts_at = datetime.now(UTC)

    def clear(self) -> None:
        self.__init__()

    @property
    def announced(self) -> bool:
        return self.phase == ANNOUNCED

    @property
    def started(self) -> bool:
        return self.phase == STARTED

    def seconds_left(self) -> float:
        """Seconds until the start, 0 when the time is past."""
        if self.starts_at is None:
            return 0.0
        return max(0.0, (self.starts_at - datetime.now(UTC)).total_seconds())

    def state_json(self) -> dict:
        return {
            "phase": self.phase,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "duration_minutes": self.duration_minutes,
            "message": self.message,
        }
