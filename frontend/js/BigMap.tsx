import React, {
  type ReactElement,
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { Hash, type LngLatBounds, type Map as MapGL } from "maplibre-gl";
import {
  Layer,
  type MapLayerMouseEvent,
  type MapProps,
  Popup,
  Source,
  type ViewStateChangeEvent,
} from "react-map-gl/maplibre";
import { Link } from "wouter";

import debounce from "lodash/debounce";

import VehicleMarker, {
  type Vehicle as VehicleLocation,
  getClickedVehicleMarkerId,
} from "./VehicleMarker";

import {
  Locations,
  type VehicleJourney,
  type VehicleJourneyLocation,
  getUtcOffsetSeconds,
  locationsFromPolyline,
} from "./JourneyMap";
import LoadingSorry from "./LoadingSorry";
import BusTimesMap, { ThemeContext } from "./Map";
import StopPopup, { type Stop } from "./StopPopup";
import { Route } from "./TripMap";
import TripTimetable, { type Trip, type TripTime } from "./TripTimetable";
import VehiclePopup from "./VehiclePopup";
import { recordSkew } from "./clockSkew";
import { getBounds, getFont } from "./utils";

const apiRoot = process.env.apiRoot ?? "/";

const updateLocalStorage = debounce((zoom: number, latLng) => {
  try {
    localStorage.setItem("vehicleMap", `${zoom}/${latLng.lat}/${latLng.lng}`);
  } catch (e) {
    // never mind
  }
}, 2000);

const hash = new Hash();

function updateHash(map: MapGL) {
  hash._map = map;
  hash._updateHashUnthrottled();
}

const initialViewStateElement = document.getElementById("initialViewState");
let INITIAL_VIEW_STATE: MapProps["initialViewState"];
if (initialViewStateElement) {
  INITIAL_VIEW_STATE = JSON.parse(
    initialViewStateElement.textContent as string,
  );
}

if (INITIAL_VIEW_STATE && !window.location.hash) {
  try {
    if (localStorage.vehicleMap) {
      const parts = localStorage.vehicleMap.split("/");
      if (parts.length === 3) {
        INITIAL_VIEW_STATE = {
          zoom: parts[0],
          latitude: parts[1],
          longitude: parts[2],
        };
      }
    }
  } catch (e) {
    // never mind
  }
}

function getBoundsQueryString(bounds: LngLatBounds): string {
  return `?ymax=${bounds.getNorth()}&xmax=${bounds.getEast()}&ymin=${bounds.getSouth()}&xmin=${bounds.getWest()}`;
}

function containsBounds(
  a: LngLatBounds | null,
  b: LngLatBounds,
): boolean | undefined {
  return a?.contains(b.getNorthWest()) && a.contains(b.getSouthEast());
}

function shouldShowStops(zoom?: number) {
  return zoom && zoom >= 14;
}

function shouldShowVehicles(zoom?: number) {
  return zoom && zoom >= 6;
}

export enum MapMode {
  Slippy = 0,
  Operator = 1,
  Trip = 2,
  Journey = 3,
}

function Stops({
  times,
  clickedStopFeature,
  clickedStopUrl,
  setClickedStop,
}: {
  times?: Trip["times"];
  clickedStopFeature?: Stop;
  clickedStopUrl?: string;
  setClickedStop: (stop?: string) => void;
}) {
  // if we're displaying the stops of a trip
  const stopsById = React.useMemo<{ [url: string]: Stop } | undefined>(() => {
    if (times) {
      return Object.assign(
        {},
        ...times.map((time) => {
          const url = `/stops/${time.stop.atco_code}`;
          return {
            [url]: {
              type: "Feature",
              geometry: { type: "Point", coordinates: time.stop.location },
              properties: {
                url,
                name: time.stop.name,
                aimed_arrival_time: time.aimed_arrival_time,
                aimed_departure_time: time.aimed_departure_time,
                expected_arrival_time: time.expected_arrival_time,
                expected_departure_time: time.expected_departure_time,
                actual_departure_time: time.actual_departure_time,
              },
            },
          };
        }),
      );
    }
  }, [times]);

  const clickedStop = times
    ? stopsById && clickedStopUrl && stopsById[clickedStopUrl]
    : clickedStopFeature;

  const theme = React.useContext(ThemeContext);

  const font = getFont(theme);

  return (
    <React.Fragment>
      {times ? null : (
        <Source
          type="vector"
          tiles={[`${location.origin}/stops/{z}/{x}/{y}.pbf`]}
          minzoom={14}
          maxzoom={14}
        >
          <Layer
            id="stops"
            source-layer="stops"
            type="symbol"
            minzoom={14}
            layout={{
              "text-field": ["get", "icon"],
              "text-font": font,
              "text-allow-overlap": true,
              "text-size": 10,
              "icon-rotate": ["+", 45, ["coalesce", ["get", "bearing"], 0]],
              "icon-image": [
                "case",
                ["==", ["get", "bearing"], ["literal", null]],
                "stop-marker-circle",
                "stop-marker",
              ],
              "icon-allow-overlap": true,
              "icon-ignore-placement": true,
              "text-ignore-placement": true,
              "icon-padding": [3],
            }}
            paint={{
              "text-color": "#ffffff",
            }}
          />
        </Source>
      )}
      {clickedStop ? (
        <StopPopup
          item={clickedStop}
          onClose={() => setClickedStop(undefined)}
        />
      ) : null}
    </React.Fragment>
  );
}

function fetchJson(url: string) {
  return fetch(apiRoot + url, {
    credentials: "omit",
  }).then(
    (response) => {
      recordSkew(response);
      if (response.ok) {
        return response.json();
      }
    },
    () => {
      // never mind
    },
  );
}

type VehiclesProps = {
  vehicles: VehicleLocation[];
  tripId?: string;
  journeyId?: string;
  clickedVehicleMarkerId?: number;
  setClickedVehicleMarker: (vehicleId?: number) => void;
};

const Vehicles = memo(function Vehicles({
  vehicles,
  tripId,
  journeyId,
  clickedVehicleMarkerId,
  setClickedVehicleMarker,
}: VehiclesProps) {
  const vehiclesGeoJson = React.useMemo(() => {
    if (vehicles.length < 1000) {
      return null;
    }
    return {
      type: "FeatureCollection" as const,
      features: vehicles
        ? vehicles.map((vehicle) => {
            return {
              type: "Feature" as const,
              geometry: {
                type: "Point" as const,
                coordinates: vehicle.coordinates,
              },
              properties: {
                id: vehicle.id, // (not feature.id, which can't be negative)
                url: vehicle.vehicle?.url,
                colour:
                  vehicle.vehicle?.colour ||
                  (vehicle.vehicle?.css?.length === 7
                    ? vehicle.vehicle.css
                    : "#fff"),
              },
            };
          })
        : [],
    };
  }, [vehicles]);

  const vehiclesById = React.useMemo<
    { [id: string]: VehicleLocation } | undefined
  >(() => {
    if (!vehiclesGeoJson) {
      return Object.assign(
        {},
        ...vehicles.map((item) => ({ [item.id]: item })),
      );
    }
  }, [vehicles, vehiclesGeoJson]);

  const clickedVehicle =
    clickedVehicleMarkerId &&
    (vehiclesById
      ? vehiclesById[clickedVehicleMarkerId]
      : vehicles.find((item) => item.id === clickedVehicleMarkerId));

  let markers: ReactElement[] | ReactElement;

  if (!vehiclesGeoJson) {
    markers = vehicles.map((item) => {
      return (
        <VehicleMarker
          key={item.id}
          selected={
            item === clickedVehicle ||
            (tripId && tripId === item.trip_id?.toString()) ||
            (journeyId && journeyId === item.journey_id?.toString()) ||
            false
          }
          vehicle={item}
        />
      );
    });
  } else {
    markers = (
      <Source type="geojson" data={vehiclesGeoJson}>
        <Layer
          {...{
            id: "vehicles",
            type: "circle",
            paint: {
              "circle-color": ["get", "colour"],
            },
          }}
        />
      </Source>
    );
  }

  return (
    <React.Fragment>
      {markers}
      {clickedVehicle && (
        <VehiclePopup
          item={clickedVehicle}
          activeLink={
            journeyId
              ? clickedVehicle.journey_id?.toString() === journeyId
              : false
          }
          onClose={() => setClickedVehicleMarker()}
          snazzyTripLink
        />
      )}
      {clickedVehicle && vehiclesGeoJson && (
        <VehicleMarker selected={true} vehicle={clickedVehicle} />
      )}
    </React.Fragment>
  );
});

function TripSidebar(props: {
  trip?: Trip;
  tripId?: string;
  vehicle?: VehicleLocation | null;
  highlightedStop?: string;
  onHover: (stop: TripTime) => void;
}) {
  let className = "trip-timetable map-sidebar";

  const trip = props.trip;

  if (!trip) {
    return <div className={className} />;
  }

  if (props.tripId !== trip.id?.toString()) {
    className += " loading";
  }

  const operator = trip.operator ? (
    <li>
      <a href={`/operators/${trip.operator.slug}`}>{trip.operator.name}</a>
    </li>
  ) : null;

  const service = props.vehicle?.service ? (
    <li>
      <a href={props.vehicle.service.url}>{props.vehicle.service.line_name}</a>
    </li>
  ) : trip.service?.slug ? (
    <li>
      <a href={`/services/${trip.service.slug}`}>{trip.service.line_name}</a>
    </li>
  ) : null;

  return (
    <div className={className}>
      {operator || service ? (
        <ol className="breadcrumb">
          {operator}
          {service}
        </ol>
      ) : null}
      <TripTimetable
        trip={trip}
        vehicle={props.vehicle}
        highlightedStop={props.highlightedStop}
        onHover={props.onHover}
      />
      <dl className="contact-details">
        {trip.block ? (
          <div>
            <dt>Block</dt>
            <dd>
              <a href={`/trips/${trip.id}/block`}>{trip.block}</a>
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}

function JourneySidebar(props: {
  journey?: VehicleJourney;
  journeyId: string;
  journeyContext?: string | null;
  highlightedStop?: string;
  vehicle?: VehicleLocation | null;
  onHover: (stop: TripTime) => void;
}) {
  let className = "trip-timetable map-sidebar";

  const journey = props.journey;

  if (!journey) {
    return (
      <div className={className}>
        <LoadingSorry />
      </div>
    );
  }

  const showNavigation = journey.previous || journey.next;
  const hash = props.journeyContext ? `#context=${props.journeyContext}` : "";

  const _operator = journey.operator || journey.trip?.operator;
  let operator: ReactElement | undefined;
  if (_operator) {
    operator = (
      <li>
        <a href={`/operators/${_operator.slug}`}>{_operator.name}</a>
      </li>
    );
  }

  let service: ReactElement | undefined;
  let serviceLink: ReactElement | undefined;
  if (journey.service) {
    service = (
      <li>
        <a href={`/services/${journey.service.slug}?date=${journey.date}`}>
          {journey.route_name}
        </a>
      </li>
    );
    serviceLink = (
      <a
        href={`/services/${journey.service.slug}/vehicles?date=${journey.date}#journey-${journey.id}`}
      >
        {journey.route_name}
      </a>
    );
  } else if (_operator && journey.route_name) {
    serviceLink = (
      <a
        href={`/services/${_operator.noc}:${journey.route_name}/vehicles?date=${journey.date}#journey-${journey.id}`}
      >
        {journey.route_name}
      </a>
    );
    service = <li>{serviceLink}</li>;
  } else if (journey.vehicle) {
    service = (
      <li>
        <a
          href={`/vehicles/${journey.vehicle.slug}?date=${journey.date}#journey-${journey.id}`}
        >
          {journey.vehicle.fleet_code && journey.vehicle.reg
            ? `${journey.vehicle.fleet_code} - ${journey.vehicle.reg}`
            : journey.vehicle.reg ||
              journey.vehicle.fleet_code ||
              journey.vehicle.slug}
        </a>
      </li>
    );
  }

  if (!journey.trip_id) {
    className += " no-stops";
  }

  if (props.journeyId !== journey.id?.toString()) {
    className += " loading";
  }

  return (
    <div className={className}>
      {operator || service ? (
        <ol className="breadcrumb">
          {operator}
          {service}
        </ol>
      ) : null}
      {showNavigation ? (
        <div className="navigation">
          {journey.previous ? (
            <p className="previous">
              <Link href={`/journeys/${journey.previous.id}${hash}`}>
                &larr; {journey.previous.datetime.slice(11, 16)}
              </Link>
            </p>
          ) : null}
          {journey.next ? (
            <p className="next">
              <Link href={`/journeys/${journey.next.id}${hash}`}>
                {journey.next.datetime.slice(11, 16)} &rarr;
              </Link>
            </p>
          ) : null}
        </div>
      ) : null}
      {!journey.trip && journey.destination ? (
        <p>To {journey.destination}</p>
      ) : null}
      {journey.trip?.times ? (
        <TripTimetable
          trip={journey.trip}
          vehicle={props.vehicle}
          highlightedStop={props.highlightedStop}
          onHover={props.onHover}
        />
      ) : null}
      <dl className="contact-details">
        {journey.vehicle ? (
          <div>
            <dt>Vehicle</dt>
            <dd>
              <a
                href={`/vehicles/${journey.vehicle.slug}?date=${journey.date}#journey-${journey.id}`}
              >
                {journey.vehicle.fleet_code}{" "}
                {journey.vehicle.reg ? (
                  <span className="reg">{journey.vehicle.reg}</span>
                ) : journey.vehicle.fleet_code ? null : (
                  journey.vehicle.slug
                )}
              </a>
            </dd>
          </div>
        ) : null}
        {serviceLink ? (
          <div>
            <dt>Service</dt>
            <dd>{serviceLink}</dd>
          </div>
        ) : null}
        {journey.trip?.block ? (
          <div>
            <dt>Block</dt>
            <dd>
              <a href={`/trips/${journey.trip.id}/block?date=${journey.date}`}>
                {journey.trip.block}
              </a>
            </dd>
          </div>
        ) : null}
        <div>
          <dt>Date</dt>
          <dd>{journey.date}</dd>
        </div>
      </dl>
    </div>
  );
}

function getStopName({
  common_name,
  locality_name,
  indicator,
}: {
  [name: string]: string;
}) {
  let name = common_name;
  if (indicator) {
    if (
      /^(opp|opposite|adj|adjacent|at|o\/s|nr|near|before|after|by|on|in|outside)(\s.*)?$/i.test(
        indicator,
      )
    ) {
      name = `${indicator} ${name}`;
      if (locality_name) {
        return `${locality_name}, ${name}`;
      }
    } else {
      name = `${name} (${indicator})`;
    }
  }
  if (locality_name && !common_name.startsWith(locality_name)) {
    name = `${locality_name} ${name}`;
  }
  return name;
}

export type Operator = {
  noc: string;
  slug: string;
  name: string;
};

export default function BigMap(
  props: {
    operatorSlug?: string;
    operator?: Operator;
    trip?: Trip;
    tripId?: string;
    journeyId?: string;
  } & (
    | {
        mode: MapMode.Journey;
        journeyId: string;
      }
    | {
        mode: MapMode.Trip | MapMode.Operator | MapMode.Slippy;
      }
  ),
) {
  const mapRef = React.useRef<MapGL | null>(null);

  const [trip, setTrip] = React.useState<Trip | undefined>(props.trip);

  const [journey, setJourney] = React.useState<VehicleJourney>();

  // null means not found
  const [operator, setOperator] = React.useState<Operator | null>();

  React.useEffect(() => {
    if (!props.operatorSlug) {
      setOperator(undefined);
      return;
    }
    if (props.operator?.slug === props.operatorSlug) {
      setOperator(props.operator);
      return;
    }
    setOperator(undefined);
    let cancelled = false;
    fetchJson(
      `api/operators/?slug=${encodeURIComponent(props.operatorSlug)}`,
    ).then((response?: { results: Operator[] }) => {
      if (!cancelled) {
        setOperator(response?.results[0] || null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [props.operatorSlug, props.operator]);

  const [vehicles, setVehicles] = React.useState<VehicleLocation[]>();

  const [zoom, setZoom] = React.useState<number>();

  const [clickedStopUrl, setClickedStopURL] = React.useState(() => {
    if (document.referrer && props.mode !== MapMode.Slippy) {
      const referrer = new URL(document.referrer).pathname;
      if (referrer.indexOf("/stops/") === 0) {
        return referrer;
      }
    }
  });

  const [journeyContext] = React.useState(() =>
    new URLSearchParams(window.location.hash.slice(1)).get("context"),
  );

  const [clickedStopFeature, setClickedStopFeature] = React.useState<
    Stop | undefined
  >();

  // hovering a timetable row opens a popup, but doesn't highlight the row -
  // only a clicked stop is highlighted
  const [hoveredStopUrl, setHoveredStopUrl] = React.useState<string>();

  const popupStopUrl = hoveredStopUrl || clickedStopUrl;

  const setStopPopup = React.useCallback((url?: string) => {
    setHoveredStopUrl(undefined);
    setClickedStopURL(url);
  }, []);

  const [tripVehicle, setTripVehicle] = React.useState<VehicleLocation | null>(
    null,
  );

  const initialViewState = useRef(
    props.mode === MapMode.Slippy ? INITIAL_VIEW_STATE : undefined,
  );

  const polylineLocations = useMemo(() => {
    if (journey?.time_aware_polyline) {
      return locationsFromPolyline(
        journey.time_aware_polyline,
        getUtcOffsetSeconds(journey.datetime),
      );
    }
    return [];
  }, [journey?.time_aware_polyline, journey?.datetime]);

  const [appendedLocations, setAppendedLocations] = useState<
    VehicleJourneyLocation[]
  >([]);

  const journeyLocations = useMemo(
    () => polylineLocations.concat(appendedLocations),
    [polylineLocations, appendedLocations],
  );

  // extend the trail as the bus moves
  useEffect(() => {
    if (props.mode !== MapMode.Journey || !tripVehicle) return;
    setAppendedLocations((appended) => {
      const lastTs = appended.length
        ? appended[appended.length - 1].datetime
        : polylineLocations.length
          ? polylineLocations[polylineLocations.length - 1].datetime
          : 0;
      if (tripVehicle.datetime <= lastTs) return appended;
      return appended.concat([
        {
          coordinates: tripVehicle.coordinates,
          datetime: tripVehicle.datetime,
          direction: tripVehicle.heading,
        },
      ]);
    });
  }, [tripVehicle, props.mode, polylineLocations]);

  const bounds = useMemo(() => {
    if (trip) {
      return getBounds(trip.times, (time) => time.stop.location);
    }
    if (journey) {
      let _bounds = getBounds(
        journey.trip?.times,
        (item) => item.stop.location,
      );
      _bounds = getBounds(
        polylineLocations,
        (item) => item.coordinates,
        _bounds,
      );
      _bounds = getBounds(
        tripVehicle ? [tripVehicle] : undefined,
        (item) => item.coordinates,
        _bounds,
      );
      // e.g. stops without locations
      return _bounds?.isEmpty() ? undefined : _bounds;
    }
  }, [trip, journey, polylineLocations, tripVehicle]);

  const fitBoundsOptions = useMemo(() => {
    if (props.mode === MapMode.Slippy || props.mode === MapMode.Operator) {
      return {
        padding: { top: 50, bottom: 150, left: 50, right: 50 },
      };
    }
    return { padding: 50 };
  }, [props.mode]);

  const fittedBoundsFor = useRef<string>(undefined);

  useEffect(() => {
    if (bounds && mapRef.current) {
      const key = trip ? `trip-${trip.id}` : `journey-${journey?.id}`;
      if (fittedBoundsFor.current === key) return;
      fittedBoundsFor.current = key;
      mapRef.current.fitBounds(bounds, { padding: 50 });
    }
  }, [bounds, trip, journey?.id]);

  // slippy map stuff
  const boundsRef = React.useRef<LngLatBounds | null>(null);
  const vehiclesHighWaterMark = React.useRef<LngLatBounds | null>(null);
  const vehiclesTimeout = React.useRef<number | null>(null);
  const vehiclesAbortController = React.useRef<AbortController | null>(null);
  const vehiclesLength = React.useRef<number>(0);
  const vehiclesLoadedAt = React.useRef<number>(0);

  const [loadingBuses, setLoadingBuses] = React.useState(true);

  const loadVehicles = React.useCallback(
    (first = false) => {
      if (!first && document.hidden) {
        return;
      }
      if (vehiclesTimeout.current) {
        clearTimeout(vehiclesTimeout.current);
      }

      if (vehiclesAbortController.current) {
        vehiclesAbortController.current.abort();
        vehiclesAbortController.current = null;
      }

      let _bounds: LngLatBounds;
      let url: string | undefined;
      switch (props.mode) {
        case MapMode.Slippy:
          if (boundsRef.current) {
            _bounds = boundsRef.current;
            url = getBoundsQueryString(_bounds);
          }
          break;
        case MapMode.Operator:
          if (operator) {
            url = `?operator=${operator.noc}`;
          }
          break;
        case MapMode.Trip:
          if (trip?.service?.id) {
            url = `?service=${trip.service.id}`;
          }
          break;
        case MapMode.Journey:
          if (journey?.live) {
            if (journey.service?.id) {
              url = `?service=${journey.service.id}`;
            } else if (journey.vehicle?.id) {
              url = `?id=${journey.vehicle.id}`;
            } else {
              // journey-based tracking with no vehicle (e.g. FlixBus) -
              // the journey id is used in lieu of a vehicle id
              url = `?id=${journey.id}`;
            }
          }
          break;
      }
      if (!url) {
        return;
      }

      const handleItems = (items: VehicleLocation[]) => {
        vehiclesHighWaterMark.current = _bounds;

        if (props.mode === MapMode.Operator && first) {
          const bounds = getBounds(items, (item) => item.coordinates);
          if (bounds) {
            const fitBoundsOptions = {
              padding: { top: 50, bottom: 150, left: 50, right: 50 },
            };
            if (mapRef.current) {
              // navigated here from another map
              mapRef.current.fitBounds(bounds, fitBoundsOptions);
            } else if (!initialViewState.current) {
              initialViewState.current = { bounds, fitBoundsOptions };
            }
          }
        }

        if (items.length || vehiclesLength.current || first) {
          if (trip || journey) {
            for (const item of items) {
              if (
                (trip && trip.id === item.trip_id) ||
                (journey &&
                  (journey.vehicle?.id
                    ? journey.vehicle.id === item.id
                    : journey.id === item.journey_id))
              ) {
                if (first) setClickedVehicleMarker(item.id);
                setTripVehicle(item);
                break;
              }
            }
          }

          vehiclesLength.current = items.length;
          setVehicles(items);
        }
      };

      setLoadingBuses(true);

      vehiclesAbortController.current = new AbortController();

      return fetch(`${apiRoot}vehicles.json${url}`, {
        credentials: "omit",
        signal: vehiclesAbortController.current.signal,
      })
        .then(
          (response) => {
            recordSkew(response);
            if (response.ok || response.status === 404) {
              vehiclesLoadedAt.current = Date.now();
              response.json().then(handleItems);

              setLoadingBuses(false);
            }

            if (!document.hidden) {
              vehiclesTimeout.current = window.setTimeout(loadVehicles, 12000); // 12 seconds
            }
          },
          () => {
            // never mind
            // setLoadingBuses(false);
          },
        )
        .catch(() => {
          // never mind
          // setLoadingBuses(false);
        });
    },
    [props.mode, operator, trip, journey],
  );

  const loadJourney = React.useCallback(() => {
    fetchJson(
      `api/vehiclejourneys/${props.journeyId}/details.json${journeyContext ? `?context=${journeyContext}` : ""}`,
    ).then((journey: VehicleJourney) => {
      vehiclesLoadedAt.current = Date.now();
      setJourney(journey);
      const item = journey.vehicle?.id
        ? (journey.live?.find((v) => v.id === journey.vehicle?.id) ?? null)
        : // journey-based tracking with no vehicle (e.g. FlixBus)
          (journey.live?.find((v) => v.journey_id === journey.id) ?? null);
      // sort of duplicating `handleItems`
      vehiclesHighWaterMark.current = null;
      setVehicles(journey.live);
      vehiclesLength.current = journey.live?.length || 0;
      if (item) {
        setClickedVehicleMarker(item?.id);
      }
      setTripVehicle(item);
    });
  }, [props.journeyId, journeyContext]);

  React.useEffect(() => {
    if (vehiclesTimeout.current) {
      clearTimeout(vehiclesTimeout.current);
    }
    if (vehiclesAbortController.current) {
      vehiclesAbortController.current.abort();
      vehiclesAbortController.current = null;
    }
    setAppendedLocations([]);
    if (props.tripId) {
      // trip mode
      if (trip?.id?.toString() === props.tripId) {
        loadVehicles(true);
        document.title = `${trip.service?.line_name} \u2013 ${trip.operator?.name} \u2013 bustimes.org`;
      } else {
        setJourney(undefined);
        fetchJson(`api/trips/${props.tripId}.json`).then(setTrip);
      }
    } else if (props.operatorSlug) {
      setJourney(undefined);
      setTrip(undefined);
      // operator mode
      if (operator) {
        document.title = `Map \u2013 ${operator.name} \u2013 bustimes.org`;
        loadVehicles(true);
      }
    } else if (props.journeyId) {
      // journey mode
      if (journey?.id?.toString() === props.journeyId) {
        if (!document.hidden) {
          vehiclesTimeout.current = window.setTimeout(loadVehicles, 12000); // 12 seconds
        }
      } else {
        setTrip(undefined);
        loadJourney();
      }
    } else {
      setJourney(undefined);
      setTrip(undefined);
      // slippy mode
      document.title = "Map \u2013 bustimes.org";
      loadVehicles();
    }
  }, [
    props.tripId,
    trip,
    props.operatorSlug,
    operator,
    props.journeyId,
    journey,
    loadVehicles,
    loadJourney,
  ]);

  const handleMoveEnd = React.useCallback(
    (evt: ViewStateChangeEvent) => {
      const _bounds = evt.target.getBounds();
      const _zoom = evt.viewState.zoom;
      setZoom(_zoom);
      boundsRef.current = _bounds;
      updateHash(evt.target);

      if (!evt.originalEvent) {
        return;
      }

      if (vehiclesTimeout.current) {
        clearTimeout(vehiclesTimeout.current);
        setLoadingBuses(false);
      }

      if (shouldShowVehicles(_zoom)) {
        if (
          !containsBounds(vehiclesHighWaterMark.current, boundsRef.current) ||
          vehiclesLength.current >= 1000
        ) {
          setLoadingBuses(true);
          vehiclesTimeout.current = window.setTimeout(loadVehicles, 200);
        } else {
          // we've zoomed in, so already have all the vehicles in this bounding box
          vehiclesTimeout.current = window.setTimeout(loadVehicles, 12000);
        }
      }
      updateLocalStorage(_zoom, evt.target.getCenter());
    },
    [loadVehicles],
  );

  // (re)load vehicles on tab visibility change
  React.useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden) {
        return;
      }
      if (
        props.mode === MapMode.Journey &&
        journey?.live &&
        journey.time_aware_polyline &&
        Date.now() - vehiclesLoadedAt.current > 60000
      ) {
        // the trail would have a gap in it, so get a fresh polyline
        loadJourney();
      } else {
        loadVehicles();
      }
    };

    window.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [loadVehicles, loadJourney, props.mode, journey]);

  const [clickedVehicleMarkerId, setClickedVehicleMarker] =
    React.useState<number>();

  const handleMapClick = React.useCallback(
    (e: MapLayerMouseEvent) => {
      // handle click on VehicleMarker element
      const vehicleId = getClickedVehicleMarkerId(e);
      if (vehicleId) {
        setClickedVehicleMarker(vehicleId);
        setClickedStopURL(undefined);
        setHoveredStopUrl(undefined);
        setHoveredLocation(null);
        setClickedStopFeature(undefined);
        return;
      }

      // handle click on maplibre rendered feature
      if (e.features?.length) {
        for (const feature of e.features) {
          if (feature.layer.id === "vehicles" && feature.properties.id) {
            setClickedVehicleMarker(feature.properties.id);
            return;
          }
          if (feature.layer.id.startsWith("stops")) {
            const url = feature.properties.url;
            setHoveredStopUrl(undefined);
            if (url !== clickedStopUrl) {
              setClickedStopURL(url);
              setHoveredLocation(null);
              if (props.mode === MapMode.Slippy) {
                const name = getStopName(feature.properties);

                const services = feature.properties.line_names?.split(",");

                setClickedStopFeature({
                  type: "Feature",
                  properties: { url, name, services },
                  geometry: feature.geometry as {
                    type: "Point";
                    coordinates: [number, number];
                  },
                });
              }
            }
            break;
          }
        }
      } else {
        setClickedStopURL(undefined);
        setHoveredStopUrl(undefined);
        setClickedStopFeature(undefined);
      }
      setClickedVehicleMarker(undefined);
    },
    [clickedStopUrl, props.mode],
  );

  const handleMapInit = React.useCallback(
    (map: MapGL) => {
      mapRef.current = map;
      map.once("remove", () => {
        if (mapRef.current === map) {
          mapRef.current = null;
        }
      });

      if (props.mode === MapMode.Slippy) {
        if (!boundsRef.current) {
          // first load
          hash._map = map;
          hash._onHashChange();
          const _zoom = map.getZoom();
          boundsRef.current = map.getBounds();
          setZoom(_zoom);

          if (shouldShowVehicles(_zoom)) {
            setLoadingBuses(true);
            loadVehicles();
          }
        }
      }
    },
    [props.mode, loadVehicles],
  );

  useEffect(() => {
    if (props.mode === MapMode.Slippy && mapRef.current) {
      updateHash(mapRef.current);
    }
  }, [props.mode]);

  const [cursor, setCursor] = React.useState<string>();

  const [hoveredLocation, setHoveredLocation] = React.useState<{
    coordinates: [number, number];
    time: string;
  } | null>(null);

  const onMouseEnter = React.useCallback((e: MapLayerMouseEvent) => {
    const vehicleId = getClickedVehicleMarkerId(e);
    if (vehicleId) {
      return;
    }

    if (e.features?.length) {
      setCursor("pointer");
      for (const feature of e.features) {
        if (feature.layer.id === "locations") {
          const geom = feature.geometry as {
            type: "Point";
            coordinates: [number, number];
          };
          setHoveredLocation({
            coordinates: geom.coordinates,
            time: feature.properties?.time,
          });
          return;
        }
      }
      setHoveredLocation(null);
    } else {
      setHoveredLocation(null);
    }
  }, []);

  const onMouseLeave = React.useCallback(() => {
    setCursor(undefined);
    setHoveredLocation(null);
  }, []);

  const handleRowHover = React.useCallback((a: TripTime) => {
    if (a.stop.location && a.stop.atco_code) {
      setHoveredStopUrl(`/stops/${a.stop.atco_code}`);
    }
  }, []);

  const handleRowClick = React.useCallback((a: TripTime) => {
    if (a.stop.location && a.stop.atco_code) {
      setHoveredStopUrl(undefined);
      setClickedStopURL(`/stops/${a.stop.atco_code}`);
    }
  }, []);

  const showStops = shouldShowStops(zoom);
  const showBuses = props.mode !== MapMode.Slippy || shouldShowVehicles(zoom);

  const breadcrumb =
    props.operatorSlug && operator ? (
      <ol className="breadcrumb">
        <li>
          <a href="/">Home</a>
        </li>
        <li>
          <a href={`/operators/${operator.slug}`}>{operator.name}</a>
        </li>
      </ol>
    ) : null;

  if (props.mode === MapMode.Operator) {
    if (operator === null) {
      return (
        <LoadingSorry
          text={
            <>
              <p>Sorry, operator not found</p>
              <p>
                <a href="/map">Go to the main map?</a>
              </p>
            </>
          }
        />
      );
    }
    if (!vehicles || (!operator && !vehiclesLength.current)) {
      return (
        <div className="big-map">
          {breadcrumb}
          <LoadingSorry />
        </div>
      );
    }
    if (operator && !vehiclesLength.current) {
      return (
        <div className="big-map">
          {breadcrumb}
          <LoadingSorry
            text={
              <>
                <p>
                  Sorry, no {operator.name} buses are tracking at the moment
                </p>
                <p>
                  <a href="/map">Go to the main map?</a>
                </p>
              </>
            }
          />
        </div>
      );
    }
  }

  if (props.mode === MapMode.Journey && !journey && !mapRef.current) {
    return <LoadingSorry />;
  }

  // nothing to show on a map (no stops, history or live location)
  if (props.mode === MapMode.Journey && !bounds && !mapRef.current) {
    return (
      <JourneySidebar
        journey={journey}
        journeyId={props.journeyId}
        journeyContext={journeyContext}
        vehicle={tripVehicle}
        onHover={handleRowHover}
      />
    );
  }

  let className = "big-map";
  if (props.mode === MapMode.Trip || props.mode === MapMode.Journey) {
    className += " has-sidebar";
  }

  return (
    <React.Fragment>
      {props.mode !== MapMode.Slippy && (
        <Link className="map-link" href="/map">
          Map
        </Link>
      )}
      <div className={className}>
        {breadcrumb}
        <BusTimesMap
          initialViewState={
            initialViewState.current || { bounds, fitBoundsOptions }
          }
          onMoveEnd={props.mode === MapMode.Slippy ? handleMoveEnd : undefined}
          onClick={handleMapClick}
          onMouseEnter={onMouseEnter}
          onMouseMove={
            props.mode === MapMode.Journey ? onMouseEnter : undefined
          }
          onMouseLeave={onMouseLeave}
          cursor={cursor}
          onMapInit={handleMapInit}
          interactiveLayerIds={[
            "stops",
            "stops-circle",
            "vehicles",
            "locations",
          ]}
        >
          {props.mode === MapMode.Trip && trip ? (
            <Route times={trip.times} />
          ) : null}

          {props.mode === MapMode.Journey && journey?.trip?.times ? (
            <Route times={journey.trip.times} />
          ) : null}

          {props.mode === MapMode.Slippy ? (
            <Stops
              clickedStopFeature={clickedStopFeature}
              setClickedStop={(url) => {
                setClickedStopURL(url);
                if (!url) setClickedStopFeature(undefined);
              }}
            />
          ) : trip ? (
            <Stops
              times={trip.times}
              clickedStopUrl={popupStopUrl}
              setClickedStop={setStopPopup}
            />
          ) : props.mode === MapMode.Journey && journey?.trip?.times ? (
            <Stops
              times={journey.trip.times}
              clickedStopUrl={popupStopUrl}
              setClickedStop={setStopPopup}
            />
          ) : null}

          {props.mode === MapMode.Journey && journeyLocations.length ? (
            <Locations locations={journeyLocations} />
          ) : null}

          {vehicles && showBuses ? (
            <Vehicles
              vehicles={vehicles}
              tripId={props.tripId}
              journeyId={props.journeyId}
              clickedVehicleMarkerId={clickedVehicleMarkerId}
              setClickedVehicleMarker={setClickedVehicleMarker}
            />
          ) : null}

          {zoom &&
          ((props.mode === MapMode.Slippy && !showStops) || loadingBuses) ? (
            <div className="maplibregl-ctrl map-status-bar">
              {props.mode === MapMode.Slippy && !showStops
                ? "Zoom in to see stops"
                : null}
              {!showBuses ? <div>Zoom in to see buses</div> : null}
              {showBuses && loadingBuses ? <div>Loading…</div> : null}
            </div>
          ) : null}

          {hoveredLocation ? (
            <Popup
              longitude={hoveredLocation.coordinates[0]}
              latitude={hoveredLocation.coordinates[1]}
              closeButton={false}
              closeOnClick={false}
              // offset={8}
              focusAfterOpen={false}
              className="location-popup"
            >
              {hoveredLocation.time}
            </Popup>
          ) : null}
        </BusTimesMap>
      </div>

      {props.mode === MapMode.Trip ? (
        <TripSidebar
          trip={trip}
          tripId={props.tripId}
          vehicle={tripVehicle}
          highlightedStop={clickedStopUrl}
          onHover={handleRowHover}
        />
      ) : null}

      {props.mode === MapMode.Journey ? (
        <JourneySidebar
          journey={journey}
          journeyId={props.journeyId}
          journeyContext={journeyContext}
          vehicle={tripVehicle}
          highlightedStop={clickedStopUrl}
          onHover={handleRowHover}
        />
      ) : null}
    </React.Fragment>
  );
}
