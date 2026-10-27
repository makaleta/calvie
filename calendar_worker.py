"""One-shot calendar processing. Resource limits precede all third-party imports."""

import json
import sys


def response_chunks(payload):
    from datetime import datetime, time, timedelta

    import pytz
    from babel.dates import format_date, format_datetime, format_time, get_date_format, get_datetime_format, get_time_format
    from fastapi.encoders import jsonable_encoder
    from icalevents.icalevents import events
    from starlette.templating import Jinja2Templates

    tz = pytz.timezone(payload["timezone"])
    data = events(string_content=payload["content"], start=datetime.fromisoformat(payload["start"]),
                  end=datetime.fromisoformat(payload["end"]))
    if len(data) > payload["max_events"]:
        raise ValueError("Too many events")
    for event in data:
        if event.all_day:
            start_date, end_date = event.start.date(), event.end.date()
            if start_date == end_date:
                end_date += timedelta(days=1)
            event.start = tz.localize(datetime.combine(start_date, time.min))
            event.end = tz.localize(datetime.combine(end_date, time.min))
        else:
            event.astimezone(tz)
    if payload["format"] == "json":
        yield from json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(jsonable_encoder(data))
        return

    locale = payload["locale"]

    def localize(event):
        date_str = str(get_date_format("medium", locale=locale)).replace("MMMM", "MMM").replace("y", "").strip(", ")
        date_str = f"EEE {date_str}"
        time_str = str(get_time_format("short", locale=locale))
        datetime_str = str(get_datetime_format("long", locale=locale)).format(time_str, date_str)
        if event.all_day:
            end_date = event.end.date() - timedelta(days=1)
            start = format_date(event.start.date(), date_str, locale=locale)
            end = format_date(end_date, date_str, locale=locale)
            return start if event.start.date() == end_date else f"{start} - {end}"
        start = format_datetime(event.start, datetime_str, locale=locale, tzinfo=tz)
        if event.start.date() == event.end.date():
            end = format_time(event.end, time_str, locale=locale, tzinfo=tz)
        else:
            end = format_datetime(event.end, datetime_str, locale=locale, tzinfo=tz)
        return f"{start} - {end}"

    scheme = payload["color_scheme"]
    force_scheme = {"light": "white", "dark": "black"}.get(scheme) if scheme is not None else payload["colour"]
    minimal = [{"summary": event.summary, "interval": localize(event)} for event in sorted(data, key=lambda e: e.start)]
    template = Jinja2Templates(directory="templates").get_template("iframe.html")
    yield from template.generate(events=minimal, lang=locale, width=payload["width"],
                                 force_scheme=force_scheme, color_scheme=scheme)


def main():
    try:
        import resource

        memory_bytes = int(sys.argv[1])
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        payload = json.load(sys.stdin)
        total = 0
        for chunk in response_chunks(payload):
            encoded = chunk.encode("utf-8")
            total += len(encoded)
            if total > payload["max_response_bytes"]:
                return 2
            sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # Never serialize exceptions, calendar data, or tracebacks into the IPC response.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
