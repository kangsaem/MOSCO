"""얼굴 검출(BlazeFace full-range) + 얼굴 랜드마크(FaceMesh 468점). ONNX Runtime, CPU.

모델: Google MediaPipe (Apache 2.0). face-detection-tflite(MIT) 패키지의 tflite 를 ONNX 로 변환해 models/ 에 넣음.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import cv2
import numpy as np

from . import paths

# ---- MediaPipe FaceMesh 인덱스 (얼굴 기준 왼/오른쪽)
FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377,
             152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]
LEFT_EYE = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466]
RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
LEFT_BROW = [276, 283, 282, 295, 285, 300, 293, 334, 296, 336]
RIGHT_BROW = [46, 53, 52, 65, 55, 70, 63, 105, 66, 107]
LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
FEATURES = (LEFT_EYE, RIGHT_EYE, LEFT_BROW, RIGHT_BROW, LIPS)
CHEEKS = [50, 280, 101, 330, 151, 9, 205, 425]      # 피부색 자동 샘플 위치(볼·이마)


@dataclass
class Det:
    box: np.ndarray        # x0, y0, x1, y1 (픽셀)
    score: float
    kps: np.ndarray        # 6x2 (픽셀): 오른눈, 왼눈, 코, 입, 오른귀, 왼귀 (화면 기준 순서는 모델 정의)


def _anchors(size=192, stride=4):
    n = size // stride
    ys, xs = np.mgrid[0:n, 0:n]
    a = np.stack([(xs + 0.5) / n, (ys + 0.5) / n], -1).reshape(-1, 2)
    return a.astype(np.float32)


def _sess(name):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = 2
    so.inter_op_num_threads = 1
    so.log_severity_level = 3
    return ort.InferenceSession(str(paths.model_dir() / name), so, providers=["CPUExecutionProvider"])


def nms(dets: list[Det], thr=0.3) -> list[Det]:
    """가중 평균 NMS(MediaPipe 방식)."""
    dets = sorted(dets, key=lambda d: -d.score)
    out = []
    while dets:
        top = dets[0]
        b = np.array([d.box for d in dets])
        x0 = np.maximum(b[:, 0], top.box[0]); y0 = np.maximum(b[:, 1], top.box[1])
        x1 = np.minimum(b[:, 2], top.box[2]); y1 = np.minimum(b[:, 3], top.box[3])
        inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        iou = inter / (area + (top.box[2] - top.box[0]) * (top.box[3] - top.box[1]) - inter + 1e-9)
        # 작은 상자가 큰 상자 안에 거의 들어가면(타일 경계 조각) 같은 얼굴로 봄
        cover = inter / (area + 1e-9)
        same = (iou > thr) | (cover > 0.7)
        grp = [d for d, s in zip(dets, same) if s]
        w = np.array([d.score for d in grp])[:, None]
        box = (np.array([d.box for d in grp]) * w).sum(0) / w.sum()
        kps = (np.array([d.kps for d in grp]) * w[:, :, None]).sum(0) / w.sum()
        out.append(Det(box, top.score, kps))
        dets = [d for d, s in zip(dets, same) if not s]
    return out


class FaceDetector:
    SIZE = 192

    def __init__(self):
        self.s = _sess("face_detection_full_range.onnx")
        self.inp = self.s.get_inputs()[0].name
        self.anchors = _anchors()
        self.lock = threading.Lock()

    def _run(self, rgb: np.ndarray, min_score: float):
        """rgb: uint8 정사각 아님 가능 → 레터박스 192."""
        h, w = rgb.shape[:2]
        s = self.SIZE / max(h, w)
        nh, nw = max(1, round(h * s)), max(1, round(w * s))
        img = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
        pad = np.zeros((self.SIZE, self.SIZE, 3), np.uint8)
        py, px = (self.SIZE - nh) // 2, (self.SIZE - nw) // 2
        pad[py:py + nh, px:px + nw] = img
        x = (pad.astype(np.float32) / 127.5 - 1.0)[None]
        reg, cls = self.s.run(None, {self.inp: x})     # ORT run 은 스레드 안전
        reg = reg[0]; sc = 1 / (1 + np.exp(-np.clip(cls[0, :, 0], -80, 80)))
        idx = np.where(sc > min_score)[0]
        out = []
        for i in idx:
            r = reg[i].reshape(8, 2) / self.SIZE
            c = r[0] + self.anchors[i]; wh = r[1]
            if wh[0] <= 0 or wh[1] <= 0:
                continue
            pts = r[2:] + self.anchors[i]
            # 레터박스 제거 → 원본 픽셀
            def back(p):
                return np.stack([(p[..., 0] * self.SIZE - px) / s, (p[..., 1] * self.SIZE - py) / s], -1)
            box = np.concatenate([back(c - wh / 2), back(c + wh / 2)])
            out.append(Det(box, float(sc[i]), back(pts)))
        return out

    def detect(self, rgb: np.ndarray, min_score=0.5, tiles=False, min_size=0) -> list[Det]:
        """tiles=True: 화면 전체 + 겹치는 타일 여러 단계(작은 얼굴용).
        타일 한 변 = 화면 높이, 1/2, 1/3.4 … 최소 얼굴 크기(min_size)에 맞춰 단계 선택."""
        h, w = rgb.shape[:2]
        dets = self._run(rgb, min_score)
        if tiles:
            for side in (h, h / 2 * 1.2, h / 3.4 * 1.2):
                side = int(min(side, w, h))
                nx = max(1, int(np.ceil((w - side) / (side * 0.7))) + 1)
                ny = max(1, int(np.ceil((h - side) / (side * 0.7))) + 1)
                for y in np.linspace(0, h - side, ny).astype(int):
                    for x in np.linspace(0, w - side, nx).astype(int):
                        for d in self._run(rgb[y:y + side, x:x + side], min_score):
                            d.box = d.box + np.array([x, y, x, y], np.float32)
                            d.kps = d.kps + np.array([x, y], np.float32)
                            dets.append(d)
                if min_size and side / 12 < min_size:
                    break       # 이보다 작은 타일은 필요 없음
        dets = nms(dets)
        if min_size:
            dets = [d for d in dets if (d.box[2] - d.box[0]) >= min_size]
        return dets


class FaceMesh:
    SIZE = 192

    def __init__(self):
        self.s = _sess("face_landmark.onnx")
        self.inp = self.s.get_inputs()[0].name
        self.lock = threading.Lock()

    def run(self, rgb: np.ndarray, center, size, angle):
        """회전 정사각 ROI(center, size px, angle rad) → 468x2 픽셀 좌표, 신뢰도."""
        c, s = np.cos(angle), np.sin(angle)
        half = size / 2
        # ROI 좌표계 → 이미지: 192 입력 픽셀 u,v
        src = np.array([[-half, -half], [half, -half], [half, half]], np.float32)
        rot = np.array([[c, -s], [s, c]], np.float32)
        src = (src @ rot.T) + np.asarray(center, np.float32)
        dst = np.array([[0, 0], [self.SIZE, 0], [self.SIZE, self.SIZE]], np.float32)
        M = cv2.getAffineTransform(src, dst)
        crop = cv2.warpAffine(rgb, M, (self.SIZE, self.SIZE), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        x = (crop.astype(np.float32) / 255.0)[None]
        lm, flag = self.s.run(None, {self.inp: x})
        conf = float(1 / (1 + np.exp(-flag.flatten()[-1])))
        p = lm.reshape(-1, 3)[:, :2]
        Mi = cv2.invertAffineTransform(M)
        pts = p @ Mi[:, :2].T + Mi[:, 2]
        return pts.astype(np.float32), conf


def roi_from_det(d: Det):
    """검출 → 랜드마크 ROI (MediaPipe: 1.5배 정사각, 두 눈 기울기)."""
    b = d.box
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    size = max(b[2] - b[0], b[3] - b[1]) * 1.5
    e0, e1 = d.kps[0], d.kps[1]
    ang = np.arctan2(e1[1] - e0[1], e1[0] - e0[0])
    # kps[0]=모델의 오른눈(화면 왼쪽) → 각도 0 기준
    return (cx, cy), size, ang


def roi_from_landmarks(pts: np.ndarray):
    """이전 프레임 랜드마크 → 다음 ROI (추적)."""
    x0, y0 = pts.min(0); x1, y1 = pts.max(0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    size = max(x1 - x0, y1 - y0) * 1.5
    a, b = pts[33], pts[263]
    ang = np.arctan2(b[1] - a[1], b[0] - a[0])
    return (cx, cy), size, ang


_lock = threading.Lock()
_det = None
_mesh = None


def detector() -> FaceDetector:
    global _det
    with _lock:
        if _det is None:
            _det = FaceDetector()
        return _det


def mesh() -> FaceMesh:
    global _mesh
    with _lock:
        if _mesh is None:
            _mesh = FaceMesh()
        return _mesh
