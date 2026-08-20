# NavQ

NavQ is the Flutter commuter client for an AI-based intelligent traffic-management platform. It combines the existing Python traffic graph with dynamic routing, congestion visualization, 15-minute prediction integration, EcoSaver, carbon savings, credits, rewards, profile and theme persistence.

## Run

```bash
flutter pub get
flutter run
```

## Backend

From the project root, install the Python requirements used by the existing backend, then run:

```bash
python virtual_world.py --place "Koramangala, Bangalore, India"
```

For offline/demo mode:

```bash
python virtual_world.py
```

## API URL

`lib/config/api_constants.dart` uses `http://10.0.2.2:5000` for an Android emulator. On a physical phone, replace it with your PC's LAN IP, for example `http://192.168.1.10:5000`.

If the backend cannot be reached, NavQ uses clearly labelled demo/offline data. The 15-minute prediction fallback is a heuristic and is **not** represented as a trained ML model.

## Main features

- Interactive traffic map
- Green/yellow/red congestion road segments
- Start/destination selection and swap
- Current GPS location
- Car/walking route modes with real mode-specific road routing
- Dynamic traffic backend plus real-world Valhalla/OSRM route fallback
- 15-minute prediction API abstraction
- EcoSaver carbon-saving route architecture
- Eco credits and rewards
- Profile and theme persistence
- Android app name: NavQ
- Supplied logo used for the launcher icon

## NavQ routing and profile updates

This build adds:

- Nearby discovery of mapped metro stations, schools and hospitals using OpenStreetMap/Overpass data.
- EcoSaver multimodal routing: walk to a nearby metro station, estimated metro leg, then walk to the destination.
- Metro feasibility filtering based on station proximity; the app does not invent a transit route when no suitable station pair is found.
- GPS geofencing during an active journey: once the user reaches the origin metro station, the **Board metro** action becomes available.
- Distance-scaled Eco Credits for the metro leg, with one-time credit awarding per eco journey.
- Trip history stored locally on the device.
- Working Profile interactions for preferred travel mode, trip history, notification preferences, location permissions, privacy and About NavQ.
- Dark mode keeps the map on a high-contrast Voyager basemap for readability.

### Transit-data note

The current prototype uses mapped metro stations and an estimated metro-leg time/distance. A production transit implementation should connect a city-specific GTFS/GTFS-Realtime or OpenTripPlanner feed so station-to-station connectivity, line changes, fares, and live departures are authoritative.
