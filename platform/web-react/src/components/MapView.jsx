import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";

// Leaflet's default marker icon paths break under a bundler (webpack/Vite both
// rewrite asset URLs) unless explicitly re-pointed at the bundled asset URLs — a
// well-known Leaflet+bundler gotcha, fixed once here so every page that uses markers
// doesn't have to rediscover it.
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({ iconRetinaUrl: markerIcon2x, iconUrl: markerIcon, shadowUrl: markerShadow });

export const PUNE_CENTER = [18.5204, 73.8567];

/**
 * A thin imperative wrapper around Leaflet (not react-leaflet) — this app already
 * needs fine-grained imperative control (marker updates on every WS tick, map
 * clicks feeding external state), which react-leaflet's declarative model fights
 * more than it helps for this specific access pattern. `onReady` hands back the
 * raw Leaflet map instance for the page to drive directly.
 */
export default function MapView({ center = PUNE_CENTER, zoom = 13, onReady, onClick, style }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);

  useEffect(() => {
    if (mapRef.current) return;
    const map = L.map(containerRef.current).setView(center, zoom);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap",
    }).addTo(map);
    mapRef.current = map;
    onReady?.(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !onClick) return;
    map.on("click", onClick);
    return () => map.off("click", onClick);
  }, [onClick]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%", ...style }} />;
}
