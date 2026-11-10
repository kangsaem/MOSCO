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


USE_GPU = False          # 설정에서 켜면 GPU(DirectML→CUDA) 검출. 꺼지면 CPU


def available_providers():
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception:  # noqa: BLE001
        return ["CPUExecutionProvider"]


def gpu_name():
    """쓸 수 있는 GPU 가속기 이름(없으면 None). 설정 화면 표시용."""
    av = available_providers()
    for p, name in (("DmlExecutionProvider", "GPU (DirectML)"), ("CUDAExecutionProvider", "GPU (CUDA)")):
        if p in av:
            return name
    return None


def _providers():
    if USE_GPU:
        av = available_providers()
        for p in ("DmlExecutionProvider", "CUDAExecutionProvider"):
            if p in av:
                return [p, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def set_gpu(on: bool):
    """GPU 검출 켜고 끄기. 이미 만든 세션을 버려 다음 사용 때 새 장치로 다시 만든다."""
    global USE_GPU, _det, _mesh, _small, _person
    on = bool(on) and gpu_name() is not None
    if on == USE_GPU:
        return
    USE_GPU = on
    with _lock:
        _det = _mesh = _small = _person = None


def active_device():
    """지금 검출이 실제로 쓰는 장치 이름(로그·표시용)."""
    if USE_GPU:
        provs = _providers()
        if provs and provs[0] != "CPUExecutionProvider":
            return {"DmlExecutionProvider": "GPU(DirectML)", "CUDAExecutionProvider": "GPU(CUDA)"}.get(provs[0], "GPU")
    return "CPU"


def _sess(name):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    provs = _providers()
    if provs[0] == "CPUExecutionProvider":
        so.intra_op_num_threads = 2
        so.inter_op_num_threads = 1
    else:
        # DirectML 권장: 메모리 패턴·병렬 실행 끄기(동적 입력에서 안정)
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(paths.model_dir() / name), so, providers=provs)


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

    def detect(self, rgb: np.ndarray, min_score=0.5, tiles=False, min_size=0, tile_top=1.0) -> list[Det]:
        """tiles=True: 화면 전체 + 겹치는 타일 여러 단계(작은 얼굴용).
        타일 한 변 = 화면 높이, 1/2, 1/3.4 … 최소 얼굴 크기(min_size)에 맞춰 단계 선택.
        tile_top<1.0: 타일(작은 얼굴 검색)을 화면 상단 그 비율까지만 — 하단 제외로 속도↑.
        전체 화면 1회 통과는 그대로라 하단의 '큰' 얼굴은 계속 잡힘."""
        h, w = rgb.shape[:2]
        dets = self._run(rgb, min_score)             # 전체 화면 1회(하단 포함, 큰 얼굴 보존)
        if tiles:
            hlim = int(round(h * max(0.1, min(1.0, tile_top))))   # 타일은 상단 hlim 까지만
            for side in (h, h / 2 * 1.2, h / 3.4 * 1.2):
                side = int(min(side, w, h))
                yend = max(0, hlim - side)
                nx = max(1, int(np.ceil((w - side) / (side * 0.7))) + 1)
                ny = max(1, int(np.ceil(yend / (side * 0.7))) + 1)
                for y in np.linspace(0, yend, ny).astype(int):
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


# ------------------------------------------------------------ 작은 얼굴: MTCNN (WIDER FACE 학습)
def _nms_xyxy(b, s, thr, mode="union"):
    """b: (N,4) s: (N,) → 남길 인덱스."""
    if len(b) == 0:
        return np.zeros(0, int)
    x0, y0, x1, y1 = b.T
    area = (x1 - x0 + 1) * (y1 - y0 + 1)
    order = s.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        xx0 = np.maximum(x0[i], x0[order[1:]]); yy0 = np.maximum(y0[i], y0[order[1:]])
        xx1 = np.minimum(x1[i], x1[order[1:]]); yy1 = np.minimum(y1[i], y1[order[1:]])
        inter = np.clip(xx1 - xx0 + 1, 0, None) * np.clip(yy1 - yy0 + 1, 0, None)
        ov = inter / (np.minimum(area[i], area[order[1:]]) if mode == "min" else (area[i] + area[order[1:]] - inter))
        order = order[1:][ov <= thr]
    return np.array(keep, int)


def _square(b):
    w, h = b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]
    m = np.maximum(w, h)
    b = b.copy()
    b[:, 0] += w / 2 - m / 2
    b[:, 1] += h / 2 - m / 2
    b[:, 2] = b[:, 0] + m
    b[:, 3] = b[:, 1] + m
    return b


def _crops(img, boxes, size):
    """img: 정규화 float (H,W,3). boxes 영역을 size×size 로(밖은 0)."""
    out = np.zeros((len(boxes), size, size, 3), np.float32)
    H, W = img.shape[:2]
    for k, (x0, y0, x1, y1) in enumerate(boxes):
        w, h = x1 - x0, y1 - y0
        if w < 2 or h < 2:
            continue
        M = np.array([[size / w, 0, -x0 * size / w], [0, size / h, -y0 * size / h]], np.float32)
        out[k] = cv2.warpAffine(img, M, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    return out


class SmallFaceDetector:
    """MTCNN P/R/O-net. 작은 얼굴(min~max px) 전용 피라미드."""

    def __init__(self):
        self.p = _sess("mtcnn_pnet.onnx")
        self.r = _sess("mtcnn_rnet.onnx")
        self.o = _sess("mtcnn_onet.onnx")

    def detect(self, rgb, min_face=10, max_face=64, th=(0.6, 0.7, 0.7)):
        H, W = rgb.shape[:2]
        img = (rgb.astype(np.float32) - 127.5) / 128.0
        scales = []
        s = 12.0 / min_face
        while 12.0 / s <= max_face * 1.1:
            scales.append(s)
            s *= 0.709
        cand = []
        for s in scales:
            hs, ws = int(np.ceil(H * s)), int(np.ceil(W * s))
            im = cv2.resize(img, (ws, hs), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            reg, cls = self.p.run(None, {"input": im[None]})
            prob = cls[0, :, :, 1]
            ys, xs = np.where(prob > th[0])
            if len(ys) == 0:
                continue
            r = reg[0, ys, xs]
            x0 = xs * 2.0; y0 = ys * 2.0; x1 = x0 + 12; y1 = y0 + 12
            b = np.stack([x0 + r[:, 0] * 12, y0 + r[:, 1] * 12, x1 + r[:, 2] * 12, y1 + r[:, 3] * 12], 1) / s
            sc = prob[ys, xs]
            k = _nms_xyxy(b, sc, 0.5)
            cand.append(np.c_[b[k], sc[k]])
        if not cand:
            return []
        c = np.concatenate(cand)
        c = c[_nms_xyxy(c[:, :4], c[:, 4], 0.7)]
        b = _square(c[:, :4])
        # R-net
        reg, cls = self.r.run(None, {"input": _crops(img, b, 24)})
        keep = cls[:, 1] > th[1]
        b, reg, sc = b[keep], reg[keep], cls[keep, 1]
        if len(b) == 0:
            return []
        wh = np.c_[b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]]
        b = b + reg * np.c_[wh, wh]
        k = _nms_xyxy(b, sc, 0.7)
        b = _square(b[k])
        # O-net
        reg, _lm, cls = self.o.run(None, {"input": _crops(img, b, 48)})
        keep = cls[:, 1] > th[2]
        b, reg, sc = b[keep], reg[keep], cls[keep, 1]
        if len(b) == 0:
            return []
        wh = np.c_[b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]]
        b = b + reg * np.c_[wh, wh]
        k = _nms_xyxy(b, sc, 0.7, "min")
        return [(*b[i], float(sc[i])) for i in k]


# ------------------------------------------------------------ 사람(몸): NanoDet-m (COCO) → 머리 위치 추정
class PersonDetector:
    SIZE = 416

    def __init__(self):
        self.s = _sess("nanodet_m.onnx")
        self.inp = self.s.get_inputs()[0].name
        names = [o.name for o in self.s.get_outputs()]
        shapes = {o.name: o.shape for o in self.s.get_outputs()}
        self.levels = []
        for st in (8, 16, 32):
            n = (self.SIZE // st) ** 2
            cls = next(k for k in names if shapes[k][1] == n and shapes[k][2] == 80)
            reg = next(k for k in names if shapes[k][1] == n and shapes[k][2] == 44)
            self.levels.append((st, cls, reg))
        self.mean = np.array([103.53, 116.28, 123.675], np.float32)
        self.std = np.array([57.375, 57.12, 58.395], np.float32)

    def _run(self, rgb, thr):
        h, w = rgb.shape[:2]
        x = cv2.resize(rgb[..., ::-1], (self.SIZE, self.SIZE)).astype(np.float32)
        x = ((x - self.mean) / self.std).transpose(2, 0, 1)[None]
        out = dict(zip([o.name for o in self.s.get_outputs()], self.s.run(None, {self.inp: x})))
        boxes = []
        for st, cn, rn in self.levels:
            c = out[cn][0][:, 0]
            idx = np.where(c > thr)[0]
            if not len(idx):
                continue
            r = out[rn][0][idx].reshape(-1, 4, 11)
            e = np.exp(r - r.max(-1, keepdims=True))
            e /= e.sum(-1, keepdims=True)
            d = (e * np.arange(11)).sum(-1) * st
            n = self.SIZE // st
            ys, xs = np.divmod(idx, n)
            cx, cy = xs * st, ys * st
            b = np.stack([cx - d[:, 0], cy - d[:, 1], cx + d[:, 2], cy + d[:, 3]], 1)
            b *= [w / self.SIZE, h / self.SIZE, w / self.SIZE, h / self.SIZE]
            boxes.append(np.c_[b, c[idx]])
        return np.concatenate(boxes) if boxes else np.zeros((0, 5), np.float32)

    def detect(self, rgb, thr=0.4, tiles=True):
        H, W = rgb.shape[:2]
        allb = [self._run(rgb, thr)]
        if tiles:      # 2×2 겹침 타일(멀리 있는 사람)
            th, tw = int(H * 0.6), int(W * 0.6)
            for y in (0, H - th):
                for x in (0, W - tw):
                    b = self._run(rgb[y:y + th, x:x + tw], thr)
                    if y > 0 and len(b):   # 타일 위 경계에서 잘린 사람(다리만 등) → 머리 추정 틀림, 버림
                        b = b[b[:, 1] > 4]
                    b[:, :4] += [x, y, x, y]
                    allb.append(b)
        b = np.concatenate(allb)
        if not len(b):
            return []
        k = _nms_xyxy(b[:, :4], b[:, 4], 0.55)
        b = b[k]
        # 같은 사람의 부분 상자(상반신 등): 큰 상자 안에 거의 들어가면 버림
        k = _nms_xyxy(b[:, :4], b[:, 4] + (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]) * 1e-7, 0.8, "min")
        return [tuple(float(v) for v in b[i]) for i in k]


def head_from_person(x0, y0, x1, y1, cut_bottom=False):
    """사람 상자 → 머리 상자(얼굴 상자 기준 좌표계, face_to_head 와 맞춤). 서 있는 사람 가정.
    화면 아래에서 잘린 사람(상반신)은 키를 모르므로 어깨 폭으로만 추정."""
    bw, bh = x1 - x0, y1 - y0
    hw = bw * 0.48 if cut_bottom else min(bw * 0.48, bh * 0.22)     # 불확실 → 넉넉하게
    cx = (x0 + x1) / 2
    cy = y0 + hw * 0.62
    fw = hw / 1.1               # 얼굴 상자 크기로 환산(여유 % 가 같은 비율로 적용되게, 조금 크게)
    return cx, cy + fw * 0.15, fw, fw


_small = None
_person = None


def small_detector() -> SmallFaceDetector:
    global _small
    with _lock:
        if _small is None:
            _small = SmallFaceDetector()
        return _small


def person_detector() -> PersonDetector:
    global _person
    with _lock:
        if _person is None:
            _person = PersonDetector()
        return _person
