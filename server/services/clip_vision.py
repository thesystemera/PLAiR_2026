"""CLIP (openai/clip-vit-base-patch32, local in VIDEO_CLIP_VISION_MODEL) judges video frames for the clip library.

`look` is how much a frame looks like footage a VJ would cut into a music video rather than what doesn't belong
(screen recordings, presenters, tutorials, title cards, captions, games, slides): the share of a softmax over the
two sets of descriptions that goes to the first. Measured on 80 cached clips (18 Nov): under 0.45 almost all bad,
over 0.8 almost all usable; a clip whose frames average 0.7 with none under 0.4 passed 27 of 80, one of them bad.
`relevance` is the frame's cosine to the search term. Loaded on first use, released after VISION_IDLE_S unused."""
import subprocess
import threading
import time
from typing import List, Optional, Tuple

import numpy as np
import torch
from PIL import Image

from config.settings import settings
from models_global import device

FOOTAGE = (
    "cinematic film footage", "aerial drone footage of a landscape", "a city street at night with lights",
    "nature footage of the ocean, a forest or the sky", "people dancing at a concert or a party",
    "abstract colorful visual effects", "slow motion footage", "vintage film footage",
    "a person filmed cinematically", "a music video scene",
)
NOT_FOOTAGE = (
    "a screenshot of computer software", "a person talking to the camera", "a video game screenshot",
    "a title card with large text", "a news broadcast with captions", "a presentation slide with text and a logo",
    "hands demonstrating a product in a tutorial", "a black screen", "a webcam video of a person in a room",
    "a podcast interview",
)
FRAME_SIZE = 224
FRAME_EVERY_S = 5
VISION_IDLE_S = 600


def frame_text(text: str) -> str:
    return f"a video frame of {text}"


def clip_frames(path: str, seconds: float) -> List[Image.Image]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-t", str(seconds),
         "-vf", f"fps=1/{FRAME_EVERY_S},scale={FRAME_SIZE}:{FRAME_SIZE}:force_original_aspect_ratio=increase,"
                f"crop={FRAME_SIZE}:{FRAME_SIZE}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=60).stdout
    size = FRAME_SIZE * FRAME_SIZE * 3
    return [Image.fromarray(np.frombuffer(raw[i:i + size], dtype=np.uint8).reshape(FRAME_SIZE, FRAME_SIZE, 3))
            for i in range(0, len(raw) - size + 1, size)]


class ClipVision:
    def __init__(self):
        self.lock = threading.Lock()
        self.model = None
        self.processor = None
        self.anchors = None
        self.last_used = 0.0

    def _ensure(self):
        if self.model is not None:
            return
        from transformers import CLIPModel, CLIPProcessor
        path = str(settings.VIDEO_CLIP_VISION_MODEL)
        self.model = CLIPModel.from_pretrained(path).to(device).eval()
        self.processor = CLIPProcessor.from_pretrained(path, use_fast=True)
        self.anchors = self._texts([frame_text(text) for text in FOOTAGE + NOT_FOOTAGE])

    def _texts(self, texts: List[str]) -> torch.Tensor:
        inputs = self.processor(text=texts, return_tensors="pt", padding=True, truncation=True).to(device)
        return torch.nn.functional.normalize(self.model.get_text_features(**inputs), dim=-1)

    def judge(self, images: List[Image.Image], term: Optional[str] = None) -> Tuple[np.ndarray, np.ndarray]:
        if not images:
            return np.zeros(0), np.zeros(0)
        with self.lock, torch.no_grad():
            self._ensure()
            self.last_used = time.time()
            pixels = self.processor(images=images, return_tensors="pt").to(device)
            frames = torch.nn.functional.normalize(self.model.get_image_features(**pixels), dim=-1)
            look = (100 * frames @ self.anchors.T).softmax(-1)[:, :len(FOOTAGE)].sum(1)
            relevance = (frames @ self._texts([frame_text(term)]).T).squeeze(-1) if term else torch.zeros_like(look)
            return look.float().cpu().numpy(), relevance.float().cpu().numpy()

    def release_if_idle(self):
        with self.lock:
            if self.model is None or time.time() - self.last_used < VISION_IDLE_S:
                return
            self.model = self.processor = self.anchors = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()


vision = ClipVision()
