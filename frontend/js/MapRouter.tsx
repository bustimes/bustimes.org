import React from "react";
import { Route, Switch } from "wouter";

import BigMap, { MapMode } from "./BigMap";
import type { Trip } from "./TripTimetable";

const tripDataElement = document.getElementById("trip-data");
let tripData: Trip | undefined;
if (tripDataElement) {
  tripData = JSON.parse(tripDataElement.textContent as string) as Trip;
}

export default function MapRouter() {
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
      <Route path="/vehicles/tfl/:reg">
        <BigMap
          mode={MapMode.Trip}
          trip={tripData}
          vehicleId={window.VEHICLE_ID}
        />
      </Route>
      <Route path="/operators/:operatorSlug/map">
        {(params) => (
          <BigMap
            mode={MapMode.Operator}
            operatorSlug={params.operatorSlug}
            noc={window.OPERATOR_ID || (tripData?.operator?.noc as string)}
          />
        )}
      </Route>
      <Route path="/map">
        <BigMap mode={MapMode.Slippy} />
      </Route>
    </Switch>
  );
}
