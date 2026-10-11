"""Person/animal detection with a YOLO11 model running on ncnn (Vulkan on the GPU).

Plain, -seg (outlines) and -pose (people only, with 17 body keypoints)
models are supported.
"""

from dataclasses import dataclass
from pathlib import Path

import ncnn
import numpy as np

# COCO class ids that depict living beings. Teddy bear (77) is left out by
# default: whether toys/figurines count is a matter of scholarly difference,
# and it belongs in the user-facing settings rather than here.
LIVING_CLASSES = {
    0: "person", 14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep",
    19: "cow", 20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe",
}

PAD_VALUE = 114.0
NUM_CLASSES = 80  # COCO


@dataclass
class Detection:
    label: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float
    # Outline, for segmentation models: a boolean grid covering mask_box
    # (x0, y0, x1, y1, in the same coordinates as the box).
    mask: np.ndarray | None = None
    mask_box: tuple | None = None
    # Pose models: (17, 3) array of COCO keypoints (x, y, confidence).
    kpts: np.ndarray | None = None
    # Blur an oval filling the box rather than an outline (face mode).
    ellipse: bool = False

    def moved(self, dx, dy):
        mask_box = None
        if self.mask_box:
            mx0, my0, mx1, my1 = self.mask_box
            mask_box = (mx0 + dx, my0 + dy, mx1 + dx, my1 + dy)
        kpts = None
        if self.kpts is not None:
            kpts = self.kpts.copy()
            kpts[:, :2] += (dx, dy)
        return Detection(self.label, self.score, self.x0 + dx, self.y0 + dy,
                         self.x1 + dx, self.y1 + dy, self.mask, mask_box, kpts, self.ellipse)

    def box(self):
        return (self.x0, self.y0, self.x1, self.y1)


class Detector:
    def __init__(self, model_dir, conf=0.25, iou=0.45, use_gpu=True, classes=LIVING_CLASSES):
        model_dir = Path(model_dir)
        # Ultralytics' ncnn export bakes the anchor grid in at a fixed square
        # size, so the input must be exactly imgsz x imgsz. A different size
        # needs a re-export (see tools/export_models.sh).
        meta = (model_dir / "metadata.yaml").read_text()
        self.imgsz = read_imgsz(model_dir / "metadata.yaml")
        # Segmentation models also output each figure's outline; pose models
        # find only people, with body keypoints.
        self.segment = "task: segment" in meta
        self.pose = "task: pose" in meta
        if self.pose:
            classes = {0: "person"}
        self.num_classes = 1 if self.pose else NUM_CLASSES
        self.conf = conf
        self.iou = iou
        self.classes = classes
        self.class_ids = np.array(sorted(classes))

        self.net = ncnn.Net()
        self.net.opt.use_vulkan_compute = use_gpu and ncnn.get_gpu_count() > 0
        self.net.opt.use_fp16_storage = True
        self.net.opt.use_fp16_arithmetic = True
        # The CPU only does pre/post-processing; a small pool is as fast and
        # far cheaper. Must be set before loading, or gemm keeps the default.
        self.net.opt.num_threads = 2
        self.net.load_param(str(model_dir / "model.ncnn.param"))
        self.net.load_model(str(model_dir / "model.ncnn.bin"))
        self.on_gpu = self.net.opt.use_vulkan_compute

    def preprocess(self, pixels, pixel_type, w, h, stride):
        """Letterbox: scale longest side to imgsz, pad the rest to a square."""
        scale = self.imgsz / max(w, h)
        tw, th = round(w * scale), round(h * scale)
        mat = ncnn.Mat.from_pixels_resize(pixels, pixel_type, w, h, stride, tw, th)
        pad_w, pad_h = self.imgsz - tw, self.imgsz - th
        left, top = pad_w // 2, pad_h // 2
        mat = ncnn.copy_make_border(
            mat, top, pad_h - top, left, pad_w - left, ncnn.BorderType.BORDER_CONSTANT, PAD_VALUE
        )
        mat.substract_mean_normalize([], [1 / 255.0] * 3)
        return mat, scale, left, top

    def detect(self, pixels, pixel_type, w, h, stride):
        """Detections in the w x h image at `pixels`, in its own coordinates."""
        mat, scale, left, top = self.preprocess(pixels, pixel_type, w, h, stride)
        return self.postprocess(self.infer(mat), scale, left, top, w, h)

    def infer(self, mat):
        ex = self.net.create_extractor()
        ex.input("in0", mat)
        _, out = ex.extract("out0")  # (4 + classes [+ 32 mask coefficients], anchors)
        if not self.segment:
            return np.array(out), None

        def protos():
            # (32, imgsz/4, imgsz/4) mask prototypes. ncnn only computes an
            # output when it's extracted, so frames with nobody in them never
            # pay for this (or for copying it off the GPU).
            _, p = ex.extract("out1")
            return np.array(p)
        return np.array(out), protos

    def postprocess(self, outputs, scale, left, top, w, h):
        out, protos = outputs
        boxes = out[:4].T
        scores_all = out[4:4 + self.num_classes].T
        scores_living = scores_all[:, self.class_ids]
        best = scores_living.argmax(1)
        scores = scores_living[np.arange(len(best)), best]
        keep = scores >= self.conf
        boxes, scores, best = boxes[keep], scores[keep], best[keep]
        if not len(scores):
            return []
        coeffs = None
        if protos is not None:
            coeffs, protos = out[4 + NUM_CLASSES:].T[keep], protos()
        kpts = None
        if self.pose:
            kpts = out[4 + self.num_classes:].T[keep].reshape(-1, 17, 3).copy()
            kpts[..., 0] = (kpts[..., 0] - left) / scale
            kpts[..., 1] = (kpts[..., 1] - top) / scale

        cx, cy, bw, bh = boxes.T
        model_xyxy = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1)
        xyxy = model_xyxy.copy()
        xyxy[:, [0, 2]] = ((xyxy[:, [0, 2]] - left) / scale).clip(0, w)
        xyxy[:, [1, 3]] = ((xyxy[:, [1, 3]] - top) / scale).clip(0, h)

        # Class-agnostic NMS: everything detected gets blurred the same way,
        # so overlapping person/animal boxes don't need to be kept apart.
        dets = []
        for i in nms(xyxy, scores, self.iou):
            det = Detection(self.classes[int(self.class_ids[best[i]])], float(scores[i]), *map(float, xyxy[i]))
            if kpts is not None:
                det.kpts = kpts[i]
            if coeffs is not None:
                det.mask, mbox = self._mask(protos, coeffs[i], model_xyxy[i])
                det.mask_box = ((mbox[0] - left) / scale, (mbox[1] - top) / scale,
                                (mbox[2] - left) / scale, (mbox[3] - top) / scale)
            dets.append(det)
        return dets

    def _mask(self, protos, coeff, box):
        """The figure's outline inside its box, from the mask prototypes.
        Returns (bool grid, its extent in model input coordinates)."""
        cells = protos.shape[1]
        cell = self.imgsz / cells
        mx0, my0 = (max(0, int(v // cell)) for v in box[:2])
        mx1, my1 = (min(cells, int(-(-v // cell))) for v in box[2:])
        mx1, my1 = max(mx1, mx0 + 1), max(my1, my0 + 1)
        logits = np.tensordot(coeff, protos[:, my0:my1, mx0:mx1], axes=1)
        return logits > 0, (mx0 * cell, my0 * cell, mx1 * cell, my1 * cell)  # sigmoid > 0.5


def read_imgsz(metadata_path):
    lines = metadata_path.read_text().splitlines()
    i = lines.index("imgsz:")
    return int(lines[i + 1].strip("- "))


def nms(xyxy, scores, iou_thresh):
    order = scores.argsort()[::-1]
    areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
    keep = []
    while len(order):
        i = order[0]
        keep.append(i)
        rest = order[1:]
        ix0 = np.maximum(xyxy[i, 0], xyxy[rest, 0])
        iy0 = np.maximum(xyxy[i, 1], xyxy[rest, 1])
        ix1 = np.minimum(xyxy[i, 2], xyxy[rest, 2])
        iy1 = np.minimum(xyxy[i, 3], xyxy[rest, 3])
        inter = (ix1 - ix0).clip(0) * (iy1 - iy0).clip(0)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou < iou_thresh]
    return keep
