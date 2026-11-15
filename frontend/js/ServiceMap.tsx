import React, { lazy, Suspense } from "react";
import { createPortal } from "react-dom";

import loadjs from "loadjs";
import LoadingSorry from "./LoadingSorry";
import type { ServiceMapMapProps } from "./ServiceMapMap";
import type { Vehicle } from "./VehicleMarker";
import { recordSkew } from "./clockSkew";

const ServiceMapMap = lazy(() => import("./ServiceMapMap"));

let hasCss = false;

declare global {
  interface Window {
    LIVERIES_CSS_URL: string;
    STREAM?: boolean;
    SERVICES?: {
      id: number;
      line_names: string[];
    }[];
  }
}

type ServiceMapProps = {
  serviceId: number;
  buttonText: string;
};

const mapContainer = document.getElementById("map");

export default function ServiceMap({ serviceId, buttonText }: ServiceMapProps) {
  const [isOpen, setOpen] = React.useState(() => {
    return window.location.hash === "#map";
  });

  const closeMap = React.useCallback(() => {
    if (isOpen) {
      if (window.history.state?.hasHistory) {
        window.history.back();
      } else {
        window.location.hash = "";
      }
    }
  }, [isOpen]);

  const [vehicles, setVehicles] = React.useState<Vehicle[]>();

  const [stopsAndGeometry, setStopsAndGeometry] = React.useState<
    ServiceMapMapProps["stopsAndGeometry"]
  >({});

  const [selectedServices, setSelectedServices] = React.useState(
    new Set<number>([serviceId]),
  );

  React.useEffect(() => {
    const handleHashChange = () => {
      if (window.location.hash === "#map") {
        setOpen(true);
        window.history.replaceState({ hasHistory: true }, "");
      } else {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      // ESC
      if (isOpen && event.key === "Escape") {
        closeMap();
      }
    };

    window.addEventListener("hashchange", handleHashChange);
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      window.removeEventListener("hashchange", handleHashChange);
      window.removeEventListener("keydown", handleKeyDown);
    };
  });

  const handleSelectService = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const serviceId = Number.parseInt(event.target.value, 10);
      if (event.target.checked) {
        selectedServices.add(serviceId);
      } else {
        selectedServices.delete(serviceId);
      }
      setSelectedServices(new Set(selectedServices));
    },
    [selectedServices],
  );

  const first = React.useRef(true);

  React.useEffect(() => {
    const loadStops = (serviceId: number) => {
      fetch(`/services/${serviceId}.json`).then(
        (response) => {
          if (response.ok) {
            response.json().then((data) => {
              setStopsAndGeometry({
                ...stopsAndGeometry,
                [serviceId]: data,
              });
            });
          }
        },
        () => {
          // never mind
        },
      );
    };

    if (isOpen) {
      for (const serviceId of Array.from(selectedServices)) {
        if (!stopsAndGeometry[serviceId]) {
          loadStops(serviceId);
        }
      }
    }
  }, [isOpen, selectedServices, stopsAndGeometry]);

  React.useEffect(() => {
    if (isOpen) {
      document.body.classList.add("has-overlay");
      if (!hasCss) {
        loadjs(window.LIVERIES_CSS_URL, () => {
          hasCss = true;
        });
      }
    } else {
      document.body.classList.remove("has-overlay");
    }
  }, [isOpen]);

  React.useEffect(() => {
    let timeout: number;
    let events: EventSource | undefined;
    let streaming = false;
    let loading = false;
    let current: Vehicle[] = [];

    const serviceIds = Array.from(selectedServices).join(",");

    const loadVehicles = () => {
      if ((document.hidden && !first.current) || !selectedServices.size) {
        return;
      }

      loading = true;
      fetch(`/vehicles.json?service=${serviceIds}`).then(
        (response) => {
          recordSkew(response);
          response.json().then((items) => {
            loading = false;
            current = items;
            setVehicles(items);
            clearTimeout(timeout);
            if (isOpen && !document.hidden && (items.length || streaming)) {
              // when the event stream is working, poll only to remove expired vehicles
              timeout = window.setTimeout(
                loadVehicles,
                streaming ? 60000 : 10000,
              );
            }
          });
        },
        () => {
          loading = false;
        },
      );
    };

    const connect = () => {
      events = new EventSource(`/stream/services/${serviceIds}`);
      events.onopen = () => {
        streaming = true;
      };
      events.onerror = () => {
        // (the browser will try to reconnect)
        if (streaming) {
          streaming = false;
          loadVehicles();
        }
      };
      events.onmessage = (event) => {
        const items: Vehicle[] = JSON.parse(event.data);
        const updates = new Map(items.map((item) => [item.id, item]));
        current = current.map((vehicle) => {
          const item = updates.get(vehicle.id);
          if (!item) {
            return vehicle;
          }
          updates.delete(vehicle.id);
          // keep the extra stuff (livery etc) that vehicles.json adds
          return {
            ...vehicle,
            ...item,
            service: vehicle.service && { ...vehicle.service, ...item.service },
          };
        });
        setVehicles(current);
        if (updates.size && !loading) {
          loadVehicles(); // a new vehicle
        }
      };
    };

    if (isOpen || first.current) {
      loadVehicles();
    }
    first.current = false;

    const handleVisibilityChange = () => {
      if (document.hidden) {
        clearTimeout(timeout);
        events?.close();
        streaming = false;
      } else {
        loadVehicles();
        if (window.STREAM) {
          connect();
        }
      }
    };

    if (isOpen) {
      if (window.STREAM && selectedServices.size) {
        connect();
      }
      window.addEventListener("visibilitychange", handleVisibilityChange);
    }

    return () => {
      window.removeEventListener("visibilitychange", handleVisibilityChange);
      clearTimeout(timeout);
      events?.close();
    };
  }, [isOpen, selectedServices]);

  if (vehicles) {
    buttonText = "Map";
    const count = vehicles.length;
    if (count === 1) {
      buttonText += ` (tracking ${count} bus)`;
    } else if (count) {
      buttonText += ` (tracking ${count} buses)`;
    }
  }

  const button = <a href="#map">{buttonText}</a>;

  if (!isOpen || !mapContainer) {
    return button;
  }

  const closeButton = (
    <button type="button" onClick={closeMap} className="map-button">
      Close map
    </button>
  );

  return (
    <React.Fragment>
      {button}
      {createPortal(
        <div className="service-map">
          {closeButton}
          {window.SERVICES ? (
            <div className="map-select-services">
              {window.SERVICES.map((service) => (
                <label key={service.id}>
                  <input
                    type="checkbox"
                    value={service.id}
                    disabled={service.id === serviceId}
                    checked={selectedServices.has(service.id)}
                    onChange={handleSelectService}
                  />{" "}
                  {service.line_names.join(", ")}
                </label>
              ))}
            </div>
          ) : null}
          <Suspense fallback={<LoadingSorry />}>
            <ServiceMapMap
              vehicles={vehicles}
              stopsAndGeometry={stopsAndGeometry}
              serviceIds={selectedServices}
            />
          </Suspense>
        </div>,
        mapContainer,
      )}
    </React.Fragment>
  );
}
