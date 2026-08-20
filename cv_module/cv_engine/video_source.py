from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


class VideoSourceError(RuntimeError):
    pass


class VideoSource:
    def __init__(self, source: str | Path, target_fps: float = 5.0):
        self.source = Path(source)
        self.target_fps = float(target_fps)
        self.cap: cv2.VideoCapture | None = None
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self.frame_count = 0
        self.duration_seconds = 0.0
        self._last_emit = 0.0

    def open(self) -> None:
        if not self.source.exists():
            raise VideoSourceError(f"Video file not found: {self.source}")
        self.cap = cv2.VideoCapture(str(self.source))
        if not self.cap.isOpened():
            self.release()
            raise VideoSourceError(f"Could not open video: {self.source}")
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if self.fps <= 0:
            self.fps = 30.0
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.duration_seconds = self.frame_count / self.fps if self.frame_count > 0 else 0.0
        self._last_emit = 0.0

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self.cap is None:
            self.open()
        assert self.cap is not None
        return self.cap.read()

    def frames(self) -> Iterator[tuple[int, np.ndarray]]:
        if self.cap is None:
            self.open()
        assert self.cap is not None
        frame_number = 0
        interval = 1.0 / max(self.target_fps, 1e-6)
        while True:
            ok, frame = self.cap.read()
            if not ok:
                break
            frame_number += 1
            now = time.monotonic()
            if self.target_fps >= self.fps or now - self._last_emit >= interval:
                self._last_emit = now
                yield frame_number, frame

    def reset(self) -> None:
        if self.cap is None:
            self.open()
        assert self.cap is not None
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self._last_emit = 0.0

    def release(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
