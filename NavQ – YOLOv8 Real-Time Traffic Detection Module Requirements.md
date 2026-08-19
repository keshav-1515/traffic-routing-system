# NavQ – YOLOv8 Real-Time Traffic Detection Module

## 1. Project Overview

NavQ is a real-time traffic congestion and navigation application built using Flutter. The application displays a map, calculates routes between two locations, identifies traffic congestion on roads, and provides alternative/eco-friendly routes.

The current application already has:

- Interactive map visualization
- Current-location detection
- Start and destination selection
- Driving and walking route calculation
- Road-network-based routing
- Traffic congestion values associated with road segments
- Predicted congestion values
- Metro station detection using OpenStreetMap/Overpass
- EcoSaver mode
- Metro-based routing
- Nearby metro stations, schools and hospitals
- GPS journey tracking
- Eco Credits based on metro usage

The missing/improvable component is a **real-time computer-vision-based traffic detection system**.

The goal of the YOLOv8 module is to analyze live traffic-camera/video footage and convert vehicle detections into meaningful traffic information that NavQ can use to calculate road congestion.

---

# 2. What the YOLOv8 System Should Do

The YOLOv8 model should process traffic-camera footage or a live video stream and detect vehicles in real time.

At minimum, detect:

- Car
- Motorcycle
- Bus
- Truck
- Auto-rickshaw, if possible
- Bicycle, if useful
- Person, optionally

The important output is not simply:

> "There are 25 cars."

The system should produce something closer to:

> Road segment X currently has 18 cars, 7 motorcycles and 2 buses, with an estimated congestion level of 0.72.

This information will eventually be sent to the NavQ backend.

---

# 3. Required YOLOv8 Pipeline

The intended pipeline is:

Camera / Video
↓
YOLOv8 Object Detection
↓
Vehicle Classification
↓
Object Tracking
↓
Vehicle Counting
↓
Traffic-Density Estimation
↓
Road/Camera Mapping
↓
Congestion Score
↓
NavQ Backend API
↓
Flutter Map
↓
Route Calculation

YOLOv8 therefore forms the **computer vision layer** of NavQ.

---

# 4. Detection Requirements

Use YOLOv8 for real-time object detection.

Preferred models:

- YOLOv8n – fastest, suitable for real-time/mobile experimentation
- YOLOv8s – better accuracy while remaining relatively lightweight
- YOLOv8m – if GPU resources are available and accuracy is more important

Start with YOLOv8n or YOLOv8s.

The system should output for every detected vehicle:

```json
{
  "class": "car",
  "confidence": 0.91,
  "bbox": [x1, y1, x2, y2],
  "track_id": 24
}
```

The `track_id` is important because the same vehicle should not be counted repeatedly in every frame.

---

# 5. Object Tracking

YOLO detection alone is not sufficient.

A tracking algorithm should be used after detection.

Possible choices:

- ByteTrack
- BoT-SORT
- DeepSORT

Preferred:

**YOLOv8 + ByteTrack**

This allows the system to maintain a unique ID for each vehicle.

For example:

Frame 1:
Car → ID 17

Frame 2:
Car → ID 17

Frame 3:
Car → ID 17

The system should count this as **one vehicle**, not three vehicles.

---

# 6. Vehicle Counting

The system should support two types of traffic measurements.

### A. Current vehicle count

Example:

```text
Cars: 24
Motorcycles: 13
Buses: 2
Trucks: 4
Total: 43
```

### B. Vehicle flow

If possible, determine how many vehicles cross a virtual line during a specific time interval.

Example:

```text
Vehicles crossing line:
0–10 sec  → 8
10–20 sec → 11
20–30 sec → 7
```

This can be used to estimate traffic flow.

Line-crossing is preferred over simply counting objects visible in a frame.

---

# 7. Traffic Density

The system should convert detections into a normalized traffic-density value.

Use a value between:

```text
0.0 → 1.0
```

where:

```text
0.0 = very low traffic
1.0 = extremely congested
```

For example:

```text
0.00 – 0.30 → Low
0.30 – 0.60 → Moderate
0.60 – 0.80 → Heavy
0.80 – 1.00 → Severe
```

The exact thresholds can be calibrated using real traffic footage.

Do NOT simply define congestion as:

```text
number_of_cars / arbitrary_constant
```

if possible.

The system should consider:

- Number of vehicles
- Road/camera region
- Vehicle occupancy
- Traffic flow
- Vehicle speed
- Road capacity
- Queue length
- Average movement speed

---

# 8. Speed Estimation

If possible, estimate vehicle speed from tracking.

For example:

```text
Average speed = 42 km/h
```

or:

```text
Average speed = 8 km/h
```

This is extremely useful because 30 vehicles moving at 40 km/h do not represent the same congestion as 30 vehicles moving at 3 km/h.

Speed estimation may require camera calibration/homography for accurate real-world speed.

If accurate speed estimation is difficult initially, implement vehicle counting and tracking first.

---

# 9. Congestion Score

The final output should ideally contain a congestion score:

```text
congestion_score ∈ [0,1]
```

For example:

```text
Vehicle density     = 0.78
Average speed       = 0.22 normalized
Traffic flow        = 0.65

Final congestion    = 0.74
```

The exact formula can be designed collaboratively with the NavQ backend.

A possible conceptual model is:

```text
Congestion =
    weighted(vehicle_density)
  + weighted(speed_reduction)
  + weighted(queue_length)
  + weighted(flow)
```

The output must ultimately be normalized to `[0,1]`.

---

# 10. Road/Camera Mapping

This is one of the MOST IMPORTANT requirements.

The YOLO system will normally know:

> Camera 3 has 27 vehicles.

NavQ needs:

> Road segment `abc-123` has congestion 0.72.

Therefore each camera/video source must be associated with a geographic location and preferably a road segment.

Example:

```json
{
  "camera_id": "CAM_001",
  "latitude": 12.9716,
  "longitude": 77.5946,
  "road_id": "road_1024"
}
```

The YOLO service can then produce:

```json
{
  "road_id": "road_1024",
  "vehicle_count": 43,
  "cars": 25,
  "motorcycles": 12,
  "buses": 3,
  "trucks": 3,
  "average_speed_kmh": 11.4,
  "congestion_score": 0.78,
  "timestamp": "..."
}
```

This is what the NavQ backend can consume.

---

# 11. Real-Time API Requirement

The YOLO module should expose an API so that the NavQ backend can obtain the latest traffic information.

A possible endpoint:

```text
POST /api/traffic/update
```

Example request:

```json
{
  "camera_id": "CAM_001",
  "road_id": "road_1024",
  "vehicle_count": 43,
  "cars": 25,
  "motorcycles": 12,
  "buses": 3,
  "trucks": 3,
  "average_speed_kmh": 11.4,
  "congestion_score": 0.78,
  "timestamp": "2026-08-19T10:30:00"
}
```

The backend can then update its traffic database/cache.

---

# 12. Expected Data Frequency

The system should ideally update traffic information periodically.

For example:

```text
YOLO inference: continuous
↓
Tracking: continuous
↓
Traffic aggregation: every 5–10 seconds
↓
Backend update: every 5–10 seconds
```

The Flutter application does NOT need to receive every individual YOLO frame.

It only needs the aggregated traffic state.

This significantly reduces network and processing requirements.

---

# 13. Integration With Existing NavQ Architecture

The current NavQ application already expects road segments to contain traffic information.

The existing road model contains concepts such as:

```text
road_id
road geometry
congestion
road length
capacity
free-flow time
current speed
road type
```

The YOLO module should therefore ultimately populate/update:

```text
road_id
congestion_score
current_speed_kmh
vehicle_count
timestamp
```

The Flutter application can then visualize roads using the congestion level.

Conceptually:

```text
YOLOv8
   ↓
Traffic Detection Server
   ↓
Traffic Database / Cache
   ↓
NavQ Backend
   ↓
/api/traffic
   ↓
Flutter TrafficProvider
   ↓
Map
```

---

# 14. Map Visualization

NavQ already uses congestion levels to determine road visualization.

Conceptually:

```text
Green  → Low congestion
Yellow → Moderate congestion
Red    → Heavy congestion
```

Therefore the YOLO module does NOT need to draw the final Flutter map.

Its responsibility is to provide reliable traffic measurements.

---

# 15. Route Optimization Integration

The eventual objective is:

User enters:

```text
A → B
```

NavQ obtains the road network and current traffic conditions.

Example:

```text
Road A → congestion 0.20
Road B → congestion 0.82
Road C → congestion 0.35
```

The routing algorithm should then prefer a route that minimizes a traffic-aware cost rather than simply minimizing geographic distance.

For example:

```text
Route cost =
distance cost
+
time cost
+
traffic congestion penalty
```

Therefore YOLO's output directly influences route selection.

---

# 16. Prediction Layer

The current NavQ architecture also has a traffic-prediction concept.

The YOLO system should therefore preserve historical traffic measurements.

Example:

```text
10:00 → 0.42
10:05 → 0.51
10:10 → 0.63
10:15 → 0.71
10:20 → 0.76
```

This historical data can later be used for:

- Time-series forecasting
- LSTM
- XGBoost
- Random Forest
- Temporal models
- Other traffic prediction methods

The YOLO module itself does NOT necessarily need to implement prediction initially.

Its main responsibility is obtaining reliable real-time traffic observations.

---

# 17. Dataset Requirements

The model should preferably be trained/fine-tuned using traffic-specific datasets.

The dataset should contain:

- Different vehicle types
- Different traffic densities
- Day/night conditions
- Different camera angles
- Different weather conditions
- Occluded vehicles
- Dense traffic
- Indian traffic if possible

Indian traffic footage would be especially useful because NavQ is intended for Indian road conditions.

If the standard YOLOv8 pretrained model performs sufficiently well, start with it and then fine-tune using a traffic-specific dataset.

---

# 18. Important Edge Cases

The model should handle:

- Vehicles partially hidden behind other vehicles
- Motorcycles between cars
- Large buses/trucks
- Stationary vehicles
- Traffic jams
- Vehicles entering/leaving the camera frame
- Shadows
- Different lighting conditions
- Night traffic
- Rain
- Camera vibration
- Dense intersections

Tracking should prevent duplicate counting.

---

# 19. Performance Requirements

The system should prioritize real-time performance.

Target:

```text
Inference: preferably 15–30+ FPS
```

depending on hardware.

For a prototype:

```text
YOLOv8n + ByteTrack
```

is a good starting point.

GPU acceleration should be used when available.

Possible deployment:

```text
Python
Ultralytics YOLOv8
OpenCV
ByteTrack / BoT-SORT
FastAPI or Flask
```

---

# 20. Recommended Software Architecture

Suggested structure:

```text
traffic_ai/
│
├── models/
│   └── best.pt
│
├── detection/
│   ├── detector.py
│   └── tracker.py
│
├── traffic/
│   ├── counter.py
│   ├── density.py
│   ├── speed.py
│   └── congestion.py
│
├── cameras/
│   └── camera_config.json
│
├── api/
│   └── server.py
│
├── main.py
└── requirements.txt
```

---

# 21. Minimum Viable Version

For the first version, implement only:

```text
Video
 ↓
YOLOv8
 ↓
Vehicle detection
 ↓
ByteTrack
 ↓
Vehicle counting
 ↓
Traffic density
 ↓
Congestion score
 ↓
JSON API
```

Once this works reliably, add:

```text
Speed estimation
↓
Road mapping
↓
Multiple cameras
↓
Real-time backend integration
↓
Historical data
↓
Traffic prediction
```

---

# 22. Final Output Expected From the YOLO Module

For every monitored road/camera, the system should ideally produce something like:

```json
{
  "camera_id": "CAM_001",
  "road_id": "road_1024",
  "timestamp": "2026-08-19T10:30:15Z",

  "vehicles": {
    "car": 25,
    "motorcycle": 12,
    "bus": 3,
    "truck": 3
  },

  "total_vehicles": 43,

  "average_speed_kmh": 11.4,

  "traffic_density": 0.81,

  "congestion_score": 0.78,

  "traffic_level": "heavy"
}
```

This JSON is the key interface between the YOLO project and NavQ.

---

# 23. What I Need From You

The most important thing is:

**Don't build only a YOLO vehicle detector. Build a YOLO + tracking + traffic analytics system whose output can be consumed by a navigation backend.**

The final objective is:

```text
CAMERA
  ↓
YOLOv8
  ↓
Vehicle Detection
  ↓
ByteTrack
  ↓
Vehicle Count / Flow / Speed
  ↓
Traffic Density
  ↓
Congestion Score (0–1)
  ↓
Road ID
  ↓
NavQ Backend
  ↓
Traffic-Aware Routing
  ↓
Flutter Map
```

The YOLO module should be developed independently from the Flutter frontend, with a clean REST API between the two systems. This will allow us to replace the current simulated/static traffic values with actual computer-vision-derived real-time traffic data without rewriting the entire NavQ application.