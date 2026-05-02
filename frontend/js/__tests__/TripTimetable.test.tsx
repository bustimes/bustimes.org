/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react";
import TripTimetable, { type Trip } from "../TripTimetable";
import type { Vehicle } from "../VehicleMarker";

const trip: Trip = {
  id: 273819070,
  vehicle_journey_code: "vj_41",
  ticket_machine_code: "29",
  block: "",
  service: { id: 4441, line_name: "502", mode: "bus" },
  operator: { noc: "KCTB", name: "Konectbus", vehicle_mode: "bus" },
  notes: [],
  times: [
    {
      id: 16494874561,
      stop: {
        atco_code: "2900C0305",
        name: "Harford Park & Ride",
        location: [1.2720919815463243, 52.588796929212215],
        bearing: 135,
      },
      aimed_arrival_time: null,
      aimed_departure_time: "10:05",
      track: null,
      timing_status: "PTP",
      pick_up: true,
      set_down: false,
      expected_arrival_time: null,
      expected_departure_time: null,
    },
    {
      id: 16494874562,
      stop: {
        atco_code: "2900N1237",
        name: "County Hall Bus Shelter",
        location: [1.3073762895735876, 52.61532759989593],
        bearing: 225,
      },
      aimed_arrival_time: null,
      aimed_departure_time: "10:13",
      track: null,
      timing_status: "PTP",
      pick_up: true,
      set_down: true,
      expected_arrival_time: null,
      expected_departure_time: null,
    },
    {
      id: 16494874563,
      stop: {
        atco_code: "2900N12909",
        name: "Norwich Bus Station",
        location: [1.292518141141929, 52.6240700123112],
        bearing: null,
      },
      aimed_arrival_time: "10:18",
      aimed_departure_time: "10:20",
      track: null,
      timing_status: "PTP",
      pick_up: true,
      set_down: true,
      expected_arrival_time: null,
      expected_departure_time: null,
    },
    {
      id: 16494874564,
      stop: {
        atco_code: "2900N12245",
        name: "Norwich Tombland",
        location: [1.298782630795197, 52.63091962080655],
        bearing: 0,
      },
      aimed_arrival_time: null,
      aimed_departure_time: "10:24",
      track: null,
      timing_status: "OTH",
      pick_up: true,
      set_down: true,
      expected_arrival_time: null,
      expected_departure_time: null,
    },
    {
      id: 16494874565,
      stop: {
        atco_code: "2900N12233",
        name: "Norwich Anglia Square",
        location: [1.2963982671391574, 52.63577066440402],
        bearing: 0,
      },
      aimed_arrival_time: null,
      aimed_departure_time: "10:27",
      track: null,
      timing_status: "PTP",
      pick_up: true,
      set_down: true,
      expected_arrival_time: null,
      expected_departure_time: null,
    },
    {
      id: 16494874566,
      stop: {
        atco_code: "2900S32517",
        name: "Sprowston Park and Ride",
        location: [1.33416997295962, 52.66492263745109],
        bearing: 225,
      },
      aimed_arrival_time: "10:40",
      aimed_departure_time: null,
      track: null,
      timing_status: "PTP",
      pick_up: true,
      set_down: true,
      expected_arrival_time: null,
      expected_departure_time: null,
    },
  ],
};

const vehicle: Vehicle = {
  id: 20158,
  coordinates: [1.308246, 52.617555],
  heading: 303,
  datetime: "2023-09-15T09:17:19Z",
  destination: "Sprowston",
  block: "601067",
  trip_id: 273819070,
  service_id: 4441,
  service: {
    url: "/services/502-park-ride-harford-pr-norwich-city-centre-sprow",
    line_name: "502",
  },
  vehicle: {
    url: "/vehicles/kctb-goea-630",
    name: "630 - SN65 OAL",
    features: "Double decker",
    livery: 405,
    colour: "#79b33b",
  },
  progress: {
    id: 16494874562,
    sequence: 2,
    prev_stop: "2900N1237",
    next_stop: "2900N12909",
    progress: 0.022,
  },
  delay: 252,
};

it("renders trip timetable", async () => {
  const { container, rerender } = render(<TripTimetable trip={trip} />);

  expect(container).toMatchSnapshot();

  rerender(<TripTimetable trip={trip} vehicle={vehicle} />);
  expect(container).toMatchSnapshot();

  rerender(
    <TripTimetable
      trip={trip}
      vehicle={vehicle}
      highlightedStop="/stops/2900N12245"
    />,
  );
  expect(container).toMatchSnapshot();
});
