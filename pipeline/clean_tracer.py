#!/usr/bin/env python3
"""Remove a baked-in app shot tracer (saturated red line) from a clip by inpainting it frame by frame.

  clean_tracer.py IN.mov OUT.mov [--crop-bottom 0.08]   (also crops an app watermark strip off the bottom)
"""
import argparse, json, subprocess
import numpy as np, cv2


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate:stream_side_data=rotation", "-of", "json", path],
                         check=True, capture_output=True).stdout
    v = json.loads(out)["streams"][0]
    rot = abs(int(float((v.get("side_data_list") or [{}])[0].get("rotation", 0)))) % 180
    w, h = (v["height"], v["width"]) if rot == 90 else (v["width"], v["height"])
    return int(w), int(h), v["r_frame_rate"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out")
    ap.add_argument("--crop-bottom", type=float, default=0.0, help="fraction of the height to cut off the bottom")
    ap.add_argument("--keep-line", action="store_true", help="only crop the watermark; leave the red tracer line")
    a = ap.parse_args()
    W, H, fps = probe(a.inp)
    Hc = int(H * (1 - a.crop_bottom)) // 2 * 2
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", a.inp, "-vf", f"crop={W}:{Hc}:0:0", "-f", "rawvideo",
                            "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{Hc}",
                            "-r", fps, "-i", "-", "-i", a.inp, "-map", "0:v", "-map", "1:a:0?", "-c:v", "libx264",
                            "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", a.out],
                           stdin=subprocess.PIPE)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    n = fixed = 0
    while True:
        buf = dec.stdout.read(W * Hc * 3)
        if len(buf) < W * Hc * 3:
            break
        f = np.frombuffer(buf, np.uint8).reshape(Hc, W, 3).copy()
        b, g, r = f[:, :, 0].astype(int), f[:, :, 1].astype(int), f[:, :, 2].astype(int)
        mask = ((r > 170) & (g < 100) & (b < 100)).astype(np.uint8)   # pure red only, not autumn leaves
        if mask.any() and not a.keep_line:
            mask = cv2.dilate(mask, k)
            f = cv2.inpaint(f, mask, 7, cv2.INPAINT_TELEA)
            fixed += 1
        enc.stdin.write(f.tobytes()); n += 1
    enc.stdin.close(); dec.wait(); enc.wait()
    print(json.dumps({"out": a.out, "frames": n, "frames_inpainted": fixed, "size": f"{W}x{Hc}"}))


if __name__ == "__main__":
    main()
