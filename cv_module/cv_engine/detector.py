"""YOLO-based detector with graceful fallback."""
from .schemas import Detection

ALLOWED = set(['car', 'motorcycle', 'bus', 'truck'])


class DummyDetector:
    """Simple deterministic stub used when YOLO is unavailable."""
    def __init__(self, *a, **k):
        pass

    def detect(self, frame):
        # return empty list — mock generator provides metrics
        return []


class YOLODetector:
    def __init__(self, model_path=None, conf_thresh=0.35):
        # lazy import
        try:
            from ultralytics import YOLO
        except Exception as e:
            raise
        self.model = YOLO(model_path or 'yolov8n.pt')
        self.conf_thresh = conf_thresh
        # map class ids to names if available
        self.names = getattr(self.model, 'model', None)

    def detect(self, frame):
        # frame may be None — model must be called with an actual image
        if frame is None:
            return []
        results = self.model.predict(frame, imgsz=640, conf=self.conf_thresh)
        dets = []
        for r in results:
            boxes = getattr(r, 'boxes', None)
            if boxes is None:
                continue
            for b in boxes:
                try:
                    cls_id = int(b.cls.cpu().numpy()[0]) if hasattr(b, 'cls') else None
                except Exception:
                    cls_id = None
                conf = float(b.conf.cpu().numpy()[0]) if hasattr(b, 'conf') else float(b.conf)
                xyxy = b.xyxy.cpu().numpy()[0].tolist() if hasattr(b, 'xyxy') else list(b.xyxy[0])
                # resolve class name if possible
                name = None
                if cls_id is not None:
                    try:
                        name = self.model.names.get(cls_id, str(cls_id))
                    except Exception:
                        name = str(cls_id)
                if name not in ALLOWED:
                    continue
                x1, y1, x2, y2 = xyxy[:4]
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                dets.append(Detection(cls=name, confidence=conf, bbox=(x1, y1, x2, y2), center=(cx, cy)))
        return dets
