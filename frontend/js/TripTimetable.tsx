import React, { type ReactElement } from "react";
import { formatTime } from "./StopPopup";
import type { Vehicle } from "./VehicleMarker";

export type TripTime = {
  id: number;
  stop: {
    name: string;
    atco_code?: string;
    location?: [number, number];
    bearing?: number | null;
  };
  track?: [number, number][] | null;
  aimed_arrival_time: string | null;
  aimed_departure_time: string | null;
  expected_arrival_time?: string | null;
  expected_departure_time?: string | null;
  actual_arrival_time?: string | null;
  actual_departure_time?: string;
  timing_status: "PTP" | "OTH";
  pick_up?: boolean;
  set_down?: boolean;
  call_condition?: string | null;
  note_codes?: string[];
};

type Note = {
  code: string;
  text: string;
};

export type Trip = {
  id?: number;
  vehicle_journey_code?: string;
  ticket_machine_code?: string;
  block?: string;
  service?: {
    slug?: string;
    id: number | null;
    line_name?: string;
    mode?: string;
  };
  operator?: {
    slug?: string;
    noc: string;
    name: string;
    vehicle_mode: string;
  };
  times: TripTime[];
  notes?: Note[];
};

// distance (number of stops) between bus and this stop
function getDiff(stop: TripTime, vehicle?: Vehicle | null) {
  if (vehicle?.progress) {
    if (stop.id && vehicle.progress.id) {
      return stop.id - vehicle.progress.id;
    }
    if (stop.stop.atco_code) {
      if (vehicle.progress.prev_stop === stop.stop.atco_code) {
        return 0;
      }
      if (vehicle.progress.next_stop === stop.stop.atco_code) {
        return 1;
      }
    }
  }
  return null;
}

function Row({
  stop,
  onHover,
  vehicle,
  aimedColumn,
  expectedColumn,
  highlightedStop,
  diff,
  covered,
  rowRef,
  first = false,
  last = false,
}: {
  stop: TripTime;
  onHover?: (stop: TripTime) => void;
  vehicle?: Vehicle | null;
  aimedColumn: boolean;
  expectedColumn: boolean;
  highlightedStop?: string;
  diff: number | null;
  covered: boolean; // actual column taken by the previous row's vehicle cell
  rowRef?: React.Ref<HTMLTableRowElement>;
  first: boolean;
  last: boolean;
}) {
  const handlePointerEnter = React.useCallback(() => {
    if (onHover && stop.stop.location) {
      onHover(stop);
    }
  }, [stop, onHover]);

  let rowSpan: number | undefined;
  if (
    aimedColumn &&
    stop.aimed_arrival_time &&
    stop.aimed_departure_time &&
    stop.aimed_arrival_time !== stop.aimed_departure_time
  ) {
    rowSpan = 2;
  }

  let aimed: ReactElement | null | string = null;
  if (aimedColumn) {
    aimed = formatTime(stop.aimed_arrival_time || stop.aimed_departure_time);
    aimed = <td>{aimed}</td>;
  }

  let actual: string | null | ReactElement | undefined;
  let actualRowSpan = rowSpan;
  let actualDeparture: string | null = null; // shown on the second row, when split

  let expected = null;
  let expectedDeparture = null;

  if (expectedColumn) {
    expected = formatTime(
      stop.expected_arrival_time || stop.expected_departure_time,
    );
    if (rowSpan === 2) {
      if (
        stop.expected_departure_time &&
        stop.expected_departure_time !== stop.expected_arrival_time
      ) {
        expected = <td>{expected}</td>;
        expectedDeparture = <td>{formatTime(stop.expected_departure_time)}</td>;
      } else {
        expected = <td rowSpan={2}>{expected}</td>;
      }
    } else {
      expected = <td>{expected}</td>;
    }
  }

  if (vehicle?.progress && diff === 0) {
    actual = <strong>{vehicle.datetime.slice(11, 16)}</strong>;
    if (vehicle.progress.progress > 0.1) {
      actualRowSpan = (actualRowSpan || 1) + 1;
    }
  } else if (diff !== 1 && !covered) {
    // vehicle history
    if (
      rowSpan === 2 &&
      stop.actual_arrival_time &&
      stop.actual_departure_time &&
      stop.actual_arrival_time !== stop.actual_departure_time
    ) {
      actual = stop.actual_arrival_time.slice(11, 16);
      actualDeparture = stop.actual_departure_time.slice(11, 16);
      actualRowSpan = 1;
    } else {
      const time = stop.actual_departure_time || stop.actual_arrival_time;
      actual = time ? time.slice(11, 16) : undefined;
    }
  }
  if (actual) {
    actual = <td rowSpan={actualRowSpan}>{actual}</td>;
  }

  let caveat: ReactElement | undefined;
  if (!first && !last) {
    if (stop.set_down === false) {
      if (stop.pick_up === false) {
        caveat = <abbr title="does not stop">pass</abbr>;
      } else {
        caveat = <abbr title="picks up only">p</abbr>;
      }
    } else if (stop.pick_up === false) {
      caveat = <abbr title="sets down only">s</abbr>;
    }
  }

  const notes = stop.note_codes?.map((note_code) => (
    <strong key={note_code}>{note_code}</strong>
  ));

  if (caveat || notes?.length) {
    caveat = (
      <div className="note">
        {caveat}
        {notes}
      </div>
    );
  }

  let className: string | undefined;

  let stopName: string | ReactElement = stop.stop.name;
  if (stop.call_condition === "notStopping") {
    stopName = <s>{stopName}</s>;
  }
  if (stop.stop.atco_code) {
    const url = `/stops/${stop.stop.atco_code}`;
    if (url === highlightedStop) {
      className = "is-highlighted";
    }
    stopName = (
      <a href={url}>
        {caveat}
        {stopName}
      </a>
    );
  }

  if (stop.timing_status === "OTH") {
    className = className ? `${className} minor` : "minor";
  }

  return (
    <React.Fragment>
      <tr
        className={className}
        onPointerEnter={handlePointerEnter}
        ref={rowRef}
      >
        <td className="stop-name" rowSpan={rowSpan}>
          {stopName}
        </td>
        {aimed}
        {expected}
        {actual}
      </tr>
      {rowSpan ? (
        <tr className={className} onPointerEnter={handlePointerEnter}>
          <td>{formatTime(stop.aimed_departure_time)}</td>
          {expectedDeparture}
          {actualDeparture ? <td>{actualDeparture}</td> : null}
        </tr>
      ) : null}
    </React.Fragment>
  );
}

const TripTimetable = React.memo(function TripTimetable({
  trip,
  onHover,
  vehicle,
  highlightedStop,
}: {
  trip: Trip;
  onHover?: (stop: TripTime) => void;
  vehicle?: Vehicle | null;
  highlightedStop?: string;
}) {
  const aimedColumn: boolean = trip.times.some(
    (item) => item.aimed_arrival_time || item.aimed_departure_time,
  );

  const expectedColumn: boolean = trip.times.some(
    (item) => item.expected_arrival_time || item.expected_departure_time,
  );

  const actualColumn =
    vehicle || trip.times.some((item) => item.actual_departure_time);

  const times = trip.times;
  const indexOfLastRow = times.length - 1;
  const diffs = times.map((stop) => getDiff(stop, vehicle));
  const vehicleIndex = diffs.indexOf(0);
  const coveredIndex =
    vehicleIndex !== -1 && vehicle?.progress && vehicle.progress.progress > 0.1
      ? vehicleIndex + 1
      : -1;

  const tableRef = React.useRef<HTMLTableElement>(null);
  const progressRowRef = React.useRef<HTMLTableRowElement>(null);

  // scroll a stop clicked on the map into view (but not the initial one, from
  // the referrer)
  const prevHighlightedStop = React.useRef(highlightedStop);
  React.useEffect(() => {
    if (highlightedStop === prevHighlightedStop.current) {
      return;
    }
    prevHighlightedStop.current = highlightedStop;
    if (highlightedStop) {
      tableRef.current
        ?.querySelector(".is-highlighted")
        ?.scrollIntoView({ block: "nearest" });
    }
  }, [highlightedStop]);

  // on navigating to a new trip, scroll to the vehicle's progress (once known),
  // or else to the top
  const scrolledToTop = React.useRef<Trip>(undefined);
  const scrolledToProgress = React.useRef<Trip>(undefined);
  const progressKnown = vehicleIndex !== -1;
  React.useLayoutEffect(() => {
    if (scrolledToProgress.current === trip) {
      return;
    }
    if (progressKnown && progressRowRef.current) {
      scrolledToProgress.current = trip;
      progressRowRef.current.scrollIntoView({ block: "center" });
    } else if (scrolledToTop.current !== trip) {
      scrolledToTop.current = trip;
      tableRef.current?.closest(".map-sidebar")?.scrollTo(0, 0);
    }
  }, [trip, progressKnown]);

  return (
    <React.Fragment>
      <table ref={tableRef}>
        <thead>
          <tr>
            <th className="stop-name" />
            {aimedColumn ? <th>Sched&shy;uled</th> : null}
            {expectedColumn ? <th>Ex&shy;pected</th> : null}
            {actualColumn ? <th>Actual</th> : null}
          </tr>
        </thead>
        <tbody>
          {times.map((stop, i) => (
            <Row
              key={stop.id || i}
              aimedColumn={aimedColumn}
              expectedColumn={expectedColumn}
              stop={stop}
              onHover={onHover}
              vehicle={vehicle}
              highlightedStop={highlightedStop}
              diff={diffs[i]}
              covered={i === coveredIndex}
              rowRef={i === vehicleIndex ? progressRowRef : undefined}
              first={i === 0}
              last={i === indexOfLastRow}
            />
          ))}
        </tbody>
      </table>
      {trip.notes?.map((note) => (
        <p key={note.code}>
          <strong>{note.code}</strong> {note.text}
        </p>
      ))}
    </React.Fragment>
  );
});

export default TripTimetable;
