"""Face relight for Step 4, run on the Colab T4 (never imported locally: it needs CUDA, torch, mediapipe).

The look (approved 2026-11-15): the face is cleaner and brighter (skin-only retouch that keeps pores
and natural shine, gentle key light, sharper eyes), and the room is dimmed and desaturated through a
wide, feathered light-falloff map built from an AI matte. Nothing changes sharply at the outline, so
matte errors stay invisible; there is no background blur and no colour cast.

    python3 relight.py --models DIR --decode '<json argv>' --size 1920x1080 --fps 30 --frames N -- <encode argv>

The decode argv (ffmpeg, NVDEC) writes the cut timeline as raw NV12 to stdout. The encode argv
(ffmpeg, NVENC) reads raw NV12 on stdin and inherits this process's stdout and stderr, so the local
side still sees its -progress stream and its blackdetect lines. This script logs to stderr only.

Throughput on a free T4: ~80 fps at 1080p (2.7x realtime). How: NV12 in and out of pinned memory
(no pixel work on the 2 vCPUs), the whole per-frame GPU pipeline replayed as CUDA graphs,
torch.compile-fused per-pixel math, RVM MobileNetV3 fp16 on a 960 px copy every 2nd frame, MediaPipe
(skin mask, face landmarks) on a worker thread every 3rd frame, eased in on the GPU.
"""
import argparse
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import cv2
import mediapipe as mp
import numpy as np
import torch
import torch.nn.functional as F
from mediapipe.tasks.python import BaseOptions, vision

ap = argparse.ArgumentParser()
ap.add_argument("--models", required=True)
ap.add_argument("--decode", required=True, help="JSON argv of the decoder (raw NV12 on stdout)")
ap.add_argument("--size", required=True, help="WxH of the decoded frames")
ap.add_argument("--fps", type=float, default=30)
ap.add_argument("--frames", type=int, default=0, help="expected frame count (progress log only)")
ap.add_argument("--every", type=int, default=3)          # MediaPipe cadence (frames)
ap.add_argument("--matte-every", type=int, default=2)    # RVM cadence (frames); the falloff hides the reuse
ap.add_argument("--matte-w", type=int, default=960)      # RVM input width
ap.add_argument("--dsr", type=float, default=0.5)        # RVM downsample ratio of that input
ap.add_argument("--no-graph", action="store_true")
ap.add_argument("--no-compile", action="store_true")
# the look
ap.add_argument("--bg", type=float, default=0.3)         # room exposure (linear gain)
ap.add_argument("--bg-desat", type=float, default=0.45)
ap.add_argument("--spread", type=float, default=22)      # half-res px of full light past the outline
ap.add_argument("--falloff", type=float, default=28)     # half-res px falloff softness
ap.add_argument("--key", type=float, default=0.22)
ap.add_argument("--smooth", type=float, default=0.55)
ap.add_argument("--exposure", type=float, default=1.06)
ap.add_argument("encode", nargs=argparse.REMAINDER)
a = ap.parse_args()
ENCODE = a.encode[1:] if a.encode[:1] == ["--"] else a.encode
if not ENCODE:
    ap.error("missing the encode argv after --")

children: list[subprocess.Popen] = []


def log(msg: str) -> None:
    print(f"[relight] {msg}", file=sys.stderr, flush=True)


def die(code: int = 1) -> None:
    """Exit now, killing the decoder and encoder so a half-written output is never left running."""
    for p in children:
        if p.poll() is None:
            p.kill()
    os._exit(code)


def _excepthook(t, v, tb):
    traceback.print_exception(t, v, tb, file=sys.stderr)
    die(1)


sys.excepthook = _excepthook
threading.excepthook = lambda args: _excepthook(args.exc_type, args.exc_value, args.exc_traceback)

dev = "cuda"
HALF = torch.float16
torch.backends.cudnn.benchmark = True  # every shape is fixed

W, H = map(int, a.size.lower().split("x"))
FR = W * H * 3 // 2
QW, QH = W // 4, H // 4
SW, SH = 640, 360            # MediaPipe input size
SX = W / SW

# ---------------------------------------------------------------- models
rvm = torch.hub.load("PeterL1n/RobustVideoMatting", "mobilenetv3", trust_repo=True, verbose=False).eval().to(dev).half()
# RVM's backbone builds its normalisation mean/std on the CPU every call, which CUDA graph capture
# forbids: swap in GPU constants.
_MEAN = torch.tensor([0.485, 0.456, 0.406], device=dev, dtype=HALF).view(1, 3, 1, 1)
_STD = torch.tensor([0.229, 0.224, 0.225], device=dev, dtype=HALF).view(1, 3, 1, 1)
sys.modules[type(rvm.backbone).__module__].normalize = lambda x, mean, std: (x - _MEAN.to(x.dtype)) / _STD.to(x.dtype)
seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
    base_options=BaseOptions(model_asset_path=f"{a.models}/selfie_multiclass_256x256.tflite"),
    running_mode=vision.RunningMode.VIDEO, output_confidence_masks=True, output_category_mask=False))
facelm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=f"{a.models}/face_landmarker.task"),
    running_mode=vision.RunningMode.VIDEO, num_faces=1))


# ---------------------------------------------------------------- image helpers
def rs(x, w, h):
    return F.interpolate(x, size=(h, w), mode="bilinear", align_corners=False)


def down(x, w, h):
    kx, ky = x.shape[-1] // w, x.shape[-2] // h
    if kx == ky and x.shape[-1] == w * kx and x.shape[-2] == h * ky:
        return F.avg_pool2d(x, kx)       # exact for integer factors, far faster than adaptive pooling
    return F.interpolate(x, size=(h, w), mode="area")


def gauss_sep(x, s):
    """Separable gaussian as shifted-slice sums: fuses into single kernels under torch.compile."""
    r = math.ceil(3 * s)
    k = [math.exp(-i * i / (2 * s * s)) for i in range(-r, r + 1)]
    k = [v / sum(k) for v in k]
    hw, hh = x.shape[-1], x.shape[-2]
    xp = F.pad(x, (r, r, 0, 0), mode="replicate")
    x = sum(k[i] * xp[..., i:i + hw] for i in range(2 * r + 1))
    xp = F.pad(x, (0, 0, r, r), mode="replicate")
    return sum(k[i] * xp[..., i:i + hh, :] for i in range(2 * r + 1))


def box_sep(x, r):
    hw, hh = x.shape[-1], x.shape[-2]
    xp = F.pad(x, (r, r, 0, 0), mode="replicate")
    x = sum(xp[..., i:i + hw] for i in range(2 * r + 1)) / (2 * r + 1)
    xp = F.pad(x, (0, 0, r, r), mode="replicate")
    return sum(xp[..., i:i + hh, :] for i in range(2 * r + 1)) / (2 * r + 1)


def guided_sep(I, r, eps):
    m = box_sep(I, r)
    v = box_sep(I * I, r) - m * m
    A = v / (v + eps)
    return box_sep(A, r) * I + box_sep(m - A * m, r)


LUMW = torch.tensor([0.2126, 0.7152, 0.0722], device=dev).view(1, 3, 1, 1)


def lum(x):
    return (x * LUMW.to(x.dtype)).sum(1, keepdim=True)


def nv12_to_rgb(buf):
    """BT.709 limited-range NV12 -> RGB in [0, 1]."""
    y = buf[:W * H].view(1, 1, H, W).to(HALF)
    uv = buf[W * H:].view(H // 2, W // 2, 2).permute(2, 0, 1)[None].to(HALF)
    uv = F.interpolate(uv, size=(H, W), mode="bilinear", align_corners=False)
    Y = (y - 16) / 219
    cb = (uv[:, 0:1] - 128) / 224
    cr = (uv[:, 1:2] - 128) / 224
    return torch.cat([Y + 1.5748 * cr, Y - 0.1873 * cb - 0.4681 * cr, Y + 1.8556 * cb], 1).clamp(0, 1)


def rgb_to_nv12(rgb):
    """RGB in [0, 1] -> BT.709 limited-range NV12."""
    rgb = rgb.float()
    Y = lum(rgb)
    cb = (rgb[:, 2:3] - Y) / 1.8556
    cr = (rgb[:, 0:1] - Y) / 1.5748
    c = F.avg_pool2d(torch.cat([cb, cr], 1), 2)
    y8 = (16 + 219 * Y).round().clamp(0, 255).to(torch.uint8).reshape(-1)
    uv8 = (128 + 224 * c).round().clamp(0, 255).to(torch.uint8)[0].permute(1, 2, 0).reshape(-1)
    return torch.cat([y8, uv8])


# ---------------------------------------------------------------- the look
def grade1(f, gain, Pd, bg, desat):
    """Linear light: key-light gain, highlight roll-off on the subject, room dimmed + desaturated by 1 - Pd."""
    lin = f.float().clamp(0, 1) ** 2.2 * gain
    ls = lum(lin)
    roll = torch.where(ls > 0.45, (0.45 + (ls - 0.45) * 0.5) / ls.clamp(min=1e-4), torch.ones_like(ls))
    lin = lin * (1 + (roll - 1) * Pd)
    lin = lin + (lum(lin) - lin) * desat * (1 - Pd)
    return lin * (bg + (1 - bg) * Pd)


SH_T = torch.tensor([-0.015, 0.0, 0.015], device=dev).view(1, 3, 1, 1)
HI_T = torch.tensor([0.012, 0.004, -0.01], device=dev).view(1, 3, 1, 1)
BL_T = torch.tensor([1.0, 0.9, 0.8], device=dev).view(1, 3, 1, 1)


def grade2(out, bloom, S, grain, exposure):
    """Bloom, filmic (ACES fit) tone map, subtle split tone with skin protected, vibrance, grain."""
    out = out + bloom * BL_T * 0.15
    x = out * exposure
    out = ((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14)).clamp(0, 1) ** (1 / 2.2)
    L = out.mean(1, keepdim=True)
    out = out + (1 - L) ** 2 * (1 - 0.6 * S) * SH_T + L ** 2 * HI_T
    L = out.mean(1, keepdim=True)
    sat = (out - L).abs().amax(1, keepdim=True)
    out = L + (out - L) * (1.08 - 0.25 * sat)
    return (out + grain * 0.01).clamp(0, 1)


def retouch(f0, S_c, P_q, eye_q, smooth):
    """Skin retouch (mid frequencies out, pores and highlights kept) + gentle sharpening + eyes."""
    g = gauss_sep(f0, 1.3)
    base = rs(guided_sep(F.avg_pool2d(f0, 2).float(), 4, 0.005), W, H)
    f0 = f0.float()
    S = rs(S_c, W, H)
    P = rs(P_q, W, H)
    eye = rs(eye_q, W, H)
    hp = f0 - g.float()
    L = lum(f0)
    ret = base + 0.95 * hp - S * (L - 0.5).clamp(min=0) * 0.75
    hl = (1 - (L - 0.55) / 0.25).clamp(0, 1)
    f = f0 + (ret - f0) * S * smooth * hl
    return (f + hp * (0.18 * P + 0.3 * eye)).to(HALF)


def grade1_q(f, gain_q, Pd_q, bg, desat):
    return grade1(f, rs(gain_q, W, H), rs(Pd_q, W, H), bg, desat).to(HALF)


def grade2_q(lin, S_c, grain_h, exposure):
    q = F.avg_pool2d(lin.float(), 8)                    # eighth-res highlights for bloom
    bloom = gauss_sep((q - 0.7).clamp(min=0), 7)
    return grade2(lin.float(), rs(bloom, W, H), rs(S_c, W, H), rs(grain_h.float(), W, H), exposure)


def falloff_map(P_q, kk, sigma):
    Pe = F.avg_pool2d(P_q, 2)                           # eighth res: the falloff is very soft anyway
    return gauss_sep(F.max_pool2d(Pe, kk, 1, kk // 2), sigma).clamp(0, 1)


if not a.no_compile:
    _c = dict(dynamic=False, fullgraph=True)
    retouch = torch.compile(retouch, **_c)
    grade1_q = torch.compile(grade1_q, **_c)
    grade2_q = torch.compile(grade2_q, **_c)
    falloff_map = torch.compile(falloff_map, **_c)
    rgb_to_nv12 = torch.compile(rgb_to_nv12, **_c)
    nv12_to_rgb = torch.compile(nv12_to_rgb, **_c)

yq, xq = torch.meshgrid(torch.arange(QH, device=dev, dtype=torch.float32) * 4 + 2,
                        torch.arange(QW, device=dev, dtype=torch.float32) * 4 + 2, indexing="ij")
gen = torch.Generator(device=dev).manual_seed(7)

# ---------------------------------------------------------------- MediaPipe worker
wstate = {"face": None}


def analyze(small, pha_q, ts):
    """Skin mask, face box + irises, hands entering from the frame edges, and the poster guard."""
    img = mp.Image(image_format=mp.ImageFormat.SRGB, data=small)
    cm = [m.numpy_view().astype(np.float32).squeeze() for m in seg.segment_for_video(img, ts).confidence_masks]
    S = cm[2] + cm[3]
    person = 1 - cm[0]
    fr = facelm.detect_for_video(img, ts)
    if fr.face_landmarks:
        lm = np.array([(p.x * W, p.y * H) for p in fr.face_landmarks[0]], np.float32)
        x0, y0 = lm.min(0)
        x1, y1 = lm.max(0)
        wstate["face"] = np.array([(x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, *lm[468], *lm[473]], np.float32)
    fc = wstate["face"]
    # hands: body-skin blobs entering from the frame edges, away from the face
    body = (cm[2] > 0.5).astype(np.uint8)
    if fc is not None:
        cx, cy, fw = fc[:3] / SX
        cv2.rectangle(body, (int(cx - fw), 0), (int(cx + fw), int(cy + fw * 1.2)), 0, -1)
    _, lab = cv2.connectedComponents(body)
    ids = set(np.unique(np.concatenate([lab[:, :3].ravel(), lab[:, -3:].ravel(), lab[-3:, :].ravel()]))) - {0}
    hm = np.zeros((SH, SW), np.float32)
    for i in ids:
        hm = np.maximum(hm, (lab == i).astype(np.float32))
    hm = cv2.GaussianBlur(cv2.dilate(hm, np.ones((7, 7), np.uint8)), (0, 0), 2) * np.clip(person * 1.3, 0, 1)
    # poster guard: keep only matte regions connected to the face or to the bottom edge (the body)
    q = (pha_q > 0.2).astype(np.uint8)
    _, lab = cv2.connectedComponents(q, connectivity=8)
    keep = set(np.unique(lab[-2:, :]).tolist())
    if fc is not None:
        cx, cy, fw = fc[:3] / 4
        keep |= set(np.unique(lab[int(max(0, cy - fw * .4)):int(min(QH, cy + fw * .4)),
                                  int(max(0, cx - fw * .3)):int(min(QW, cx + fw * .3))]).tolist())
    keep.discard(0)
    km = np.isin(lab, list(keep)).astype(np.float32)
    km = cv2.GaussianBlur(cv2.dilate(km, np.ones((15, 15), np.uint8)), (0, 0), 3)
    return S, hm, km, fc


# ---------------------------------------------------------------- IO threads (zero-copy pinned ring)
NSLOT = 4
pin_in = [torch.empty(FR, dtype=torch.uint8).pin_memory() for _ in range(NSLOT)]
pin_out = [torch.empty(FR, dtype=torch.uint8).pin_memory() for _ in range(NSLOT)]
pin_small = [torch.empty(SH, SW, 3, dtype=torch.uint8).pin_memory() for _ in range(NSLOT)]
pin_pha = [torch.empty(QH, QW).pin_memory() for _ in range(NSLOT)]
done_ev = [torch.cuda.Event() for _ in range(NSLOT)]
free_in: queue.Queue = queue.Queue()
free_out: queue.Queue = queue.Queue()
for k in range(NSLOT):
    free_in.put(k)
    free_out.put(k)
inq: queue.Queue = queue.Queue()
outq: queue.Queue = queue.Queue()

dec = subprocess.Popen(json.loads(a.decode), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, bufsize=0)
children.append(dec)
enc = subprocess.Popen(ENCODE, stdin=subprocess.PIPE, bufsize=0)   # stdout/stderr inherited
children.append(enc)


def reader():
    while True:
        k = free_in.get()
        mv = memoryview(pin_in[k].numpy())
        got = 0
        while got < FR:
            r = dec.stdout.readinto(mv[got:])
            if not r:
                break
            got += r
        if got < FR:
            break
        inq.put(k)
    inq.put(None)


def writer():
    while True:
        k = outq.get()
        if k is None:
            break
        enc.stdin.write(memoryview(pin_out[k].numpy()))
        free_out.put(k)
    enc.stdin.close()


threading.Thread(target=reader, daemon=True).start()
wt = threading.Thread(target=writer, daemon=True)
wt.start()
ex = ThreadPoolExecutor(1)

# ---------------------------------------------------------------- static state (CUDA graphs)
MW, MH = a.matte_w, a.matte_w * H // W
nv_in = torch.zeros(FR, dtype=torch.uint8, device=dev)
nv_out = torch.zeros(FR, dtype=torch.uint8, device=dev)
small = torch.zeros(SH, SW, 3, dtype=torch.uint8, device=dev)
pha_qs = torch.zeros(QH, QW, device=dev)
S_t, S_c = torch.zeros(1, 1, SH, SW, device=dev), torch.zeros(1, 1, SH, SW, device=dev)
hand_t, hand_c = torch.zeros(1, 1, SH, SW, device=dev), torch.zeros(1, 1, SH, SW, device=dev)
keep_t, keep_c = torch.ones(1, 1, QH, QW, device=dev), torch.ones(1, 1, QH, QW, device=dev)
face_t = torch.tensor([W / 2, H / 2, 300, W / 2, H / 2, W / 2, H / 2, 0], device=dev)   # no face yet
face_c = face_t.clone()
grain_bank = torch.randn(8, 1, 1, H // 2, W // 2, device=dev, generator=gen).to(HALF)
grain = torch.zeros(1, 1, H // 2, W // 2, device=dev, dtype=HALF)
rec: list = []
KK = int(a.spread / 4) * 2 + 1   # eighth-res max-pool window (~= 2 * spread half-res px)


def step(run_rvm=True):
    f0 = nv12_to_rgb(nv_in)
    if run_rvm:
        _, pha_m, *rec_o = rvm(down(f0, MW, MH), *rec, downsample_ratio=a.dsr)
        for r, ro in zip(rec, rec_o):
            r.copy_(ro)
        pha_qs.copy_(rs(pha_m.float(), QW, QH)[0, 0])
    pha_q = pha_qs[None, None]
    small.copy_((down(f0.float(), SW, SH) * 255 + 0.5).clamp(0, 255).to(torch.uint8)[0].permute(1, 2, 0))
    S_c.lerp_(S_t, 0.4)
    hand_c.lerp_(hand_t, 0.4)
    keep_c.lerp_(keep_t, 0.4)
    face_c.lerp_(face_t, 0.4)
    cx, cy, fw, e1x, e1y, e2x, e2y, has = face_c.unbind()
    fw = fw.clamp(min=60)

    # light-falloff map + key light (quarter res)
    P_q = torch.maximum(pha_q * keep_c, rs(hand_c, QW, QH))
    Pd_q = falloff_map(P_q, KK, a.falloff / 4)
    lx, ly = cx - 0.55 * fw, cy - 0.45 * fw
    g = torch.exp(-((xq - lx) ** 2 + (yq - ly) ** 2) / (2 * (1.15 * fw) ** 2))
    farside = torch.sigmoid((xq - (cx + 0.15 * fw)) / (0.25 * fw))
    gain_q = 1.05 * (1 + a.key * g * has) * (1 - 0.15 * has * farside * torch.exp(-((yq - cy) / (1.6 * fw)) ** 2))
    eye_q = has * (torch.exp(-((xq - e1x) ** 2 + (yq - e1y) ** 2) / (2 * (0.09 * fw) ** 2))
                   + torch.exp(-((xq - e2x) ** 2 + (yq - e2y) ** 2) / (2 * (0.09 * fw) ** 2))).clamp(0, 1)
    f = retouch(f0, S_c, P_q, eye_q[None, None], a.smooth)
    lin = grade1_q(f, gain_q[None, None], Pd_q, a.bg, a.bg_desat)
    out = grade2_q(lin, S_c, grain, a.exposure)
    nv_out.copy_(rgb_to_nv12(out))
    return out


def apply(res):
    S, hm, km, fc = res
    S_t.copy_(torch.from_numpy(S)[None, None])
    hand_t.copy_(torch.from_numpy(hm)[None, None])
    keep_t.copy_(torch.from_numpy(km)[None, None])
    if fc is not None:
        face_t.copy_(torch.tensor([*fc.tolist(), 1.0]))


# ---------------------------------------------------------------- main loop
fut = None
graphs: dict[bool, tuple[torch.cuda.CUDAGraph, torch.Tensor]] = {}
pending = None   # (out slot, in slot, frame index): rendered on the GPU, not shipped yet
n, t0 = 0, time.time()


def ship(slot, islot, idx):
    """Wait for frame idx's GPU work, hand it to the encoder, feed MediaPipe."""
    global fut
    done_ev[slot].synchronize()
    if fut is None and idx % a.every == 0 and idx > 0:
        fut = ex.submit(analyze, pin_small[slot].numpy().copy(), pin_pha[slot].numpy().copy(), int(idx * 1000 / a.fps))
    free_in.put(islot)
    outq.put(slot)


def capture(run_rvm: bool) -> None:
    s_ = torch.cuda.Stream()
    s_.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(s_):
        for _ in range(3):
            step(run_rvm)
    torch.cuda.current_stream().wait_stream(s_)
    g = torch.cuda.CUDAGraph()
    with torch.cuda.graph(g):
        out = step(run_rvm)
    graphs[run_rvm] = (g, out)


with torch.no_grad():
    while True:
        islot = inq.get()
        if islot is None:
            break
        slot = free_out.get()
        nv_in.copy_(pin_in[islot], non_blocking=True)
        grain.copy_(grain_bank[n % 8])

        if n == 0:
            # RVM's recurrent state, the first MediaPipe pass, then warm-up + graph capture
            f0 = nv12_to_rgb(nv_in)
            _, _, *r0 = rvm(down(f0, MW, MH), None, None, None, None, downsample_ratio=a.dsr)
            rec.extend(r.clone() for r in r0)
            step()
            torch.cuda.synchronize()
            apply(analyze(small.cpu().numpy(), pha_qs.cpu().numpy(), 0))
            S_c.copy_(S_t), hand_c.copy_(hand_t), keep_c.copy_(keep_t), face_c.copy_(face_t)
            if not a.no_graph:
                capture(True)
                if a.matte_every > 1:
                    capture(False)
            log(f"ready after {time.time() - t0:.0f}s (models, compile{', CUDA graphs' if graphs else ''})")

        if fut is not None and fut.done():
            apply(fut.result())
            fut = None
        full = n % a.matte_every == 0
        if graphs:
            graphs[full][0].replay()
        else:
            step(full)
        pin_out[slot].copy_(nv_out, non_blocking=True)
        if n % a.every == 0:
            pin_small[slot].copy_(small, non_blocking=True)
            pin_pha[slot].copy_(pha_qs, non_blocking=True)
        done_ev[slot].record()

        if pending is not None:
            ship(*pending)   # overlaps with this frame's GPU work
        pending = (slot, islot, n)
        n += 1
        if n % 1800 == 0:
            el = time.time() - t0
            total = f"/{a.frames}" if a.frames else ""
            log(f"{n}{total} frames, {n / el:.1f} fps")

    if pending is not None:
        ship(*pending)

outq.put(None)
wt.join()
dec_rc = dec.wait()
enc_rc = enc.wait()
el = time.time() - t0
log(f"done: {n} frames in {el:.0f}s ({n / el:.1f} fps, {n / a.fps / el:.2f}x realtime)")
if dec_rc != 0:
    log(f"decoder exited with code {dec_rc}")
if a.frames and n < a.frames - 2:
    log(f"only {n} of {a.frames} frames came through")
    os._exit(1)
os._exit(enc_rc or (1 if dec_rc else 0))   # os._exit skips MediaPipe's noisy interpreter shutdown
