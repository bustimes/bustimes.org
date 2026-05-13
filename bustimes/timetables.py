import datetime
import graphlib
from dataclasses import dataclass
from difflib import Differ
from functools import cached_property, cmp_to_key, partial

from django.conf import settings
from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Prefetch, Q, Value
from django.utils.html import format_html
from django.utils.timezone import localdate
from sql_util.utils import Exists

from .formatting import format_timedelta
from .models import Calendar, Note, StopTime, Trip
from .utils import get_calendars, get_descriptions, get_routes

differ = Differ(charjunk=lambda _: True)


def compare_trips(rows, trip_ids, a, b):
    a_time = None
    b_time = None

    a_top = rows.index(a.top)
    a_bottom = rows.index(a.bottom)
    b_top = rows.index(b.top)
    b_bottom = rows.index(b.bottom)

    a_index = trip_ids.index(a.id)
    b_index = trip_ids.index(b.id)

    for row in rows[max(a_top, b_top) : min(a_bottom, b_bottom) + 1]:
        if row.times[a_index] and row.times[b_index]:
            a_time = row.times[a_index].departure_or_arrival()
            b_time = row.times[b_index].departure_or_arrival()
            return (a_time - b_time).total_seconds()

    if a_top > b_bottom:  # b is above a
        a_time = a.start
        b_time = b.end
    elif b_top > a_bottom:  # a is above b
        a_time = a.end
        b_time = b.start
    else:
        a_time = a.start
        b_time = b.start

    if a_time and b_time:
        return (a_time - b_time).total_seconds()

    return 0


class Timetable:
    def __init__(self, routes, date, calendar_id=None, detailed=False, operators=None):
        self.today = localdate()

        self.operators = operators

        routes = list(routes.order_by("id").select_related("source"))
        self.routes = self.current_routes = routes
        # self.current_routes is a subset of self.routes
        self.yesterday = None
        self.yesterday_routes = []

        self.date = date
        self.detailed = detailed

        self.groupings = [Grouping(False, self), Grouping(True, self)]
        self.calendar_options = None

        self.calendar = None
        # self.start_date = None
        if not self.routes:
            self.calendars = None
            return

        four_weeks_time = self.today + datetime.timedelta(days=28)

        scotland = any(route.source.name == "S" for route in routes)

        self.calendars = list(
            Calendar.objects.filter(Exists("trip", filter=Q(route__in=self.routes)))
            .annotate(
                bank_holiday_inclusions=ArrayAgg(
                    "calendarbankholiday__bank_holiday__bankholidaydate__date",
                    filter=Q(
                        calendarbankholiday__operation=True,
                        calendarbankholiday__bank_holiday__bankholidaydate__date__range=[
                            self.today,
                            four_weeks_time,
                        ],
                    ),
                    default=[],
                ),
                bank_holiday_exclusions=ArrayAgg(
                    "calendarbankholiday__bank_holiday__bankholidaydate__date",
                    filter=Q(
                        calendarbankholiday__operation=False,
                        calendarbankholiday__bank_holiday__bankholidaydate__date__range=[
                            self.today,
                            four_weeks_time,
                        ],
                    ),
                    default=[],
                ),
            )
            .prefetch_related("calendardate_set")
        )

        if not self.date and self.calendars:
            if len(self.calendars) == 1:
                calendar = self.calendars[0]
                # calendar has a summary like 'school days only', or no exceptions within 28 days
                if calendar.is_sufficiently_simple(self.today, four_weeks_time):
                    self.calendar = calendar
                    # if calendar.start_date > self.today:  # starts in the future
                    #     self.start_date = calendar.start_date

                    #     # in case a Friday only service has a start_date that's a Sunday, for example:
                    #     for date in self.get_date_options():
                    #         self.start_date = date
                    #         break

            else:
                self.get_calendar_options(calendar_id)

        if self.calendars and not self.calendar:
            self.date_options = list(self.get_date_options())
            if not self.date:
                if self.date_options:
                    self.date = self.date_options[0]
                else:
                    self.date = self.today

        # consider revision numbers:
        if self.date:
            self.current_routes = get_routes(routes, self.date)
            self.yesterday = self.date - datetime.timedelta(days=1)
            self.yesterday_routes = get_routes(routes, self.yesterday)

        if not self.calendar:
            if self.calendars:
                calendar_ids = [calendar.id for calendar in self.calendars]
                self.calendar_ids = list(
                    get_calendars(
                        self.date, calendar_ids, scotland=scotland
                    ).values_list("id", flat=True)
                )
                self.yesterday_calendar_ids = list(
                    get_calendars(
                        self.yesterday, calendar_ids, scotland=scotland
                    ).values_list("id", flat=True)
                )

    def correct_directions(self, trips):
        # for merged multi-operator routes: reverse the polarity if they disagree which direction is inbound/outbound
        stops = {}  # stops by source and direction
        for trip in trips:
            if trip.operator_id not in stops:
                stops[trip.operator_id] = {
                    True: set(),  # inbound
                    False: set(),  # outbound
                }
            stops[trip.operator_id][trip.inbound].update(
                stop.stop_id for stop in trip.times
            )

        if len(stops) == 2:
            operator_a, operator_b = stops

            if (
                len(stops[operator_a][True] & stops[operator_b][False])
                > len(stops[operator_a][True] & stops[operator_b][True])
            ) and (
                len(stops[operator_a][False] & stops[operator_b][True])
                > len(stops[operator_a][False] & stops[operator_b][False])
            ):
                for trip in trips:
                    if trip.operator_id == operator_a:
                        trip.inbound = not trip.inbound

    def render(self):
        one_day = datetime.timedelta(days=1)

        today_q = Q(route__in=self.current_routes)
        yesterday_q = Q(route__in=self.yesterday_routes, start__gte=one_day)

        if not self.calendar:
            if self.calendars:
                today_q &= Q(calendar__in=self.calendar_ids) | Q(calendar=None)
                yesterday_q &= Q(calendar__in=self.yesterday_calendar_ids) | Q(
                    calendar=None
                )
                # today_calendar_ids = set(self.calendar_ids) | {None}
            else:
                today_q &= Q(calendar=None)
                yesterday_q &= Q(calendar=None)
                # today_calendar_ids = {None}
        elif self.calendar_options:
            today_q &= Q(calendar=self.calendar)
            if self.yesterday and self.calendar.allows(self.yesterday):
                yesterday_q &= Q(calendar=self.calendar)
            else:
                yesterday_q = Q(pk__in=[])
        elif self.yesterday and not self.calendar.allows(self.yesterday):
            yesterday_q = Q(pk__in=[])

        prefetch_times = Prefetch(
            "stoptime_set",
            queryset=StopTime.objects.annotate(note_ids=ArrayAgg("notes"))
            .filter(Q(pick_up=True) | Q(set_down=True))
            .order_by("trip_id", "id"),
            to_attr="times",
        )
        prefetch_notes = Prefetch(
            "notes", queryset=Note.objects.annotate(stoptimes=Exists("stoptime"))
        )
        trips = Trip.objects.prefetch_related(prefetch_times, prefetch_notes)

        if self.detailed:
            trips = trips.select_related("garage", "vehicle_type")

        trips = (
            trips.filter(yesterday_q)
            .annotate(yesterday=Value(True))
            .union(trips.filter(today_q).annotate(yesterday=Value(False)))
        )

        if len(trips) > 1500:
            self.date = None
            return

        routes = {route.id: route for route in self.current_routes}
        for route in self.yesterday_routes:
            routes.setdefault(route.id, route)

        for trip in trips:
            trip.route = routes[trip.route_id]
            if trip.yesterday:
                # yesterday's after-midnight trip: shift back 24h so it
                # appears at its actual real-world time today
                trip.start -= one_day
                trip.end -= one_day
                for stoptime in trip.times:
                    if stoptime.arrival is not None:
                        stoptime.arrival -= one_day
                    if stoptime.departure is not None:
                        stoptime.departure -= one_day

        if len(self.current_routes) > 1:
            self.correct_directions(trips)

        for trip in trips:
            # split inbound and outbound trips into lists
            if trip.inbound:
                self.groupings[1].trips.append(trip)
            else:
                self.groupings[0].trips.append(trip)

            # stop-specific notes
            for note in trip.notes.all():
                if note.stoptimes:
                    for stoptime in trip.times:
                        if note.id in stoptime.note_ids:
                            stoptime.note = note

        del trips

        for grouping in self.groupings:
            if not self.detailed:
                grouping.trips.sort(key=lambda t: t.start)
                grouping.merge_split_trips()

            grouping.sort_rows()

            # build the table
            for trip in grouping.trips:
                grouping.handle_trip(trip)

            grouping.sort_columns()

            grouping.do_heads_and_feet(self.detailed)

        (
            self.inbound_outbound_descriptions,
            self.origins_and_destinations,
        ) = get_descriptions(self.current_routes)

        self.groupings = [grouping for grouping in self.groupings if grouping.rows]

        if all(
            route.line_name == self.routes[0].line_name for route in self.routes[1:]
        ):
            for grouping in self.groupings:
                del grouping.routes

        self.apply_stops()

        # correct origin and destination/inbound and outbound descriptions being the wrong way round
        # if self.groupings and len(self.origins_and_destinations) == 1:
        #     rows = self.groupings[0].rows
        #     if type(rows[0].stop) is Stop or type(rows[-1].stop) is Stop:
        #         pass
        #     else:
        #         origin = self.origins_and_destinations[0][0]
        #         destination = self.origins_and_destinations[0][-1]
        #         actual_origin = rows[0].stop.get_qualified_name()
        #         actual_destination = rows[-1].stop.get_qualified_name()
        #         if (
        #             SequenceMatcher(None, destination, actual_destination).quick_ratio()
        #             + SequenceMatcher(None, origin, actual_origin).quick_ratio()
        #             <
        #             SequenceMatcher(None, destination, actual_origin).quick_ratio()
        #             + SequenceMatcher(None, origin, actual_destination).quick_ratio()
        #         ):
        #             self.origins_and_destinations = [
        #                 tuple(reversed(pair)) for pair in self.origins_and_destinations
        #             ]
        #             self.inbound_outbound_descriptions = [
        #                 tuple(reversed(pair))
        #                 for pair in self.inbound_outbound_descriptions
        #             ]

        return self

    def any_trip_has(self, attr: str) -> bool:
        for grouping in self.groupings:
            for trip in grouping.trips:
                if getattr(trip, attr):
                    return True
        return False

    def apply_stops(self, stop_situations=None):
        stop_codes = (
            row.stop.atco_code for grouping in self.groupings for row in grouping.rows
        )
        stops = (
            StopTime.stop.field.related_model.objects.select_related("locality")
            .order_by()
            .defer("latlong", "locality__latlong")
            .in_bulk(stop_codes)
        )

        if stop_situations and len(stop_situations) < len(stops):
            for atco_code in stops:
                if atco_code in stop_situations:
                    if stop_situations[atco_code].summary == "Does not stop here":
                        stops[atco_code].suspended = True
                    else:
                        stops[atco_code].situation = True

        for grouping in self.groupings:
            grouping.apply_stops(stops)

    def has_multiple_operators(self) -> bool:
        if self.operators and len(self.operators) > 1:
            return True
        prev_op = None
        for grouping in self.groupings:
            for trip in grouping.trips:
                if trip.operator_id:
                    if prev_op and prev_op != trip.operator_id:
                        return True
                    prev_op = trip.operator_id

    def get_calendar_options(self, calendar_id):
        all_days = set()
        for calendar in self.calendars:
            calendar_days = calendar.get_days()
            if calendar_days and all_days.isdisjoint(calendar_days):
                all_days = all_days.union(calendar_days)
            else:
                return  # some overlap between calendar days, too complicated

            for calendar_date in calendar.calendardate_set.all():
                if calendar.end_date and calendar_date.end_date >= calendar.end_date:
                    continue
                return  # exceptions or extra days, too complicated

        for calendar in self.calendars:
            if calendar.id == calendar_id:
                self.calendar = calendar
            elif calendar_id is None and calendar.allows(self.today):
                self.calendar = calendar

        calendar_options = list(self.calendars)
        calendar_options.sort(key=Calendar.get_order)
        if not self.calendar:
            self.calendar = calendar_options[0]
        self.calendar_options = [
            (calendar.id, calendar.describe_for_timetable(self.today))
            for calendar in calendar_options
        ]

    def get_date_options(self):
        date = self.today

        for calendar in self.calendars:
            for calendar_date in calendar.calendardate_set.all():
                if (
                    calendar_date.operation is False
                    and calendar_date.contains(date)
                    and calendar.end_date
                ):
                    # fast-forward to end of current period of non-operation
                    calendar.start_date = calendar_date.end_date + datetime.timedelta(
                        days=1
                    )

        start_dates = [calendar.start_date for calendar in self.calendars]
        if start_dates:
            date = max(date, min(start_dates))

        end_date = date + datetime.timedelta(days=21)
        end_dates = [route.end_date for route in self.routes]
        if end_dates and all(end_dates):
            end_date = min(
                end_date, max(end_dates)
            )  # 21 days in the future, or the end date, whichever is sooner

            if end_date < date:  # allow users to select past dates
                self.expired = end_date
                if not self.date:
                    self.date = date
                date = end_date - datetime.timedelta(days=7)

        if self.date and self.date < date:
            yield self.date
        while date <= end_date:
            if (
                any(calendar.allows(date) for calendar in self.calendars)
                or date == self.date
            ):
                yield date
            date += datetime.timedelta(days=1)
        if self.date and self.date >= date:
            yield self.date

    def credits(self):
        credits = (route.source.credit(route) for route in self.current_routes)
        return set(credit for credit in credits if credit)


@dataclass
class Repetition:
    """Represents a special cell in a timetable, spanning multiple rows and columns,
    with some text like 'then every 5 minutes until'.
    """

    colspan: int
    duration: datetime.timedelta

    def __str__(self):
        # cleverly add non-breaking spaces if there aren't many rows
        if self.duration.seconds == 3600:
            if self.min_height < 3:
                return "then\u00a0hourly until"
            return "then hourly until"
        if self.duration.seconds % 3600 == 0:
            duration = "{} hours".format(int(self.duration.seconds / 3600))
        else:
            duration = "{} minutes".format(int(self.duration.seconds / 60))
        if self.min_height < 3:
            return "then\u00a0every {}\u00a0until".format(
                duration.replace(" ", "\u00a0")
            )
        if self.min_height < 4:
            return "then every\u00a0{} until".format(duration.replace(" ", "\u00a0"))
        return "then every {} until".format(duration)


def abbreviate(grouping, i, in_a_row, difference):
    """Given a Grouping, and a timedelta, modify each row and..."""
    seconds = difference.total_seconds()
    if not seconds:  # remove duplicates
        for j in range(i - in_a_row - 2, i):
            for row in grouping.rows:
                row.times[j] = None
        return
    if not settings.ABBREVIATE_HOURLY:
        return
    if (
        in_a_row < 4
        and not settings.ABBREVIATE_HOURLY
        or 3600 % seconds
        or seconds > 1800
        and not (settings.ABBREVIATE_HOURLY and seconds == 3600)
    ):
        # interval more than 30 minutes
        return
    repetition = Repetition(in_a_row + 1, difference)
    grouping.rows[0].times[i - in_a_row - 2] = (
        repetition  # replace top left cell with [[then every] colspan= rowspan=]
    )
    for j in range(
        i - in_a_row - 1, i - 1
    ):  # blank (in_a_row - 1) other cells from top row
        grouping.rows[0].times[j] = None
    for j in range(
        i - in_a_row - 2, i - 1
    ):  # remove (in_a_row) cells from each row below the top row
        for row in grouping.rows[1:]:
            row.times[j] = None


def journey_patterns_match(trip_a, trip_b):
    if trip_a.route_id != trip_b.route_id or trip_a.operator_id != trip_b.operator_id:
        return False
    if trip_a.journey_pattern:
        if trip_a.journey_pattern == trip_b.journey_pattern:
            if trip_a.destination_id == trip_b.destination_id:
                if trip_a.end - trip_a.start == trip_b.end - trip_b.start:
                    return True
    return False


class Grouping:
    def __init__(self, inbound: bool, parent: Timetable):
        self.routes = []
        self.rows = []
        self.trips = []
        self.inbound = inbound
        self.column_feet = {}
        self.parent = parent

    def __str__(self):
        if self.parent.inbound_outbound_descriptions:
            if self.inbound:
                descriptions = (
                    pair[1] for pair in self.parent.inbound_outbound_descriptions
                )
            else:
                descriptions = (
                    pair[0] for pair in self.parent.inbound_outbound_descriptions
                )
            return "\n".join(descriptions)

        if self.parent.origins_and_destinations:
            partses = self.parent.origins_and_destinations
            if self.inbound:
                partses = [reversed(parts) for parts in partses]
            return "\n".join([" - ".join(parts) for parts in partses])

        if headsigns := set(trip.headsign for trip in self.trips if trip.headsign):
            return f"To {' or '.join(headsigns)}"

        if self.inbound:
            return "Inbound"
        return "Outbound"

    def txt(self):
        width = max(len(str(row.stop)) for row in self.rows)
        return "\n".join(
            f"{str(row.stop):<{width}}  {'  '.join(str(time) or '     ' for time in row.times)}"
            for row in self.rows
        )

    def has_minor_stops(self):
        return any(row.is_minor() for row in self.rows)

    def has_major_stops(self):
        return any(not row.is_minor() for row in self.rows)

    def has_set_down_only(self):
        for row in self.rows:
            for cell in row.times:
                if type(cell) is Cell and cell.set_down_only():
                    return True

    def has_pick_up_only(self):
        for row in self.rows:
            for cell in row.times:
                if type(cell) is Cell and cell.pick_up_only():
                    return True

    # def get_order(self):
    #     if self.trips:
    #         return self.trips[0].start

    def width(self):
        return len(self.rows[0].times)

    def rowspan(self):
        return sum(2 if row.has_waittimes else 1 for row in self.rows)

    def min_height(self):
        return sum(
            2 if row.has_waittimes else 1 for row in self.rows if not row.is_minor()
        )

    def get_operators(self):
        operators = {o.noc: o for o in self.parent.operators}

        for head in self.get_column_heads("operator_id"):
            head.content = operators.get(head.content, head.content)
            yield head

    def get_column_heads(self, key):
        prev_value = getattr(self.trips[0], key)
        head = ColumnHead(prev_value, 1)

        for trip in self.trips[1:]:
            value = getattr(trip, key)
            if value == prev_value:
                head.span += 1
            else:
                yield head
                head = ColumnHead(value, 1)
            prev_value = value

        yield head

    def get_garages(self):
        return self.get_column_heads("garage")

    def get_vehicle_types(self):
        return self.get_column_heads("vehicle_type")

    def vehicles_by_date(self):
        journeys = (
            Trip.vehiclejourney_set.field.model.objects.filter(trip__in=self.trips)
            .select_related("vehicle")
            .order_by("-id")[: len(self.trips) * 7]
        )

        by_date = {}
        for journey in journeys:
            date = journey.datetime.date()
            if date in by_date:
                by_date[date][journey.trip_id] = journey
            else:
                by_date[date] = {journey.trip_id: journey}

        for date, by_trip in sorted(by_date.items()):
            yield date, [by_trip.get(trip.id) for trip in self.trips]

    def sort_rows(self):
        stop_times = {}
        successors = {}  # key -> set of successor keys
        in_degree = {}  # key -> number of predecessors
        # count how many trips have (prev, key) as an adjacent pair
        adjacency_count = {}
        # collect sequence numbers per stop key (can be null/wrong)
        sequences = {}

        for trip in self.trips:
            prev = None
            for stop_time in trip.times:
                key = stop_time.get_key()
                stop_times[key] = stop_time
                successors.setdefault(key, set())
                in_degree.setdefault(key, 0)

                if stop_time.sequence is not None:
                    sequences[key] = -stop_time.sequence

                if prev:
                    if key not in successors[prev]:
                        successors[prev].add(key)
                        in_degree[key] += 1
                    pair = (prev, key)
                    adjacency_count[pair] = adjacency_count.get(pair, 0) + 1
                prev = key

        def chain_length(node):
            """Count stops in the unambiguous chain from node before a fork or merge."""
            length = 1
            current = node
            while True:
                succs = successors.get(current, set())
                if len(succs) != 1:
                    break
                (nxt,) = succs
                if in_degree.get(nxt, 0) != 1:
                    break  # merge point: another predecessor still pending
                length += 1
                current = nxt
            return length

        def sort_key(k):
            adj = adjacency_count.get((last, k), 0) if last is not None else 0
            if adj > 0:
                # At a branch point: among direct successors, prefer shorter
                # chains so that short terminus branches (e.g. a Street
                # terminus stop) stay grouped near their branch point rather
                # than being stranded at the bottom of the timetable.
                return (True, -chain_length(k), adj, sequences.get(k, 0))
            else:
                # Initial selection or no direct-successor relationship:
                # use sequence number only (chain_length would wrongly prefer
                # short-chain route-starts over long-chain route-starts).
                return (False, 0, adj, sequences.get(k, 0))

        # Kahn's algorithm, but when multiple nodes are ready,
        # prefer the direct successor of the last emitted node
        # (with most trips using that edge as tie-break),
        # then prefer lower sequence number.
        # This keeps variant-only stops grouped together
        # instead of interleaving them.
        ready = [key for key, deg in in_degree.items() if deg == 0]
        result = []
        last = None

        while ready:
            best = max(ready, key=sort_key)

            ready.remove(best)
            result.append(best)
            last = best

            for succ in successors.get(best, ()):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    ready.append(succ)

        if len(result) != len(stop_times):
            # cycle detected, so we will use difflib later
            # longest trips first, to minimise duplicate rows
            self.trips.sort(key=lambda t: -len(t.times))
        else:
            self.rows = [
                Row(Stop(stop_times[key].stop_id, stop_times[key].stop_code))
                for key in result
            ]
            for row in self.rows:
                row.timing_status = stop_times[row.stop.stop_code].timing_status

    def sort_columns(self):
        rows = self.rows

        sorter = graphlib.TopologicalSorter()
        for a_index, a in enumerate(self.trips):
            a_top = rows.index(a.top)
            a_bottom = rows.index(a.bottom)

            for b_index, b in enumerate(self.trips):
                if a_index == b_index:
                    continue

                b_top = rows.index(b.top)
                b_bottom = rows.index(b.bottom)

                for row in rows[max(a_top, b_top) : min(a_bottom, b_bottom) + 1]:
                    if row.times[a_index] and row.times[b_index]:
                        a_time = row.times[a_index].departure_or_arrival()
                        b_time = row.times[b_index].departure_or_arrival()
                        if a_time > b_time:  # a after b
                            sorter.add(a.id, b.id)
                        elif a_time < b_time:  # a before b
                            sorter.add(b.id, a.id)
                        elif b.top is a.bottom:
                            sorter.add(b.id, a.id)
                        break

        trip_ids = [trip.id for trip in self.trips]
        try:
            indices = [trip_ids.index(trip_id) for trip_id in sorter.static_order()]
            assert len(trip_ids) == len(indices)
            self.trips = [self.trips[i] for i in indices]
        except (graphlib.CycleError, AssertionError):
            self.trips.sort(key=cmp_to_key(partial(compare_trips, self.rows, trip_ids)))
            new_trip_ids = [trip.id for trip in self.trips]
            indices = [trip_ids.index(trip_id) for trip_id in new_trip_ids]

        for row in rows:
            # reassemble in order
            row.times = [row.times[i] for i in indices]

    def merge_split_trips(self):
        zero = datetime.timedelta()
        fifteen = datetime.timedelta(minutes=15)
        prev_trip = None

        for i, trip_a in enumerate(self.trips):
            if not trip_a.times:
                continue

            # remove duplicates
            if (
                prev_trip
                and prev_trip.start == trip_a.start
                and prev_trip.end == trip_a.end
                and prev_trip.destination_id == trip_a.destination_id
                and len(prev_trip.times) == len(trip_a.times)
            ):
                trip_a.times = None
                continue
            prev_trip = trip_a

            # don't merge circular trips (start and finish at same stop))
            origin = trip_a.times[0].get_key()
            destination = trip_a.times[-1].get_key()
            if origin == destination:
                continue

            for j, trip_b in enumerate(self.trips[i + 1 :]):
                if (
                    trip_b.times
                    and trip_a.route_id != trip_b.route_id
                    and trip_a.route.source_id == trip_b.route.source_id
                    and trip_a.route.line_name == trip_b.route.line_name
                    and (
                        trip_a.route.service_code != trip_b.route.service_code
                        or trip_a.ticket_machine_code == trip_b.ticket_machine_code
                    )
                    and trip_a.operator_id == trip_b.operator_id
                    and destination == trip_b.times[0].get_key()
                    and origin != trip_b.times[-1].get_key()  # not circular
                    and destination != trip_b.times[-1].get_key()  # not circular
                    and zero
                    <= (trip_b.start - trip_a.end)
                    <= fifteen  # short wait time
                ):
                    # merge trip_a and trip_b
                    origin = trip_b.times[0].get_key()
                    destination = trip_b.times[-1].get_key()
                    trip_a.times[-1].departure = trip_b.times[0].departure
                    trip_a.times[-1].pick_up = trip_b.times[0].pick_up
                    trip_a.times += trip_b.times[1:]
                    trip_a.end = trip_b.end
                    trip_b.times = None

        self.trips = [trip for trip in self.trips if trip.times]

    def handle_trip(self, trip):
        rows = self.rows
        if rows:
            x = len(rows[0].times)  # number of existing columns
        else:
            x = 0
        previous_list = [row.stop.stop_code for row in rows]
        current_list = [stoptime.get_key() for stoptime in trip.times]
        if current_list == previous_list:
            diff = None
        else:
            diff = differ.compare(previous_list, current_list)

        y = 0  # how many rows along we are
        first = True

        for stoptime in trip.times:
            key = stoptime.get_key()

            if y < len(rows):
                existing_row = rows[y]
            else:
                existing_row = None

            if diff:
                instruction = next(diff)

                while instruction[0] in "-?":
                    if instruction[0] == "-":
                        y += 1
                        if y < len(rows):
                            existing_row = rows[y]
                        else:
                            existing_row = None
                    instruction = next(diff)

                assert instruction[2:] == key

                if instruction[0] == "+":
                    row = Row(Stop(stoptime.stop_id, stoptime.stop_code), [""] * x)
                    row.timing_status = stoptime.timing_status
                    if not existing_row:
                        rows.append(row)
                    else:
                        rows = self.rows = rows[:y] + [row] + rows[y:]
                else:
                    row = existing_row
                    assert instruction[2:] == existing_row.stop.stop_code
            else:
                row = existing_row

            cell = Cell(stoptime, stoptime.arrival, stoptime.departure)
            if first:
                cell.first = True
                trip.top = row
                first = False
            row.times.append(cell)

            y += 1

        if not first:  # (there was at least 1 stoptime in the trip)
            cell.last = True
            trip.bottom = row

        for row in rows:
            if len(row.times) == x:
                row.times.append("")

    def do_heads_and_feet(self, detailed=False):
        if not self.trips:
            return

        previous_trip = None
        previous_note_ids = ()
        in_a_row = 0
        prev_difference = None

        max_notes = max(len(trip.notes.all()) for trip in self.trips)

        for i, trip in enumerate(self.trips):
            difference = None
            notes = trip.notes.all()
            note_ids = {note.id for note in notes}

            # add notes
            for note in notes:
                if note.id in self.column_feet:
                    if note.id in previous_note_ids:
                        self.column_feet[note.id][-1].span += 1
                    else:
                        self.column_feet[note.id].append(ColumnFoot(note))
                elif note.id in previous_note_ids:
                    assert max_notes == 1
                    for note_id in self.column_feet:
                        self.column_feet[note_id][-1].span += 1
                elif i:  # not the first trip
                    if max_notes == 1 and self.column_feet:
                        # assert len(self.column_feet) == 1
                        for note_id in self.column_feet:
                            self.column_feet[note_id].append(ColumnFoot(note))
                    else:
                        self.column_feet[note.id] = [
                            ColumnFoot(None, i),
                            ColumnFoot(note),
                        ]
                else:
                    self.column_feet[note.id] = [ColumnFoot(note)]

            # add or expand empty cells
            if max_notes > 1 or not notes:
                for key in self.column_feet:
                    if key not in note_ids:
                        if not self.column_feet[key][-1].note:
                            # expand existing empty cell
                            self.column_feet[key][-1].span += 1
                        else:
                            # new empty cell
                            self.column_feet[key].append(ColumnFoot(None, 1))

            if previous_trip:
                if previous_trip.route.line_name != trip.route.line_name:
                    self.routes.append(
                        ColumnHead(
                            previous_trip.route,
                            i - sum(head.span for head in self.routes),
                        )
                    )

                if detailed:
                    pass
                elif previous_note_ids != note_ids:
                    if in_a_row > 1:
                        abbreviate(self, i, in_a_row - 1, prev_difference)
                    in_a_row = 0
                elif journey_patterns_match(previous_trip, trip):
                    difference = trip.start - previous_trip.start
                    if difference == prev_difference:
                        in_a_row += 1
                    else:
                        if in_a_row > 1:
                            abbreviate(self, i, in_a_row - 1, prev_difference)
                        in_a_row = 0
                else:
                    if in_a_row > 1:
                        abbreviate(self, i, in_a_row - 1, prev_difference)
                    in_a_row = 0

            prev_difference = difference
            previous_trip = trip
            previous_note_ids = note_ids

        if previous_trip:
            self.routes.append(
                ColumnHead(
                    previous_trip.route,
                    len(self.trips) - sum(head.span for head in self.routes),
                )
            )

        if in_a_row > 1:
            abbreviate(self, len(self.trips), in_a_row - 1, prev_difference)

        for row in self.rows:
            # remove 'None' cells created during the abbreviation process
            # (actual empty cells will contain an empty string '')
            row.times = [time for time in row.times if time is not None]

    def apply_stops(self, stops):
        for row in self.rows:
            row.stop = stops.get(row.stop.atco_code, row.stop)
        self.rows = [row for row in self.rows if not row.permanently_suspended()]
        min_height = self.min_height()
        rowspan = self.rowspan()
        for cell in self.rows[0].times:
            if type(cell) is Repetition:
                cell.min_height = min_height
                cell.rowspan = rowspan

        if self.has_minor_stops() and not self.has_major_stops():
            for row in self.rows:
                if row.stop and row.stop.timing_status:
                    row.timing_status = row.stop.timing_status


class ColumnHead:
    def __init__(self, content, span: int):
        self.content = content
        self.span = span

    def get_html(self):
        if self.span > 1:
            return format_html("""<td colspan="{}">{}</td>""", self.span, self.content)
        return format_html("""<td>{}</td>""", self.content)


class ColumnFoot:
    def __init__(self, note, span=1):
        self.note = note
        self.span = span


class Row:
    def __init__(self, stop, times=None):
        self.stop = stop
        self.times = times or []

    @cached_property
    def has_waittimes(self) -> bool:
        for cell in self.times:
            if type(cell) is Cell and cell.wait_time:
                return True
        return False

    @cached_property
    def set_down_only(self) -> bool:
        return all(cell.set_down_only() for cell in self.times if type(cell) is Cell)

    @cached_property
    def pick_up_only(self) -> bool:
        return all(cell.pick_up_only() for cell in self.times if type(cell) is Cell)

    @cached_property
    def note(self):
        note = None
        for cell in self.times:
            if type(cell) is Cell:
                if hasattr(cell.stoptime, "note"):
                    if note is None:
                        note = cell.stoptime.note
                    elif note != cell.stoptime.note:
                        return
                else:
                    return
        return note

    @cached_property
    def od(self) -> bool:
        """is the origin or destination of any trip"""
        return any(cell.first or cell.last for cell in self.times if type(cell) is Cell)

    is_minor = StopTime.is_minor

    def permanently_suspended(self):
        return hasattr(self.stop, "suspended") and self.stop.suspended


class Stop:
    def __init__(self, stop_id, stop_code=None):
        self.timing_status = None
        self.atco_code = stop_id
        self.stop_code = stop_code or stop_id

    def __str__(self):
        return self.stop_code or self.atco_code


class Cell:
    def __init__(self, stoptime, arrival, departure):
        self.first = False
        self.last = False
        self.stoptime = stoptime
        self.arrival = arrival
        self.departure = departure
        if arrival is None:
            self.arrival = departure
        elif departure is None:
            self.departure = arrival
        self.wait_time = arrival and departure and departure - arrival

    def departure_or_arrival(self):
        return self.stoptime.departure_or_arrival()

    def __repr__(self):
        return format_timedelta(self.arrival, plus_one=True)

    def departure_time(self):
        return format_timedelta(self.departure, plus_one=True)

    def set_down_only(self):
        if not self.last:
            if self.stoptime.set_down and not self.stoptime.pick_up:
                return True

    def pick_up_only(self):
        if not self.first:
            if self.stoptime.pick_up and not self.stoptime.set_down:
                return True
