#!/usr/bin/env python3
"""Find golf shots in a video.

Detects the moment of impact from the audio transient (club hitting ball) combined
with the burst of motion in the swing, and estimates where in the frame the action
happens so the 9:16 crop can be centred on the golfer.

Usage:
  analyze.py VIDEO [--max-shots N] [--sheet DIR] [--json OUT]

Prints JSON: {duration, width, height, fps, has_audio, shots:[{impact,start,end,cx,score,...}]}
"""
import argparse, json, os, subprocess, sys
import numpy as np

AUDIO_SR = 16000
HOP = 0.01          # 10 ms audio windows
MOTION_FPS = 10
MOTION_W = 96


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw).stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_type,width,height,r_frame_rate:stream_side_data=rotation:format=duration",
               "-of", "json", path])
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    rot = abs(int(float((v.get("side_data_list") or [{}])[0].get("rotation", 0)))) % 180
    if rot == 90:
        v["width"], v["height"] = v["height"], v["width"]
    num, den = v["r_frame_rate"].split("/")
    fps = float(num) / float(den or 1)
    return dict(duration=float(info["format"]["duration"]), width=int(v["width"]),
                height=int(v["height"]), fps=round(fps, 3), has_audio=has_audio)


def audio_envelope(path):
    """RMS energy per 10 ms window."""
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(AUDIO_SR),
               "-f", "s16le", "-"])
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop = int(AUDIO_SR * HOP)
    n = len(pcm) // hop
    if n == 0:
        return np.zeros(1)
    frames = pcm[: n * hop].reshape(n, hop)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-12)


def motion_profile(path, width, height):
    """Per-frame motion energy (mean abs frame difference) and per-column motion energy."""
    h = max(2, int(round(MOTION_W * height / width / 2)) * 2)
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-vf",
               f"fps={MOTION_FPS},scale={MOTION_W}:{h},format=gray", "-f", "rawvideo", "-"])
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, MOTION_W).astype(np.float32)
    if len(frames) < 2:
        return np.zeros(1), np.zeros((1, MOTION_W))
    diff = np.abs(frames[1:] - frames[:-1])
    return diff.mean(axis=(1, 2)), diff.mean(axis=1)  # (T,), (T, W)


def smooth(x, k):
    if k <= 1 or len(x) < k:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def pick_peaks(score, min_sep, max_n, thresh):
    """Greedy peak picking with minimum separation (in samples)."""
    order = np.argsort(score)[::-1]
    picked = []
    for i in order:
        if score[i] < thresh or len(picked) >= max_n:
            break
        if all(abs(i - j) >= min_sep for j in picked):
            picked.append(int(i))
    return picked


def analyze(path, max_shots=8, pre=3.0, post=4.0):
    meta = probe(path)
    dur = meta["duration"]
    motion, col_motion = motion_profile(path, meta["width"], meta["height"])
    motion_s = smooth(motion, 3)
    m_mu, m_sd = motion_s.mean(), motion_s.std() + 1e-6
    motion_z = (motion_s - m_mu) / m_sd

    shots = []
    if meta["has_audio"]:
        env = audio_envelope(path)
        # onset ratio: energy now vs. median of previous 300 ms
        prev = np.array([np.median(env[max(0, i - 30):i]) if i > 0 else env[0] for i in range(len(env))])
        onset = env / (prev + 1e-4)
        floor = np.percentile(env, 60)
        onset[env < floor * 1.5] = 0  # ignore quiet blips
        onset_log = np.log1p(onset)
        # combine with motion in a ±0.6 s window
        t_axis = np.arange(len(env)) * HOP
        mz = np.array([motion_z[min(len(motion_z) - 1, max(0, int(t * MOTION_FPS) - 2)):
                                min(len(motion_z), int(t * MOTION_FPS) + 4)].max()
                       if len(motion_z) else 0 for t in t_axis])
        score = onset_log + 0.6 * np.clip(mz, 0, 4)
        min_sep = int(3.0 / HOP)
        idx = pick_peaks(score, min_sep, max_shots, thresh=np.log1p(4.0))
        for i in idx:
            shots.append(dict(impact=round(float(i * HOP), 2), score=round(float(score[i]), 2),
                              audio_onset=round(float(onset[i]), 1), motion_z=round(float(mz[i]), 2),
                              source="audio+motion"))

    if not shots:
        # No audio or no clear strike: use motion bursts, impact ~ the motion peak.
        idx = pick_peaks(motion_z, int(3.0 * MOTION_FPS), max_shots, thresh=1.5)
        if not idx and len(motion_z):
            idx = [int(np.argmax(motion_z))]
        for i in idx:
            shots.append(dict(impact=round(float((i + 1) / MOTION_FPS), 2),
                              score=round(float(motion_z[i]), 2), motion_z=round(float(motion_z[i]), 2),
                              source="motion"))

    # Crop centre: motion centroid in the second before impact (the swing).
    for s in shots:
        a = max(0, int((s["impact"] - 1.0) * MOTION_FPS))
        b = min(len(col_motion), int((s["impact"] + 0.3) * MOTION_FPS) + 1)
        cols = col_motion[a:b].sum(axis=0) if b > a else col_motion.sum(axis=0)
        cols = cols - np.percentile(cols, 30)
        cols = np.clip(cols, 0, None)
        cx = float((cols * np.arange(len(cols))).sum() / cols.sum() / (len(cols) - 1)) if cols.sum() > 0 else 0.5
        s["cx"] = round(min(max(cx, 0.0), 1.0), 3)
        s["start"] = round(max(0.0, s["impact"] - pre), 2)
        s["end"] = round(min(dur, s["impact"] + post), 2)

    shots.sort(key=lambda s: s["impact"])
    meta.update(file=os.path.abspath(path), shots=shots)
    return meta


def contact_sheet(path, shot, out):
    """8x2 strip of frames from impact-1.0 s to impact+0.6 s at 10 fps, for visual checking."""
    start = max(0.0, shot["impact"] - 1.0)
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", "1.6", "-i", path,
         "-vf", "fps=10,scale=240:-2,tile=8x2", "-frames:v", "1", out])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--max-shots", type=int, default=8)
    ap.add_argument("--pre", type=float, default=3.0, help="seconds before impact to keep")
    ap.add_argument("--post", type=float, default=4.0, help="seconds after impact to keep")
    ap.add_argument("--sheet", help="directory to write per-shot contact sheets (jpg)")
    ap.add_argument("--json", help="write result JSON here (also printed)")
    a = ap.parse_args()
    res = analyze(a.video, a.max_shots, a.pre, a.post)
    if a.sheet:
        os.makedirs(a.sheet, exist_ok=True)
        base = os.path.splitext(os.path.basename(a.video))[0]
        for i, s in enumerate(res["shots"]):
            s["sheet"] = os.path.join(a.sheet, f"{base}_shot{i + 1}.jpg")
            contact_sheet(a.video, s, s["sheet"])
    txt = json.dumps(res, indent=2)
    if a.json:
        with open(a.json, "w") as f:
            f.write(txt)
    print(txt)


if __name__ == "__main__":
    main()
