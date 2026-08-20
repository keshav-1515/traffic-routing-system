from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .camera_config import CameraConfig
from .video_metrics import VideoMetrics
from .video_source import VideoSource


VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


class VideoPipelineError(RuntimeError):
    pass


class VideoCVPipeline:
    """Automated YOLOv8 + ByteTrack traffic analytics pipeline.

    The pipeline is deliberately independent of Flask. Existing CVManager code can
    consume get_metrics() and map the returned fields into its existing Metrics model.
    """

    def __init__(self, config: CameraConfig):
        self.config = config
        self.source = VideoSource(config.video_source, config.target_fps)
        self.model = None
        self.running = False
        self.frame_number = 0
        self.started_at = 0.0
        self.last_metrics = VideoMetrics(config.camera_id, config.video_source)
        self._previous_centers: dict[int, tuple[float, float]] = {}
        self._track_classes: dict[int, str] = {}
        self._track_age: dict[int, int] = defaultdict(int)
        self._counted_ids: set[int] = set()
        self._counted_classes: dict[int, str] = {}
        self._speed_history: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=8))
        self._last_tick = 0.0
        self._latency_ms = 0.0

    def _load_model(self) -> None:
        if self.model is not None:
            return
        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise VideoPipelineError(
                "Ultralytics is not installed. Run: python -m pip install ultralytics"
            ) from exc
        weights = self.config.model_weights
        if not Path(weights).exists() and weights not in {"yolov8n.pt", "yolov8s.pt", "yolov8m.pt"}:
            raise VideoPipelineError(f"YOLO weights not found: {weights}")
        device = None if self.config.device == "auto" else self.config.device
        self.model = YOLO(weights)
        if device:
            self.model.to(device)

    def start(self) -> None:
        self._load_model()
        self.source.open()
        self.running = True
        self.started_at = time.monotonic()
        self.last_metrics.error = None

    def stop(self) -> None:
        self.running = False
        self.source.release()

    def _scaled_polygon(self, normalized: list[tuple[float, float]]) -> np.ndarray:
        return np.asarray(
            self.config.scale_polygon(normalized, self.source.width, self.source.height),
            dtype=np.int32,
        )

    def _inside(self, point: tuple[float, float], polygon: np.ndarray) -> bool:
        return cv2.pointPolygonTest(polygon, point, False) >= 0

    def _count_entry(self, track_id: int, class_name: str, inside: bool, previous_inside: bool) -> None:
        if not inside or previous_inside:
            return
        if self._track_age[track_id] < self.config.min_track_age_frames:
            return
        if track_id in self._counted_ids and not self.config.count_reentry:
            return
        self._counted_ids.add(track_id)
        self._counted_classes[track_id] = class_name

    def process_frame(self, frame: np.ndarray) -> VideoMetrics:
        if self.model is None:
            raise VideoPipelineError("Pipeline not started")
        t0 = time.perf_counter()
        self.frame_number += 1
        roi_polygon = self._scaled_polygon(self.config.roi_polygon)
        count_polygon = self._scaled_polygon(self.config.counting_zone_polygon)
        results = self.model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=list(VEHICLE_CLASSES),
            conf=self.config.confidence,
            verbose=False,
        )
        result = results[0]
        class_counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
        current_tracks: dict[str, dict[str, Any]] = {}
        active_ids: set[int] = set()
        speeds: list[float] = []
        if result.boxes is not None and result.boxes.id is not None:
            ids = result.boxes.id.int().cpu().tolist()
            cls_ids = result.boxes.cls.int().cpu().tolist()
            confs = result.boxes.conf.cpu().tolist()
            boxes = result.boxes.xyxy.cpu().tolist()
            for track_id, cls_id, conf, box in zip(ids, cls_ids, confs, boxes):
                if cls_id not in VEHICLE_CLASSES:
                    continue
                x1, y1, x2, y2 = map(float, box)
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                # Filter to camera ROI.
                if not self._inside((cx, cy), roi_polygon):
                    continue
                class_name = VEHICLE_CLASSES[cls_id]
                active_ids.add(track_id)
                self._track_age[track_id] += 1
                self._track_classes[track_id] = class_name
                class_counts[class_name] += 1
                previous = self._previous_centers.get(track_id)
                previous_inside = False
                if previous is not None:
                    previous_inside = self._inside(previous, count_polygon)
                    pixel_distance = float(np.hypot(cx - previous[0], cy - previous[1]))
                    if self.config.speed_enabled and self.config.pixels_per_meter and self._last_tick > 0:
                        dt = max(time.perf_counter() - self._last_tick, 1e-3)
                        speed_mps = pixel_distance / self.config.pixels_per_meter / dt
                        self._speed_history[track_id].append(speed_mps * 3.6)
                        speeds.append(float(np.mean(self._speed_history[track_id])))
                inside = self._inside((cx, cy), count_polygon)
                self._count_entry(track_id, class_name, inside, previous_inside)
                self._previous_centers[track_id] = (cx, cy)
                current_tracks[str(track_id)] = {
                    "track_id": track_id,
                    "class": class_name,
                    "confidence": round(float(conf), 4),
                    "bbox": [x1, y1, x2, y2],
                    "center": [cx, cy],
                    "inside_counting_zone": inside,
                    "age_frames": self._track_age[track_id],
                }
        self._last_tick = time.perf_counter()
        total = sum(class_counts.values())
        density = min(1.0, len(active_ids) / max(self.config.max_density_capacity, 1))
        avg_speed = float(np.mean(speeds)) if speeds else None
        congestion = density
        if avg_speed is not None and self.config.speed_enabled:
            speed_factor = max(0.0, min(1.0, 1.0 - (avg_speed / 60.0)))
            congestion = 0.65 * density + 0.35 * speed_factor
        elapsed = max(time.monotonic() - self.started_at, 1e-6)
        proc_fps = self.frame_number / elapsed
        self._latency_ms = (time.perf_counter() - t0) * 1000.0
        counted_by_class = {k: 0 for k in class_counts}
        for class_name in self._counted_classes.values():
            if class_name in counted_by_class:
                counted_by_class[class_name] += 1
        self.last_metrics = VideoMetrics(
            camera_id=self.config.camera_id,
            source=self.config.video_source,
            mode="video",
            cars=class_counts["car"],
            motorcycles=class_counts["motorcycle"],
            buses=class_counts["bus"],
            trucks=class_counts["truck"],
            total_vehicles=total,
            active_tracked=len(active_ids),
            counted_vehicles=len(self._counted_ids),
            average_speed_kmh=avg_speed,
            min_speed_kmh=min(speeds) if speeds else None,
            max_speed_kmh=max(speeds) if speeds else None,
            traffic_density=round(float(density), 4),
            congestion_score=round(float(max(0.0, min(1.0, congestion))), 4),
            frame_number=self.frame_number,
            processing_fps=round(float(proc_fps), 2),
            inference_latency_ms=round(float(self._latency_ms), 2),
            tracks=current_tracks,
            counted_by_zone={"main": len(self._counted_ids)},
        )
        return self.last_metrics

    def step(self) -> VideoMetrics | None:
        if not self.running:
            return None
        ok, frame = self.source.read()
        if not ok or frame is None:
            self.stop()
            return self.last_metrics
        return self.process_frame(frame)

    def run(self, max_frames: int | None = None) -> VideoMetrics:
        if not self.running:
            self.start()
        processed = 0
        while self.running:
            self.step()
            processed += 1
            if max_frames is not None and processed >= max_frames:
                break
        return self.last_metrics

    def get_metrics(self) -> dict[str, Any]:
        return self.last_metrics.as_dict()
