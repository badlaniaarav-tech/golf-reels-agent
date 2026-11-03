#!/usr/bin/env python3
"""Join rendered 1080x1920 clips into one reel with crossfades.

  montage.py --out reel.mp4 clip1.mp4 clip2.mp4 ... [--xfade 0.3] [--intro "Front 9 highlights"]
             [--music track.mp3] [--music-vol 0.6] [--keep-audio]

By default clip audio is replaced by the music (if given); --keep-audio mixes both.
"""
import argparse, json, os, subprocess, tempfile

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def duration(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", p], check=True, capture_output=True, text=True).stdout
    return float(out.strip())


def make_intro(text, out, secs=1.4):
    tf = out + ".txt"
    with open(tf, "w") as f:
        f.write(text)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r=30:d={secs}",
                    "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={secs}",
                    "-vf", f"drawtext=fontfile={FONT}:textfile={tf}:fontsize=84:fontcolor=white:"
                           f"x=(w-text_w)/2:y=(h-text_h)/2,fade=t=in:d=0.3",
                    "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--xfade", type=float, default=0.3)
    ap.add_argument("--intro")
    ap.add_argument("--music")
    ap.add_argument("--music-vol", type=float, default=0.6)
    ap.add_argument("--keep-audio", action="store_true")
    a = ap.parse_args()

    clips = list(a.clips)
    tmp = tempfile.mkdtemp()
    if a.intro:
        intro = os.path.join(tmp, "intro.mp4")
        make_intro(a.intro, intro)
        clips.insert(0, intro)
    # normalise every clip to the same format so xfade/acrossfade line up
    norm = []
    for i, c in enumerate(clips):
        n = os.path.join(tmp, f"n{i}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", c, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-filter_complex", "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];[a0][1:a]amix=inputs=2:duration=first[a]"
                        if has_audio(c) else "[1:a]anull[a]",
                        "-map", "0:v", "-map", "[a]", "-vf", "scale=1080:1920,fps=30,format=yuv420p",
                        "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-ar", "48000", "-shortest", n], check=True)
        norm.append(n)
    durs = [duration(n) for n in norm]
    d = a.xfade
    f, inputs = [], []
    for n in norm:
        inputs += ["-i", n]
    if len(norm) == 1:
        f.append("[0:v]null[v];[0:a]anull[a]")
        total = durs[0]
    elif d <= 0:
        # hard cuts: plain concat
        f.append("".join(f"[{i}:v][{i}:a]" for i in range(len(norm))) + f"concat=n={len(norm)}:v=1:a=1[v][a]")
        total = sum(durs)
    else:
        off = 0.0
        vprev, aprev = "[0:v]", "[0:a]"
        for i in range(1, len(norm)):
            off += durs[i - 1] - d
            vo, ao = (f"[v{i}]", f"[a{i}]") if i < len(norm) - 1 else ("[v]", "[a]")
            f.append(f"{vprev}[{i}:v]xfade=transition=fade:duration={d}:offset={off:.3f}{vo}")
            f.append(f"{aprev}[{i}:a]acrossfade=d={d}{ao}")
            vprev, aprev = vo, ao
        total = sum(durs) - d * (len(norm) - 1)
    amap = "[a]"
    if a.music:
        inputs += ["-stream_loop", "-1", "-i", a.music]
        mi = len(norm)
        f.append(f"[{mi}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={a.music_vol},"
                 f"afade=t=out:st={max(0, total - 0.8):.2f}:d=0.8[mus]")
        if a.keep_audio:
            f.append("[a][mus]amix=inputs=2:duration=first:dropout_transition=0[aout]")
            amap = "[aout]"
        else:
            amap = "[mus]"
    cmd = ["ffmpeg", "-v", "error", "-y"] + inputs + ["-filter_complex", ";".join(f), "-map", "[v]", "-map", amap,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", "-shortest", a.out]
    subprocess.run(cmd, check=True)
    print(json.dumps(dict(out=os.path.abspath(a.out), seconds=round(total, 2), clips=len(clips))))


def has_audio(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                          "-of", "csv=p=0", p], capture_output=True, text=True).stdout
    return "audio" in out


if __name__ == "__main__":
    main()
