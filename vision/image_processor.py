"""Image processing utilities — resize, convert, annotate, and encode frames."""
import base64
import io
from pathlib import Path
from typing import Optional


class ImageProcessor:
    @staticmethod
    def bytes_to_b64(image_bytes: bytes) -> str:
        return base64.b64encode(image_bytes).decode("utf-8")

    @staticmethod
    def b64_to_bytes(b64_string: str) -> bytes:
        return base64.b64decode(b64_string)

    @staticmethod
    def file_to_b64(path: str | Path) -> str:
        return base64.b64encode(Path(path).read_bytes()).decode("utf-8")

    @staticmethod
    def resize(image_bytes: bytes, width: int, height: int) -> bytes:
        try:
            import cv2
            import numpy as np
            arr = np.frombuffer(image_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            resized = cv2.resize(img, (width, height))
            _, buf = cv2.imencode(".jpg", resized)
            return buf.tobytes()
        except ImportError:
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            img = img.resize((width, height))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            return buf.getvalue()

    @staticmethod
    def draw_text(image_bytes: bytes, text: str, x: int = 10, y: int = 30) -> bytes:
        try:
            import cv2
            import numpy as np
            arr = np.frombuffer(image_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
            _, buf = cv2.imencode(".jpg", img)
            return buf.tobytes()
        except ImportError:
            return image_bytes

    @staticmethod
    def detect_media_type(path: str | Path) -> str:
        ext = Path(path).suffix.lower()
        mapping = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                   ".gif": "image/gif", ".webp": "image/webp"}
        return mapping.get(ext, "image/jpeg")
