#!/usr/bin/env python3
"""Ball flight tracer.

Tracks the ball after impact (small bright blob moving upward, camera shake compensated) and renders a growing
turquoise line as a sequence of transparent PNG frames that render.py overlays on the clip.

  tracer.py VIDEO --impact T --clip-start S --out-dir DIR [--out-fps 30] [--out-len L] [--debug dbg.jpg]
"""
import argparse, glob, json, os, subprocess
import numpy as np
import cv2

LINE = (255, 230, 90)      # BGR: turquoise
GLOW = (255, 200, 60)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate:stream_side_data=rotation:format=duration",
                          "-of", "json", path], check=True, capture_output=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    rot = abs(int(float((v.get("side_data_list") or [{}])[0].get("rotation", 0)))) % 180
    w, h = (v["height"], v["width"]) if rot == 90 else (v["width"], v["height"])
    num, den = v["r_frame_rate"].split("/")
    return dict(width=int(w), height=int(h), fps=float(num) / float(den or 1), duration=float(info["format"]["duration"]))


def read_gray(path, start, duration, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", path,
                          "-vf", "format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         check=True, capture_output=True).stdout
    n = len(raw) // (w * h)
    return np.frombuffer(raw[: n * w * h], dtype=np.uint8).reshape(n, h, w)


def camera_shifts(frames, scale=4):
    """Per-frame camera translation (full-res pixels) estimated on the upper 70 percent of the frame."""
    h = frames.shape[1]
    small = [cv2.resize(f[: int(h * 0.7)], None, fx=1 / scale, fy=1 / scale).astype(np.float32) for f in frames]
    win = cv2.createHanningWindow((small[0].shape[1], small[0].shape[0]), cv2.CV_32F)
    shifts = [(0.0, 0.0)]
    for a, b in zip(small[:-1], small[1:]):
        (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
        if resp < 0.05 or abs(dx) > 40 or abs(dy) > 40:
            dx, dy = 0.0, 0.0
        shifts.append((dx * scale, dy * scale))
    return np.cumsum(np.array(shifts), axis=0)  # cum[k]: where frame-0 content sits in frame k


def candidates(prev, cur, shift, exclude=None):
    """Small, compact, bright spots that appeared in cur (relative to the camera-aligned prev).
    exclude: (x0, y0, x1, y1) box ignored (the golfer and club during the follow-through)."""
    M = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
    prev_al = cv2.warpAffine(prev, M, (cur.shape[1], cur.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    diff = cur.astype(np.int16) - prev_al.astype(np.int16)
    tophat = cv2.morphologyEx(cur, cv2.MORPH_TOPHAT, TOPHAT_K).astype(np.int16)
    mask = ((diff > 40) & (tophat > 35)).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if not (3 <= area <= 80 and bw <= 14 and bh <= 14):
            continue
        if max(bw, bh) / max(1, min(bw, bh)) > 2 or area / float(bw * bh) < 0.5:
            continue
        px, py = float(cents[i][0]), float(cents[i][1])
        if exclude and exclude[0] <= px <= exclude[2] and exclude[1] <= py <= exclude[3]:
            continue
        out.append((px, py, int(area)))
    return out


TOPHAT_K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))


def drop_noisy_cells(cands, W, H, cell=48, limit=35):
    """Remove candidates in cells where blobs pile up over time (trees, grass, the golfer); the ball's
    path only touches each cell a few times."""
    cols, rows = W // cell + 1, H // cell + 1
    counts = np.zeros((rows, cols), int)
    for pts in cands:
        for x, y, _ in pts:
            counts[int(y) // cell, int(x) // cell] += 1
    return [[p for p in pts if counts[int(p[1]) // cell, int(p[0]) // cell] <= limit] for pts in cands]


def ground_level(video, impact, W, H, fps, cx):
    """Image row of the ball at impact: the lowest rows swept by the club head (the strongest motion in the
    frames right around impact, in front of the golfer). Falls back to 0.8 H."""
    fr = read_gray(video, max(0.0, impact - 0.08), 0.16, W, H)
    if len(fr) < 3:
        return 0.8 * H
    cum = camera_shifts(fr)
    D = np.zeros((H, W), np.int16)
    for i in range(1, len(fr)):
        sh = cum[i] - cum[i - 1]
        pa = cv2.warpAffine(fr[i - 1], np.float32([[1, 0, sh[0]], [0, 1, sh[1]]]), (W, H), borderMode=cv2.BORDER_REPLICATE)
        D = np.maximum(D, np.abs(fr[i].astype(np.int16) - pa.astype(np.int16)))
    x0, x1 = int(max(0, (cx - 0.1) * W)), int(min(W, (cx + 0.3) * W))
    rows = np.nonzero((D[: int(0.97 * H), x0:x1] > 80).sum(axis=1) > 4)[0]
    if len(rows) == 0:
        return 0.8 * H
    return float(np.percentile(rows, 97))


def track_ball(cands, cum, fps, H):
    """Greedy multi-track chaining in stabilised coordinates; returns the best upward track [(k, x, y)]."""
    tracks, active = [], []
    for k, pts in enumerate(cands):
        stab = [(x - cum[k][0], y - cum[k][1]) for x, y, _ in pts]
        used = set()
        for tr in active:
            pts_tr = tr["pts"]
            if len(pts_tr) >= 2:
                (k1, x1, y1), (k2, x2, y2) = pts_tr[-2], pts_tr[-1]
                vx, vy = (x2 - x1) / (k2 - k1), (y2 - y1) / (k2 - k1)
            else:
                vx = vy = 0.0
            gap = k - pts_tr[-1][0]
            px, py = pts_tr[-1][1] + vx * gap, pts_tr[-1][2] + vy * gap
            r = (14 if len(pts_tr) >= 2 else 36) + 0.4 * (vx ** 2 + vy ** 2) ** 0.5 * gap
            best, bd = None, r
            for j, (sx, sy) in enumerate(stab):
                if j in used:
                    continue
                d = ((sx - px) ** 2 + (sy - py) ** 2) ** 0.5
                if d < bd:
                    best, bd = j, d
            if best is not None:
                tr["pts"].append((k, stab[best][0], stab[best][1]))
                tr["miss"] = 0
                used.add(best)
            else:
                tr["miss"] += 1
        active = [tr for tr in active if tr["miss"] <= 4]
        for j, (sx, sy) in enumerate(stab):
            if j not in used:
                tr = {"pts": [(k, sx, sy)], "miss": 0}
                tracks.append(tr)
                active.append(tr)
    def score(tr):
        p = tr["pts"]
        if len(p) < 10:
            return -1
        rise = p[0][2] - p[-1][2]                       # pixels moved upward
        span = (p[-1][0] - p[0][0]) / fps               # seconds
        if rise < 80 or span < 0.4 or p[0][0] > int(1.2 * fps):
            return -1
        k = np.array([q[0] for q in p], float); x = np.array([q[1] for q in p]); y = np.array([q[2] for q in p])
        resid = 0
        for v in (x, y):
            c = np.polyfit(k, v, 2)
            resid += float(np.sqrt(np.mean((np.polyval(c, k) - v) ** 2)))
        if resid > 14:
            return -1                                   # jittery: not a ball
        steps = np.hypot(np.diff(x), np.diff(y)) / np.maximum(1, np.diff(k))
        if not (2 <= steps.mean() <= 70):
            return -1
        return len(p) + rise / 5 - resid * 3 - p[0][0] / fps * 10
    best = max(tracks, key=score, default=None)
    return best["pts"] if best and score(best) > 0 else []


def smooth_path(pts, fps, H, ground=None, t_impact=0.0, extra=0.6):
    """Quadratic fit of the stabilised track in time; the line starts at the launch point (ball at rest) at
    impact time and runs a little beyond the last detection. Returns [(t, x, y)] in stabilised coords."""
    k = np.array([p[0] for p in pts], dtype=float)
    t = k / fps
    x = np.array([p[1] for p in pts]); y = np.array([p[2] for p in pts])
    deg = 2 if len(pts) >= 12 else 1
    fx, fy = np.polyfit(t, x, deg), np.polyfit(t, y, deg)
    x0, y0 = float(np.polyval(fx, t[0])), float(np.polyval(fy, t[0]))
    out = []
    gy = 0.8 * H if ground is None else ground
    vx, vy = float(np.polyval(np.polyder(fx), t[0])), float(np.polyval(np.polyder(fy), t[0]))
    if vy < -1:                                           # straight back along the initial direction
        dt = (gy - y0) / vy                               # negative: time back to the ground line
        launch = (x0 + vx * dt, gy)
    else:
        launch = (x0, gy)
    t_launch = t_impact
    for a in np.arange(t_launch, t[0], 1 / 60):
        u = (a - t_launch) / max(1e-6, t[0] - t_launch)
        out.append((float(a), launch[0] + (x0 - launch[0]) * u, launch[1] + (y0 - launch[1]) * u))
    for a in np.arange(t[0], t[-1] + extra, 1 / 60):
        out.append((float(a), float(np.polyval(fx, a)), float(np.polyval(fy, a))))
    return out


def draw_line(canvas, pts):
    if len(pts) < 2:
        return
    arr = np.array([[int(round(x)), int(round(y))] for x, y in pts], dtype=np.int32)
    glow = np.zeros_like(canvas)
    cv2.polylines(glow, [arr], False, (*GLOW, 120), 22, cv2.LINE_AA)
    glow = cv2.GaussianBlur(glow, (0, 0), 6)
    np.maximum(canvas, glow, out=canvas)
    core = np.zeros_like(canvas)
    cv2.polylines(core, [arr], False, (*LINE, 255), 7, cv2.LINE_AA)
    a = core[:, :, 3:4].astype(np.float32) / 255
    canvas[:] = (core * a + canvas * (1 - a)).astype(np.uint8)


def build(video, impact, clip_start, out_dir, out_fps=30, out_len=None, crop=None, out_size=(1080, 1920),
          window=3.0, debug=None, cx=0.5):
    m = probe(video)
    W, H, fps = m["width"], m["height"], m["fps"]
    read_start = max(0.0, impact - 0.05)
    frames = read_gray(video, read_start, min(window, m["duration"] - read_start), W, H)
    cum = camera_shifts(frames)
    golfer = (cx * W - 0.15 * W, 0.35 * H, cx * W + 0.15 * W, H)   # body only; the sky above stays open
    cands = [[]] + [candidates(frames[i - 1], frames[i], cum[i] - cum[i - 1],
                               exclude=golfer if i < 0.8 * fps else None) for i in range(1, len(frames))]
    cands = drop_noisy_cells(cands, W, H)
    pts = track_ball(cands, cum, fps, H)
    ground = ground_level(video, impact, W, H, fps, cx) if pts else None
    result = dict(ground_y=round(ground, 1) if ground else None, tracked_points=len(pts), frames=len(frames),
                  track_start_s=round(pts[0][0] / fps, 2) if pts else None, track_end_s=round(pts[-1][0] / fps, 2) if pts else None)
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "trace_*.png")):
        os.remove(f)
    if out_len is None:
        out_len = m["duration"] - clip_start
    n_out = int(np.ceil(out_len * out_fps))
    path = smooth_path(pts, fps, H, ground=ground, t_impact=(impact - read_start)) if pts else []
    cx0, cy0, cw, ch = (0, 0, W, H) if crop is None else crop
    sx, sy = out_size[0] / cw, out_size[1] / ch

    def to_out(x, y):
        return (x - cx0) * sx, (y - cy0) * sy

    for i in range(n_out):
        canvas = np.zeros((out_size[1], out_size[0], 4), dtype=np.uint8)
        ts = clip_start + i / out_fps                       # source time of this output frame
        k = int(round((ts - read_start) * fps))
        if path and k >= 0:
            kk = min(k, len(cum) - 1)
            cur = [(x + cum[kk][0], y + cum[kk][1]) for t, x, y in path if t <= (k / fps) + 1e-6]
            draw_line(canvas, [to_out(x, y) for x, y in cur])
        cv2.imwrite(os.path.join(out_dir, f"trace_{i:04d}.png"), canvas)
    if debug:
        k = min(len(frames) - 1, int(1.0 * fps))
        img = cv2.cvtColor(frames[k], cv2.COLOR_GRAY2BGR)
        for kk, x, y in pts:
            cv2.circle(img, (int(x + cum[k][0]), int(y + cum[k][1])), 6, (0, 0, 255), 2)
        if path:
            cv2.circle(img, (int(path[0][1] + cum[k][0]), int(path[0][2] + cum[k][1])), 14, (0, 255, 0), 3)
        for t, x, y in path:
            cv2.circle(img, (int(x + cum[k][0]), int(y + cum[k][1])), 2, (255, 200, 0), -1)
        cv2.imwrite(debug, cv2.resize(img, None, fx=0.5, fy=0.5))
        result["debug"] = debug
    result.update(out_dir=out_dir, frames_written=n_out)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--impact", type=float, required=True)
    ap.add_argument("--clip-start", type=float, required=True); ap.add_argument("--out-dir", required=True)
    ap.add_argument("--out-fps", type=int, default=30); ap.add_argument("--out-len", type=float)
    ap.add_argument("--debug"); ap.add_argument("--cx", type=float, default=0.5)
    a = ap.parse_args()
    print(json.dumps(build(a.video, a.impact, a.clip_start, a.out_dir, a.out_fps, a.out_len, debug=a.debug, cx=a.cx)))


if __name__ == "__main__":
    main()
