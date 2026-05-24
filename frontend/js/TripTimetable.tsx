import React, { type ReactElement } from "react";
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
  actual_departure_time?: string;
  // actual_arrival_time: string;
  timing_status: string;
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

function Row({
  stop,
  onMouseEnter,
  vehicle,
  aimedColumn,
  highlightedStop,
  first = false,
  last = false,
}: {
  stop: TripTime;
  onMouseEnter?: (stop: TripTime) => void;
  vehicle?: Vehicle;
  aimedColumn?: boolean;
  highlightedStop?: string;
  first: boolean;
  last: boolean;
}) {
  const handleMouseEnter = React.useCallback(() => {
    if (onMouseEnter) {
      if (stop.stop.location) {
        onMouseEnter(stop);
      }
    }
  }, [stop, onMouseEnter]);

  let className: string | undefined;

  let stopName: string | ReactElement = stop.stop.name;
  if (stop.stop.atco_code) {
    const url = `/stops/${stop.stop.atco_code}`;
    if (url === highlightedStop) {
      className = "is-highlighted";
    }
    stopName = <a href={url}>{stopName}</a>;
  }

  if (stop.timing_status && stop.timing_status !== "PTP") {
    className = className ? `${className} minor` : "minor";
  }

  let rowSpan: number | undefined;
  if (
    aimedColumn &&
    stop.aimed_arrival_time &&
    stop.aimed_departure_time &&
    stop.aimed_arrival_time !== stop.aimed_departure_time
  ) {
    rowSpan = 2;
  }

  let actual: string | null | ReactElement | undefined;
  let actualRowSpan = rowSpan;

  actual = stop.expected_departure_time || stop.expected_arrival_time; // Irish live departures

  if (!actual) {
    if (vehicle?.progress && vehicle.progress.id === stop.id) {
      actual = vehicle.datetime;
      if (vehicle.progress.progress > 0.1) {
        actualRowSpan = (actualRowSpan || 1) + 1;
      }
    } else if (!vehicle?.progress || vehicle.progress.id + 1 !== stop.id) {
      actual = stop.actual_departure_time; // vehicle history
    }
    if (actual) {
      actual = actual.slice(11, 16);
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

  let aimed: ReactElement | null | string = null;
  if (aimedColumn) {
    aimed = stop.aimed_arrival_time || stop.aimed_departure_time;
    aimed = (
      <td>
        {aimed}
        {caveat}
        {notes}
      </td>
    );
  }

  return (
    <React.Fragment>
      <tr className={className} onMouseEnter={handleMouseEnter}>
        <td className="stop-name" rowSpan={rowSpan}>
          {stopName}
        </td>
        {aimed}
        {actual}
      </tr>
      {rowSpan ? (
        <tr className={className}>
          <td>{stop.aimed_departure_time}</td>
        </tr>
      ) : null}
    </React.Fragment>
  );
}

const TripTimetable = React.memo(function TripTimetable({
  trip,
  onMouseEnter,
  vehicle,
  highlightedStop,
}: {
  trip: Trip;
  onMouseEnter?: (stop: TripTime) => void;
  vehicle?: Vehicle;
  highlightedStop?: string;
}) {
  const [showEarlierStops, setShowEarlierStops] = React.useState(false);

  const aimedColumn = trip.times?.some(
    (item: TripTime) => item.aimed_arrival_time || item.aimed_departure_time,
  );
  const actualColumn =
    vehicle ||
    trip.times?.some(
      (item: TripTime) =>
        item.actual_departure_time ||
        item.expected_arrival_time ||
        item.expected_departure_time,
    );

  let earlierStops = false;

  let times = trip.times;
  if (!showEarlierStops && vehicle && vehicle.progress) {
    const index = times.findIndex((item) => item.id === vehicle.progress?.id);
    if (index > 0) {
      times = times.slice(index);
      earlierStops = true;
    }
  }
  const indexOfLastRow = times.length - 1;

  return (
    <React.Fragment>
      {earlierStops || showEarlierStops ? (
        <label>
          <input
            type="checkbox"
            checked={showEarlierStops}
            onChange={() => setShowEarlierStops(!showEarlierStops)}
          />
          {" Show previous stops"}
        </label>
      ) : null}
      <table>
        <thead>
          <tr>
            <th className="stop-name" />
            {aimedColumn ? <th>Sched&shy;uled</th> : null}
            {actualColumn ? <th>Actual</th> : null}
          </tr>
        </thead>
        <tbody>
          {times.map((stop, i) => (
            <Row
              key={stop.id || i}
              aimedColumn={aimedColumn}
              stop={stop}
              onMouseEnter={onMouseEnter}
              vehicle={vehicle}
              highlightedStop={highlightedStop}
              first={i === 0 && !earlierStops}
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
