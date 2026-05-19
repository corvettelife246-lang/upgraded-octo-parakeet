"""Camera interface — live capture, snapshot, and frame streaming via OpenCV."""
import asyncio
import base64
import io
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Optional

from config.settings import (
    CAMERA_INDEX,
    SNAPSHOT_DIR,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)

logger = logging.getLogger(__name__)


class Camera:
    def __init__(self, index: Optional[int] = None) -> None:
        self._index = index if index is not None else CAMERA_INDEX
        self._cap = None

    def open(self) -> bool:
        try:
            import cv2
            self._cap = cv2.VideoCapture(self._index)
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, VIDEO_WIDTH)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, VIDEO_HEIGHT)
            self._cap.set(cv2.CAP_PROP_FPS, VIDEO_FPS)
            return self._cap.isOpened()
        except ImportError:
            logger.error("opencv-python not installed. Run: pip install opencv-python")
            return False

    def close(self) -> None:
        if self._cap and self._cap.isOpened():
            self._cap.release()
            self._cap = None

    def read_frame(self) -> Optional[bytes]:
        """Return JPEG-encoded frame bytes or None."""
        if not self._cap or not self._cap.isOpened():
            return None
        import cv2
        ret, frame = self._cap.read()
        if not ret:
            return None
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    def snapshot(self, filename: Optional[str] = None) -> Optional[Path]:
        """Save a snapshot to disk and return the path."""
        frame_bytes = self.read_frame()
        if frame_bytes is None:
            return None
        if not filename:
            filename = f"snapshot_{int(time.time())}.jpg"
        out_path = SNAPSHOT_DIR / filename
        out_path.write_bytes(frame_bytes)
        logger.info("Snapshot saved: %s", out_path)
        return out_path

    def snapshot_b64(self) -> Optional[str]:
        """Return a base64-encoded JPEG snapshot."""
        frame_bytes = self.read_frame()
        if frame_bytes is None:
            return None
        return base64.b64encode(frame_bytes).decode("utf-8")

    async def stream_frames(self, fps: int = VIDEO_FPS) -> AsyncIterator[bytes]:
        """Async generator yielding JPEG bytes at the requested frame rate."""
        interval = 1.0 / fps
        while True:
            frame = await asyncio.to_thread(self.read_frame)
            if frame:
                yield frame
            await asyncio.sleep(interval)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()
