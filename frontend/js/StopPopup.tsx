import React, { type ReactElement } from "react";
import { Popup } from "react-map-gl/maplibre";

export type Stop = {
  type: "Feature";
  properties: {
    name: string;
    url: string;
    services?: string[];
    aimed_arrival_time?: string | null;
    aimed_departure_time?: string | null;
    expected_arrival_time?: string | null;
    expected_departure_time?: string | null;
    actual_departure_time?: string | null;
  };
  geometry: {
    type: "Point";
    coordinates: [number, number];
  };
};

type StopPopupProps = {
  item: Stop;
  onClose: () => void;
};

function formatTime(t: string | null | undefined): string | null {
  if (!t) return null;
  // accept either "HH:MM" or full ISO timestamps
  if (t.length > 5) return t.slice(11, 16);
  return t;
}

function StopTimes({ properties }: { properties: Stop["properties"] }) {
  const aimedArrival = formatTime(properties.aimed_arrival_time);
  const aimedDeparture = formatTime(properties.aimed_departure_time);
  const expected = formatTime(
    properties.expected_arrival_time || properties.expected_departure_time,
  );
  const actual = formatTime(properties.actual_departure_time);

  let aimed: undefined | ReactElement;
  if (aimedArrival && aimedDeparture && aimedArrival !== aimedDeparture) {
    aimed = (
      <>
        <tr>
          <th scope="row" rowSpan={2}>
            Scheduled
          </th>
          <td>{aimedArrival}</td>
        </tr>
        <tr>
          <td>{aimedDeparture}</td>
        </tr>
      </>
    );
  } else if (aimedArrival || aimedDeparture) {
    aimed = (
      <tr>
        <th scope="row">Scheduled</th>
        <td>{aimedArrival || aimedDeparture}</td>
      </tr>
    );
  }

  if (!aimed && !expected && !actual) return null;

  return (
    <table className="stop-popup-times">
      <tbody>
        {aimed}
        {expected ? (
          <tr>
            <th scope="row">Expected</th>
            <td>{expected}</td>
          </tr>
        ) : null}
        {actual ? (
          <tr>
            <th scope="row">Actual</th>
            <td>{actual}</td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

export default function StopPopup({ item, onClose }: StopPopupProps) {
  let name: ReactElement;

  if (item.properties.url) {
    name = (
      <a href={item.properties.url} className="link-with-smalls">
        <div className="description">{item.properties.name}</div>
        {item.properties.services ? (
          <div className="smalls">{item.properties.services.join("  ")}</div>
        ) : null}
      </a>
    );
  } else {
    name = <div>{item.properties.name}</div>;
  }

  return (
    <Popup
      offset={2}
      latitude={item.geometry.coordinates[1]}
      longitude={item.geometry.coordinates[0]}
      closeOnClick={false}
      onClose={onClose}
      focusAfterOpen={false}
    >
      {name}
      <StopTimes properties={item.properties} />
    </Popup>
  );
}
