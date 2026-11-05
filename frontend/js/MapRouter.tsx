import React from "react";
import { Route, Switch } from "wouter";
import { useLocationProperty } from "wouter/use-browser-location";

import BigMap, { MapMode, type Operator } from "./BigMap";
import type { Trip } from "./TripTimetable";

const tripDataElement = document.getElementById("trip-data");
let tripData: Trip | undefined;
if (tripDataElement) {
  tripData = JSON.parse(tripDataElement.textContent as string) as Trip;
}

const operatorDataElement = document.getElementById("operator-data");
let operatorData: Operator | undefined;
if (operatorDataElement) {
  operatorData = JSON.parse(
    operatorDataElement.textContent as string,
  ) as Operator;
}

// /map#journey=123
function getHashJourneyId() {
  return new URLSearchParams(window.location.hash.slice(1)).get("journey");
}

function useHashJourneyId() {
  return useLocationProperty(getHashJourneyId);
}

export default function MapRouter() {
  const hashJourneyId = useHashJourneyId();

  return (
    <Switch>
      <Route path="/trips/:tripId">
        {(params) => (
          <BigMap mode={MapMode.Trip} trip={tripData} tripId={params.tripId} />
        )}
      </Route>
      <Route path="/journeys/:journeyId">
        {(params) => (
          <BigMap mode={MapMode.Journey} journeyId={params.journeyId} />
        )}
      </Route>
      <Route path="/operators/:operatorSlug/map">
        {(params) => (
          <BigMap
            mode={MapMode.Operator}
            operatorSlug={params.operatorSlug}
            operator={operatorData}
          />
        )}
      </Route>
      <Route path="/map">
        {hashJourneyId ? (
          <BigMap mode={MapMode.Journey} journeyId={hashJourneyId} />
        ) : (
          <BigMap mode={MapMode.Slippy} />
        )}
      </Route>
    </Switch>
  );
}
