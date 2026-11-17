"""Convert snapd timer strings into systemd calendar events.

Each snapd event ("mon-fri,10:00", "9:00~11:00", "00:00-24:00/96"...) becomes one or
more OnCalendar values; time windows use RandomizedDelaySec, as snapd does."""

import re

from .localization import tr

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SYSTEMD_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY = re.compile(r"(mon|tue|wed|thu|fri|sat|sun)([1-5])?")
TIME = re.compile(r"([0-9]{1,2}):([0-9]{2})")
MAX_EVENTS = 1440


def _minutes(text):
    match = TIME.fullmatch(text)
    if not match:
        raise ValueError(text)
    hours, minutes = int(match[1]), int(match[2])
    if hours > 24 or minutes > 59 or (hours == 24 and minutes):
        raise ValueError(text)
    return hours * 60 + minutes


def _days(item):
    """(systemd weekday text, systemd day-of-month text) for a day or a span of days."""
    first, _dash, last = item.partition("-")
    start, end = DAY.fullmatch(first), DAY.fullmatch(last) if last else None
    if not start or (last and not end):
        raise ValueError(item)
    week = start[2] or (end[2] if end else None)
    if (start[2] and end and end[2] != start[2]) or week == "5":
        # The fifth week (snapd's "last") has no calendar equivalent here.
        raise ValueError(item)
    days = SYSTEMD_DAYS[DAYS.index(start[1])]
    if end:
        days += ".." + SYSTEMD_DAYS[DAYS.index(end[1])]
    month_day = "*"
    if week:
        n = int(week)
        month_day = f"{(n - 1) * 7 + 1:02d}..{n * 7:02d}"
    return days, month_day


def _times(item):
    """[(start minute, window length in minutes)] for a time or a window."""
    spread = ""
    if "/" in item:
        item, spread = item.split("/", 1)
    if "~" in item or "-" in item:
        start_text, end_text = re.split(r"[~-]", item, maxsplit=1)
        start, end = _minutes(start_text), _minutes(end_text)
        if end <= start:
            end += 24 * 60  # A window over midnight.
        if spread:
            count = int(spread) if spread.isdigit() else 0
            if not 0 < count <= MAX_EVENTS:
                raise ValueError(spread)
            step = (end - start) / count
            return [(int(start + step * index), 0) for index in range(count)]
        return [(start, end - start)]
    if spread:
        raise ValueError(item)
    return [(_minutes(item), 0)]


def calendar(text):
    """(OnCalendar values, randomized delay in seconds) for snapd's timer text.
    Raises ValueError for what cannot be translated."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(tr("The timer is empty."))
    values, window = [], 0
    for event in text.split(",,"):
        days, times = [], []
        for item in event.split(","):
            item = item.strip()
            if not item:
                raise ValueError(tr("The timer {0} is not understood.").format(text))
            try:
                if item[0].isdigit():
                    times.extend(_times(item))
                else:
                    days.append(_days(item))
            except (ValueError, IndexError):
                raise ValueError(tr("The timer {0} is not understood.").format(text)) from None
        if not times:
            times = [(0, 24 * 60)]  # A day alone: once, some time that day.
        for weekday, month_day in days or [("", "*")]:
            for start, length in times:
                start %= 24 * 60
                moment = f"{start // 60:02d}:{start % 60:02d}:00"
                prefix = weekday + " " if weekday else ""
                values.append(f"{prefix}*-*-{month_day} {moment}")
                window = max(window, length * 60 - 1 if length else 0)
    values = list(dict.fromkeys(values))
    if len(values) > MAX_EVENTS:
        raise ValueError(tr("The timer {0} runs too often.").format(text))
    return values, window
