from pathlib import Path

import cv2
import numpy as np
import yaml
from ultralytics import YOLO


CONFIG_PATH = Path("cv_engine/configs/camera_01.yaml")


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Config not found: {path.resolve()}"
        )

    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError("Camera configuration is empty or invalid.")

    return config


def point_inside_polygon(
    x: float,
    y: float,
    polygon: list[list[int]],
) -> bool:
    points = np.array(polygon, dtype=np.int32)

    return (
        cv2.pointPolygonTest(
            points,
            (float(x), float(y)),
            False,
        )
        >= 0
    )


def main() -> None:
    config = load_config(CONFIG_PATH)

    # --------------------------------------------------------
    # VIDEO CONFIG
    # --------------------------------------------------------

    video_path = Path(config["video_source"])

    video_cfg = config.get("video", {})
    target_fps = float(video_cfg.get("target_fps", 5))

    if target_fps <= 0:
        raise ValueError("video.target_fps must be greater than 0.")

    # --------------------------------------------------------
    # MODEL CONFIG
    # --------------------------------------------------------

    model_cfg = config.get("model", {})

    model_path = model_cfg.get(
        "weights",
        "yolov8s.pt",
    )

    confidence = float(
        model_cfg.get(
            "confidence",
            0.20,
        )
    )

    if not 0.0 < confidence <= 1.0:
        raise ValueError(
            "model.confidence must be between 0 and 1."
        )

    # --------------------------------------------------------
    # CLASS CONFIG
    # --------------------------------------------------------

    class_config = config.get("classes", {})

    if not class_config:
        raise ValueError("No vehicle classes configured.")

    # YAML:
    #
    # car: 2
    # motorcycle: 3
    # bus: 5
    # truck: 7
    #
    # Internal mapping becomes:
    #
    # 2 -> car
    # 3 -> motorcycle
    # 5 -> bus
    # 7 -> truck

    class_map = {
        int(class_id): str(class_name)
        for class_name, class_id in class_config.items()
    }

    # --------------------------------------------------------
    # ROI CONFIG
    # --------------------------------------------------------

    roi_cfg = config.get("roi", {})
    roi_polygon = roi_cfg.get("polygon")

    if not roi_polygon or len(roi_polygon) < 3:
        raise ValueError(
            "ROI polygon must contain at least 3 points."
        )

    # --------------------------------------------------------
    # VIDEO VALIDATION
    # --------------------------------------------------------

    if not video_path.exists():
        raise FileNotFoundError(
            f"Video not found: {video_path.resolve()}"
        )

    print(f"Loading YOLO model: {model_path}")
    model = YOLO(model_path)
    print("YOLO model loaded.")

    # --------------------------------------------------------
    # OPEN VIDEO
    # --------------------------------------------------------

    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open video: {video_path.resolve()}"
        )

    fps = cap.get(cv2.CAP_PROP_FPS)

    if fps <= 0:
        fps = 30.0

    width = int(
        cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    )

    height = int(
        cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    )

    total_frames = int(
        cap.get(cv2.CAP_PROP_FRAME_COUNT)
    )

    duration = (
        total_frames / fps
        if total_frames > 0
        else 0.0
    )

    frame_interval = max(
        1,
        int(round(fps / target_fps)),
    )

    print()
    print("=" * 55)
    print("VIDEO INFORMATION")
    print("=" * 55)
    print(f"Resolution : {width} x {height}")
    print(f"FPS        : {fps:.2f}")
    print(f"Frames     : {total_frames}")
    print(f"Duration   : {duration:.2f} sec")
    print(f"Target FPS : {target_fps:.2f}")
    print(
        f"Frame step : {frame_interval}"
    )
    print("=" * 55)
    print()

    # --------------------------------------------------------
    # TRACK / COUNT STATE
    # --------------------------------------------------------

    counted_ids: set[int] = set()

    previous_centers: dict[int, tuple[float, float]] = {}

    counts = {
        "car": 0,
        "motorcycle": 0,
        "bus": 0,
        "truck": 0,
    }

    frame_number = 0
    processed_frames = 0

    window_name = "Automated Traffic CV"

    cv2.namedWindow(
        window_name,
        cv2.WINDOW_NORMAL,
    )

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    while True:
        ok, frame = cap.read()

        if not ok:
            break

        frame_number += 1

        if (
            frame_number % frame_interval
            != 0
        ):
            continue

        processed_frames += 1

        # ----------------------------------------------------
        # YOLO + BYTETRACK
        # ----------------------------------------------------

        results = model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=list(class_map.keys()),
            conf=confidence,
            verbose=False,
        )

        result = results[0]

        current_ids: set[int] = set()

        if (
            result.boxes is not None
            and result.boxes.id is not None
        ):
            boxes = result.boxes

            track_ids = (
                boxes.id
                .int()
                .cpu()
                .tolist()
            )

            class_ids = (
                boxes.cls
                .int()
                .cpu()
                .tolist()
            )

            confidences = (
                boxes.conf
                .cpu()
                .tolist()
            )

            coordinates = (
                boxes.xyxy
                .cpu()
                .tolist()
            )

            for (
                track_id,
                class_id,
                conf,
                box,
            ) in zip(
                track_ids,
                class_ids,
                confidences,
                coordinates,
            ):
                if class_id not in class_map:
                    continue

                vehicle_type = class_map[
                    class_id
                ]

                x1, y1, x2, y2 = box

                center_x = (
                    x1 + x2
                ) / 2.0

                center_y = (
                    y1 + y2
                ) / 2.0

                current_ids.add(track_id)

                inside_roi = point_inside_polygon(
                    center_x,
                    center_y,
                    roi_polygon,
                )

                previous = previous_centers.get(
                    track_id
                )

                was_inside = False

                if previous is not None:
                    was_inside = point_inside_polygon(
                        previous[0],
                        previous[1],
                        roi_polygon,
                    )

                # ------------------------------------------------
                # COUNT UNIQUE VEHICLE WHEN IT ENTERS ROI
                # ------------------------------------------------

                entered_roi = (
                    inside_roi
                    and not was_inside
                )

                # If the vehicle's first observed frame is already
                # inside the ROI, count it once as well.
                first_seen_inside = (
                    previous is None
                    and inside_roi
                )

                if (
                    (
                        entered_roi
                        or first_seen_inside
                    )
                    and track_id
                    not in counted_ids
                ):
                    counted_ids.add(track_id)

                    if vehicle_type in counts:
                        counts[
                            vehicle_type
                        ] += 1

                    print(
                        "VEHICLE COUNTED | "
                        f"ID={track_id} | "
                        f"TYPE={vehicle_type} | "
                        f"TOTAL={sum(counts.values())}"
                    )

                previous_centers[
                    track_id
                ] = (
                    center_x,
                    center_y,
                )

                # ------------------------------------------------
                # DRAW VEHICLE
                # ------------------------------------------------

                box_color = (
                    (0, 255, 0)
                    if inside_roi
                    else (255, 0, 0)
                )

                cv2.rectangle(
                    frame,
                    (
                        int(x1),
                        int(y1),
                    ),
                    (
                        int(x2),
                        int(y2),
                    ),
                    box_color,
                    2,
                )

                label = (
                    f"{vehicle_type} "
                    f"ID:{track_id} "
                    f"{conf:.2f}"
                )

                cv2.putText(
                    frame,
                    label,
                    (
                        int(x1),
                        max(
                            20,
                            int(y1) - 8,
                        ),
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )

                cv2.circle(
                    frame,
                    (
                        int(center_x),
                        int(center_y),
                    ),
                    4,
                    (0, 0, 255),
                    -1,
                )

        # ----------------------------------------------------
        # DRAW ROI
        # ----------------------------------------------------

        roi_points = np.array(
            roi_polygon,
            dtype=np.int32,
        )

        overlay = frame.copy()

        cv2.fillPoly(
            overlay,
            [roi_points],
            (0, 180, 255),
        )

        frame = cv2.addWeighted(
            overlay,
            0.10,
            frame,
            0.90,
            0,
        )

        cv2.polylines(
            frame,
            [roi_points],
            True,
            (0, 255, 255),
            3,
        )

        cv2.putText(
            frame,
            "DETECTION / COUNTING ROI",
            (
                int(roi_polygon[0][0]),
                max(
                    30,
                    int(roi_polygon[0][1]) - 10,
                ),
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )

        # ----------------------------------------------------
        # DRAW METRICS
        # ----------------------------------------------------

        total_count = sum(
            counts.values()
        )

        metrics = [
            f"Cars passed: {counts['car']}",
            (
                "Motorcycles passed: "
                f"{counts['motorcycle']}"
            ),
            f"Buses passed: {counts['bus']}",
            f"Trucks passed: {counts['truck']}",
            f"TOTAL PASSED: {total_count}",
            f"TRACKED NOW: {len(current_ids)}",
        ]

        cv2.rectangle(
            frame,
            (10, 10),
            (380, 225),
            (0, 0, 0),
            -1,
        )

        for index, text in enumerate(
            metrics
        ):
            cv2.putText(
                frame,
                text,
                (
                    20,
                    40 + index * 32,
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                (
                    0.68
                    if index < 5
                    else 0.72
                ),
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

        # ----------------------------------------------------
        # SHOW FULL VIDEO
        # ----------------------------------------------------

        cv2.imshow(
            window_name,
            frame,
        )

        key = (
            cv2.waitKey(1)
            & 0xFF
        )

        if key == 27:
            break

    # --------------------------------------------------------
    # CLEANUP
    # --------------------------------------------------------

    cap.release()
    cv2.destroyAllWindows()

    # --------------------------------------------------------
    # FINAL RESULTS
    # --------------------------------------------------------

    print()
    print("=" * 55)
    print("FINAL VEHICLE COUNTS")
    print("=" * 55)

    print(
        f"Cars:         {counts['car']}"
    )

    print(
        "Motorcycles:  "
        f"{counts['motorcycle']}"
    )

    print(
        f"Buses:        {counts['bus']}"
    )

    print(
        f"Trucks:       {counts['truck']}"
    )

    print(
        f"TOTAL PASSED: "
        f"{sum(counts.values())}"
    )

    print(
        f"UNIQUE IDS:   "
        f"{len(counted_ids)}"
    )

    print(
        f"FRAMES:       "
        f"{processed_frames}"
    )

    print("=" * 55)


if __name__ == "__main__":
    main()