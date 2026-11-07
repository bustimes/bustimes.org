import datetime

from django.utils.timezone import make_aware


FRACTIONS = ("", "¼", "½", "¾")


def format_timedelta(duration, plus_one=False, fractional=False):
    """Format a time since midnight as "HH:MM".

    Seconds are truncated, unless fractional=True, in which case the time is
    rounded down to the quarter minute, e.g. 09:00:40 -> "09:00½"
    """
    if duration is not None:
        duration = duration.total_seconds()
        hours = int(duration / 3600)
        after_midnight = 0
        while hours >= 24:
            hours -= 24
            after_midnight += 1
        minutes = int(duration % 3600 / 60)
        formatted = f"{hours:0>2}:{minutes:0>2}"
        if fractional:
            formatted += FRACTIONS[int(duration % 60 / 15)]
        if after_midnight and plus_one:
            formatted += "⁺¹"
        return formatted


def time_datetime(time, date, tzinfo=None):
    seconds = time.total_seconds()
    while seconds >= 86400:
        date += datetime.timedelta(1)
        seconds -= 86400
    time = datetime.time(
        int(seconds / 3600), int(seconds % 3600 / 60), int(seconds % 60)
    )
    combined = datetime.datetime.combine(date, time)
    return make_aware(combined, tzinfo)
