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
  useMap,
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
import TripTimetable, { type Trip } from "./TripTimetable";
import VehiclePopup from "./VehiclePopup";
import { recordSkew } from "./clockSkew";
import { getBounds, getFont } from "./utils";

const apiRoot = process.env.API_ROOT;

declare global {
  interface Window {
    INITIAL_VIEW_STATE: MapProps["initialViewState"];
  }
}

const updateLocalStorage = debounce((zoom: number, latLng) => {
  try {
    localStorage.setItem("vehicleMap", `${zoom}/${latLng.lat}/${latLng.lng}`);
  } catch (e) {
    // never mind
  }
}, 2000);

if (window.INITIAL_VIEW_STATE && !window.location.hash) {
  try {
    if (localStorage.vehicleMap) {
      const parts = localStorage.vehicleMap.split("/");
      if (parts.length === 3) {
        window.INITIAL_VIEW_STATE = {
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
  // console.log(a, b);
  // if (a) {
  //   console.log("N", a.getNorth(), b.getNorth(), a.getNorth() >= b.getNorth());
  //   console.log("E ", a.getEast(), b.getEast(), a.getEast() >= b.getEast());
  //   console.log("S ", a.getSouth(), b.getSouth(), a.getSouth() <= b.getSouth());
  //   console.log("W ", a.getWest(), b.getWest(), a.getWest() <= b.getWest());
  // }

  // console.log(a?.contains(b.getNorthWest()) && a.contains(b.getSouthEast()));
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

function SlippyMapHash() {
  const mapRef = useMap();

  React.useEffect(() => {
    if (mapRef.current) {
      const map = mapRef.current.getMap();
      const hash = map._hash || new Hash();
      map._hash = hash;
      hash.addTo(map);
      return () => {
        hash.remove();
      };
    }
  }, [mapRef]);

  return null;
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
              "icon-rotate": ["+", 45, ["get", "bearing"]],
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
  return fetch(`/${url}`, {
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
  const vehiclesById = React.useMemo<{ [id: string]: VehicleLocation }>(() => {
    return Object.assign({}, ...vehicles.map((item) => ({ [item.id]: item })));
  }, [vehicles]);

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
              id: vehicle.id,
              geometry: {
                type: "Point" as const,
                coordinates: vehicle.coordinates,
              },
              properties: {
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

  const clickedVehicle =
    clickedVehicleMarkerId && vehiclesById[clickedVehicleMarkerId];

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
  vehicle?: VehicleLocation;
  highlightedStop?: string;
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
        <ul className="breadcrumb">
          {operator}
          {service}
        </ul>
      ) : null}
      <TripTimetable
        trip={trip}
        vehicle={props.vehicle}
        highlightedStop={props.highlightedStop}
      />
    </div>
  );
}

function JourneySidebar(props: {
  journey: VehicleJourney;
  journeyId: string;
  highlightedStop?: string;
  vehicle?: VehicleLocation;
}) {
  let className = "trip-timetable map-sidebar";

  const journey = props.journey;

  let operator: ReactElement | undefined;
  if (journey.operator) {
    operator = (
      <li>
        <a href={`/operators/${journey.operator.slug}`}>
          {journey.operator.name}
        </a>
      </li>
    );
  }

  let service: ReactElement | undefined;
  if (journey.service) {
    service = (
      <li>
        <a
          href={`/services/${journey.service.slug}/vehicles?date=${journey.date}#journey-${journey.id}`}
        >
          {journey.route_name}
        </a>
      </li>
    );
  } else if (journey.operator) {
    service = (
      <li>
        <a
          href={`/services/${journey.operator.noc}:${journey.route_name}/vehicles?date=${journey.date}#journey-${journey.id}`}
        >
          {journey.route_name}
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
        <ul className="breadcrumb">
          {operator}
          {service}
        </ul>
      ) : null}
      {journey.times ? (
        <TripTimetable
          trip={{ times: journey.times }}
          vehicle={props.vehicle}
          highlightedStop={props.highlightedStop}
        />
      ) : null}
    </div>
  );
}

function getStopName({
  common_name,
  locality_name,
  indicator,
}: { [name: string]: string }) {
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

export default function BigMap(
  props: {
    noc?: string;
    trip?: Trip;
    tripId?: string;
    vehicleId?: number;
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

  const [clickedStopFeature, setClickedStopFeature] = React.useState<
    Stop | undefined
  >();

  const [tripVehicle, setTripVehicle] = React.useState<VehicleLocation>();

  const initialViewState = useRef(window.INITIAL_VIEW_STATE);

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

  const journeyIsCurrent = useMemo(() => {
    if (!journey) return false;
    return Date.now() - new Date(journey.datetime).getTime() < 4 * 3600 * 1000;
  }, [journey]);

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
      const _bounds = getBounds(journey.times, (item) => item.stop.location);
      return getBounds(polylineLocations, (item) => item.coordinates, _bounds);
    }
  }, [trip, journey, polylineLocations]);

  const fitBoundsOptions = useMemo(() => {
    if (props.mode === MapMode.Slippy || props.mode === MapMode.Operator) {
      return {
        padding: { top: 50, bottom: 150, left: 50, right: 50 },
      };
    }
    return { padding: 50 };
  }, [props.mode]);

  useEffect(() => {
    if (bounds && mapRef.current) {
      mapRef.current.fitBounds(bounds, { padding: 50 });
    }
  }, [bounds]);

  // slippy map stuff
  const boundsRef = React.useRef<LngLatBounds | null>(null);
  const vehiclesHighWaterMark = React.useRef<LngLatBounds | null>(null);
  const vehiclesTimeout = React.useRef<number | null>(null);
  const vehiclesAbortController = React.useRef<AbortController | null>(null);
  const vehiclesLength = React.useRef<number>(0);

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
          url = `?operator=${props.noc}`;
          break;
        case MapMode.Trip:
          if (props.vehicleId) {
            url = `?id=${props.vehicleId}`;
          } else if (trip?.service?.id) {
            url = `?service=${trip.service.id}&trip=${trip.id}`;
          }
          break;
        case MapMode.Journey:
          if (!journeyIsCurrent) {
            // historic journey — don't poll for live vehicles
            return;
          }
          if (journey?.service?.id) {
            url = `?service=${journey.service.id}`;
            if (journey.trip_id) {
              url += `&trip=${journey.trip_id}`;
            }
          } else if (journey?.vehicle?.id) {
            url = `?id=${journey.vehicle.id}`;
          }
          break;
      }
      if (!url) {
        return;
      }

      setLoadingBuses(true);

      vehiclesAbortController.current = new AbortController();

      return fetch(`/vehicles.json${url}`, {
        credentials: "omit",
        signal: vehiclesAbortController.current.signal,
      })
        .then(
          (response) => {
            recordSkew(response);
            if (response.ok || response.status === 404) {
              response.json().then((items: VehicleLocation[]) => {
                vehiclesHighWaterMark.current = _bounds;

                if (
                  props.mode === MapMode.Operator &&
                  !initialViewState.current
                ) {
                  const bounds = getBounds(items, (item) => item.coordinates);
                  if (bounds) {
                    initialViewState.current = {
                      bounds,
                      fitBoundsOptions: {
                        padding: { top: 50, bottom: 150, left: 50, right: 50 },
                      },
                    };
                  }
                }

                if (items.length || vehiclesLength.current || first) {
                  if (trip || journey?.vehicle?.id) {
                    for (const item of items) {
                      if (
                        (trip && trip.id === item.trip_id) ||
                        journey?.vehicle?.id === item.id
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
              });

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
    [props.mode, props.noc, trip, journey, journeyIsCurrent, props.vehicleId],
  );

  React.useEffect(() => {
    setAppendedLocations([]);
    if (props.tripId) {
      // trip mode
      if (trip?.id?.toString() === props.tripId) {
        loadVehicles(true);
        document.title = `${trip.service?.line_name} \u2013 ${trip.operator?.name} \u2013 bustimes.org`;
      } else {
        setJourney(undefined);
        fetchJson(`api/trips/${props.tripId}/`).then(setTrip);
      }
    } else if (props.noc) {
      setJourney(undefined);
      setTrip(undefined);
      // operator mode
      if (props.noc === trip?.operator?.noc) {
        document.title = `Bus tracker map \u2013 ${trip.operator.name} \u2013 bustimes.org`;
      }
      loadVehicles(true);
    } else if (props.journeyId) {
      // journey mode
      if (journey?.id?.toString() === props.journeyId) {
        if (journeyIsCurrent) {
          loadVehicles(true);
        }
      } else {
        setTrip(undefined);
        fetchJson(`api/vehiclejourneys/${props.journeyId}/`).then(
          (journey: VehicleJourney) => {
            setJourney({ ...journey, id: props.journeyId });
          },
        );
      }
    } else if (!props.vehicleId) {
      setJourney(undefined);
      setTrip(undefined);
      // slippy mode
      document.title = "Map \u2013 bustimes.org";
    } else {
      loadVehicles();
    }
  }, [
    props.tripId,
    trip,
    props.noc,
    props.vehicleId,
    props.journeyId,
    journey,
    journeyIsCurrent,
    loadVehicles,
  ]);

  const handleMoveEnd = React.useCallback(
    (evt: ViewStateChangeEvent) => {
      if (vehiclesTimeout.current) {
        clearTimeout(vehiclesTimeout.current);
        setLoadingBuses(false);
      }

      const _bounds = evt.target.getBounds();
      const _zoom = evt.viewState.zoom;
      setZoom(_zoom);
      boundsRef.current = _bounds;

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
      if (!document.hidden) {
        loadVehicles();
      }
    };

    window.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [loadVehicles]);

  const [clickedVehicleMarkerId, setClickedVehicleMarker] = React.useState<
    number | undefined
  >(props.vehicleId);

  const handleMapClick = React.useCallback(
    (e: MapLayerMouseEvent) => {
      // handle click on VehicleMarker element
      const vehicleId = getClickedVehicleMarkerId(e);
      if (vehicleId) {
        setClickedVehicleMarker(vehicleId);
        setClickedStopURL(undefined);
        setClickedStopFeature(undefined);
        return;
      }

      // handle click on maplibre rendered feature
      if (e.features?.length) {
        for (const feature of e.features) {
          if (feature.layer.id === "vehicles" && feature.id) {
            setClickedVehicleMarker(feature.id as number);
            return;
          }
          if (feature.layer.id === "stops") {
            const url = feature.properties.url;
            if (url !== clickedStopUrl) {
              setClickedStopURL(url);
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
        setClickedStopFeature(undefined);
      }
      setClickedVehicleMarker(undefined);
    },
    [clickedStopUrl, props.mode],
  );

  const handleMapInit = React.useCallback(
    (map: MapGL) => {
      mapRef.current = map;

      if (props.mode === MapMode.Slippy) {
        if (!boundsRef.current) {
          // first load
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

  const showStops = shouldShowStops(zoom);
  const showBuses = props.mode !== MapMode.Slippy || shouldShowVehicles(zoom);

  if (props.mode === MapMode.Operator) {
    if (!vehicles) {
      return <LoadingSorry />;
    }
    if (!vehiclesLength.current) {
      return (
        <LoadingSorry
          text={
            <>
              <p>Sorry, no buses are tracking at the moment</p>
              <p>
                <a href="/map">Go to the main map?</a>
              </p>
            </>
          }
        />
      );
    }
  }

  if (props.mode === MapMode.Journey && !journey && !mapRef.current) {
    return <LoadingSorry />;
  }

  let className = "big-map";
  if (props.mode === MapMode.Trip || props.mode === MapMode.Journey) {
    className += " has-sidebar";
  }
  // console.dir(bounds);
  // console.dir(journey);
  // console.dir(initialViewState.current);

  return (
    <React.Fragment>
      {props.mode !== MapMode.Slippy && (
        <Link className="map-link" href="/map">
          Map
        </Link>
      )}
      <div className={className}>
        <BusTimesMap
          initialViewState={
            initialViewState.current || { bounds, fitBoundsOptions }
          }
          onMoveEnd={props.mode === MapMode.Slippy ? handleMoveEnd : undefined}
          hash={props.mode === MapMode.Slippy}
          onClick={handleMapClick}
          onMouseEnter={onMouseEnter}
          onMouseMove={
            props.mode === MapMode.Journey ? onMouseEnter : undefined
          }
          onMouseLeave={onMouseLeave}
          cursor={cursor}
          onMapInit={handleMapInit}
          interactiveLayerIds={["stops", "vehicles", "locations"]}
        >
          {/* bounds on the map for debugging */}
          {/* bounds ? (
            <Source
              type="geojson"
              data={{
                type: "Feature",
                geometry: {
                  type: "Polygon",
                  coordinates: [
                    [
                      [bounds.getWest(), bounds.getNorth()],
                      [bounds.getEast(), bounds.getNorth()],
                      [bounds.getEast(), bounds.getSouth()],
                      [bounds.getWest(), bounds.getSouth()],
                      [bounds.getWest(), bounds.getNorth()],
                    ],
                  ],
                },
              }}
            >
              <Layer
                {...{
                  id: "bounds",
                  type: "line",
                  paint: {
                    "line-color": "#000",
                    "line-width": 2,
                  },
                }}
              />
            </Source>
          ) : null*/}

          {props.mode === MapMode.Trip && trip ? (
            <Route times={trip.times} />
          ) : null}

          {props.mode === MapMode.Journey && journey?.times ? (
            <Route times={journey.times} />
          ) : null}

          {/* props.mode === MapMode.Slippy ? <SlippyMapHash /> : null */}

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
              clickedStopUrl={clickedStopUrl}
              setClickedStop={setClickedStopURL}
            />
          ) : props.mode === MapMode.Journey && journey?.times ? (
            <Stops
              times={journey.times}
              clickedStopUrl={clickedStopUrl}
              setClickedStop={setClickedStopURL}
            />
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

          {props.mode === MapMode.Journey && journeyLocations.length ? (
            <Locations locations={journeyLocations} />
          ) : null}

          {hoveredLocation ? (
            <Popup
              longitude={hoveredLocation.coordinates[0]}
              latitude={hoveredLocation.coordinates[1]}
              closeButton={false}
              closeOnClick={false}
              offset={8}
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
        />
      ) : null}

      {props.mode === MapMode.Journey && journey ? (
        <JourneySidebar
          journey={journey}
          journeyId={props.journeyId}
          vehicle={tripVehicle}
          highlightedStop={clickedStopUrl}
        />
      ) : null}
    </React.Fragment>
  );
}
