#!/usr/bin/env python3
"""Render one golf shot as a 1080x1920 reel clip.

  render.py IN.mp4 --impact 5.2 --out clip.mp4 [--cx 0.5] [--pre 3] [--post 4]
            [--slow 0.25] [--slow-before 0.35] [--slow-after 0.3] [--smooth]
            [--title "7 iron"] [--caption "165 yards"] [--music track.mp3] [--music-vol 0.5]
            [--mute] [--no-fade]

Timeline: normal speed -> slow motion around impact -> normal speed, cropped to 9:16
centred on cx (0 = left edge, 1 = right edge), with optional text and music.
"""
import argparse, json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import glob


def pick_font(*patterns, fallback="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
    for pat in patterns:
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[0]
    return fallback


FONT = pick_font("/usr/share/fonts/**/Inter*Bold.otf", "/usr/share/fonts/**/Inter*Bold.ttf")
FONT_HEAVY = pick_font("/usr/share/fonts/**/InterDisplay-Black.otf", "/usr/share/fonts/**/Inter*Black.*",
                       "/usr/share/fonts/**/Inter*ExtraBold.*", fallback=FONT)
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_ROUND = pick_font(os.path.join(HERE, "..", "assets", "fonts", "Nunito*.ttf"), fallback=None)
FONT_ITALIC = pick_font("/usr/share/fonts/**/Inter*BoldItalic.otf", "/usr/share/fonts/**/Inter*BoldItalic.ttf",
                        fallback="/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf")
W, H = 1080, 1920


def text_width(text, font, size):
    """Pixel width of text as ffmpeg's drawtext will render it (same freetype face)."""
    try:
        from PIL import ImageFont
        return ImageFont.truetype(font, size).getlength(text)
    except Exception:
        return len(text) * size * 0.6


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate:stream_side_data=rotation:format=duration",
                          "-of", "json", path], check=True, capture_output=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    rot = abs(int(float((v.get("side_data_list") or [{}])[0].get("rotation", 0)))) % 180
    if rot == 90:
        v["width"], v["height"] = v["height"], v["width"]
    num, den = v["r_frame_rate"].split("/")
    return dict(duration=float(info["format"]["duration"]), width=int(v["width"]),
                height=int(v["height"]), fps=float(num) / float(den or 1),
                has_audio=any(s["codec_type"] == "audio" for s in info["streams"]))


def crop_filter(w, h, cx):
    if w / h > 9 / 16:                       # wider than 9:16 -> crop width
        cw = int(h * 9 / 16) // 2 * 2
        x = int(min(max(cx * w - cw / 2, 0), w - cw))
        return f"crop={cw}:{h}:{x}:0"
    ch = int(w * 16 / 9) // 2 * 2            # taller than 9:16 -> crop height (centre)
    return f"crop={w}:{ch}:0:{(h - ch) // 2}"


def atempo_chain(factor):
    """atempo only accepts 0.5..100 per stage; chain stages for slower factors."""
    parts = []
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    parts.append(f"atempo={factor:.4f}")
    return ",".join(parts)


def drawtext(text, y_expr, size, tmpdir, name, x_expr="(w-text_w)/2", font=None, color="white",
             extra="borderw=4:bordercolor=black@0.7", enable=None):
    p = os.path.join(tmpdir, name)
    with open(p, "w") as f:
        f.write(text)
    s = (f"drawtext=fontfile={font or FONT}:textfile={p}:fontsize={size}:fontcolor={color}:"
         f"{extra}:x='{x_expr}':y='{y_expr}'")
    if enable:
        s += f":enable='{enable}'"
    return s


def stat_cards_png(stats, path):
    """stats: list of (LABEL, value, unit). Draws a row of up to 3 rounded translucent cards (1080 wide)."""
    from PIL import Image, ImageDraw, ImageFont
    n = len(stats)
    cw, ch, gap = (230 if n == 3 else 260), 150, 20
    total = n * cw + (n - 1) * gap
    img = Image.new("RGBA", (W, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f_label = ImageFont.truetype(FONT, 22)
    f_value = ImageFont.truetype(FONT_HEAVY, 60)
    f_unit = ImageFont.truetype(FONT_ITALIC, 24)
    x = (W - total) // 2
    for label, value, unit in stats:
        d.rounded_rectangle((x, 0, x + cw, ch), radius=24, fill=(16, 20, 28, 175))
        cx = x + cw / 2
        d.text((cx, 30), label.upper(), font=f_label, fill="white", anchor="mm")
        d.text((cx, 76), str(value), font=f_value, fill="white", anchor="mm")
        d.text((cx, 124), unit, font=f_unit, fill=(230, 230, 230, 255), anchor="mm")
        x += cw + gap
    img.save(path)
    return path


def match_overlays(a, tmp, out_len):
    """Overlays in the 1v1 match style, animated like the reference reels:
    banner slides in from the left behind a red bracket; hole info types itself out with a soft fade;
    the situation label is static; dialogue captions slide in from the right."""
    f = []
    banner_end = a.banner_secs if a.banner else 0
    if a.banner:
        D = 0.5  # slide-in seconds, ease-out cubic
        x_final = 64
        x_expr = f"if(lt(t,{D}),{x_final}-({x_final}+tw+40)*pow(1-t/{D},3),{x_final})"
        f.append(drawtext(a.banner.upper(), "h*0.165+14", 68, tmp, "banner.txt", x_expr=x_expr,
                          font=FONT_HEAVY, color="black", extra="box=1:boxcolor=white:boxborderw=16",
                          enable=f"lt(t,{banner_end})"))
        # red "[" bracket anchored at the left of the banner, drawn on top so the banner slides in behind it.
        # Its height matches the white box exactly: text ink height + 2 * boxborderw (measured with the same font).
        from PIL import ImageFont
        bb = ImageFont.truetype(FONT_HEAVY, 68).getbbox(a.banner.upper())
        hb = (bb[3] - bb[1]) + 2 * 16
        y0 = "ih*0.165+14-16"                               # top of the white box
        for box in (f"x=34:y={y0}:w=14:h={hb}", f"x=34:y={y0}:w=44:h=12", f"x=34:y={y0}+{hb}-12:w=44:h=12"):
            f.append(f"drawbox={box}:color=#e0262c:t=fill:enable='between(t,0.08,{banner_end})'")
    if a.hole:
        # typewriter: one drawtext layer per prefix, each fading in; identical glyphs overlap exactly
        font, size, step, fade = (FONT_ROUND or FONT), 62, 0.06, 0.18
        x0 = int((W - text_width(a.hole, font, size)) / 2)
        # y is set from a fixed baseline so every prefix layer lands on exactly the same pixels
        y_expr = f"h*0.17+{int(size * 0.95)}-ascent"
        for i in range(1, len(a.hole) + 1):
            ts = banner_end + (i - 1) * step
            f.append(drawtext(a.hole[:i], y_expr, size, tmp, f"hole{i}.txt", x_expr=str(x0), font=font,
                              extra=f"shadowx=3:shadowy=3:shadowcolor=black@0.5:alpha='min(1,(t-{ts:.3f})/{fade})'",
                              enable=f"gte(t,{ts:.3f})"))
    if a.label:
        x = "w*0.72" if a.label_side == "right" else "w*0.07"
        f.append(drawtext(a.label, "h*0.58", 52, tmp, "label.txt", x_expr=x, font=(FONT_ROUND or FONT),
                          extra="shadowx=3:shadowy=3:shadowcolor=black@0.5"))
    for i, d in enumerate(a.dialogue or []):
        # "text@start-end" (seconds in the OUTPUT clip); "text" alone shows for the last 2.5 s
        if "@" in d:
            text, span = d.rsplit("@", 1)
            t0, t1 = (float(x) for x in span.split("-"))
        else:
            text, t0, t1 = d, max(0, out_len - 2.5), out_len
        D = 0.25  # slide in from the right
        x_expr = f"if(lt(t-{t0:.2f},{D}),(w-tw)/2+(w/2)*pow(1-(t-{t0:.2f})/{D},3),(w-tw)/2)"
        f.append(drawtext(text, "h*0.84", 52, tmp, f"dlg{i}.txt", x_expr=x_expr, font=FONT_ITALIC,
                          color="#ffd21f", extra="borderw=3:bordercolor=black@0.85",
                          enable=f"between(t,{t0:.2f},{t1:.2f})"))
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--impact", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cx", type=float, default=0.5)
    ap.add_argument("--pre", type=float, default=3.0)
    ap.add_argument("--post", type=float, default=4.0)
    ap.add_argument("--slow", type=float, default=0.25, help="speed factor in the slow window (0.25 = 4x slower); 1 disables")
    ap.add_argument("--slow-before", type=float, default=0.35)
    ap.add_argument("--slow-after", type=float, default=0.3)
    ap.add_argument("--smooth", action="store_true", help="frame-interpolate the slow section (slower render, smoother)")
    ap.add_argument("--title")
    ap.add_argument("--caption")
    ap.add_argument("--music")
    ap.add_argument("--music-vol", type=float, default=0.5)
    ap.add_argument("--mute", action="store_true", help="drop the original audio")
    ap.add_argument("--no-fade", action="store_true")
    g = ap.add_argument_group("match style overlays (1v1 reels)")
    g.add_argument("--banner", help='matchup banner, e.g. "Aarav vs Luke" (shown for --banner-secs)')
    g.add_argument("--banner-secs", type=float, default=4.0)
    g.add_argument("--hole", help='hole info, e.g. "431 Yards | Par 4"')
    g.add_argument("--label", help='situation label, e.g. "For Birdie", "To Tie"')
    g.add_argument("--label-side", choices=["left", "right"], default="right")
    g.add_argument("--dialogue", action="append", help='yellow caption: "text@start-end" (output seconds), repeatable')
    g.add_argument("--stats", help='launch monitor cards, e.g. "Carry=136=yds,Spin=9439=rpm,Pull=1.0°=left" (max 3)')
    g.add_argument("--stats-at", default=None, help='"start-end" in output seconds; default: impact+0.6 to end')
    g.add_argument("--tracer", action="store_true", help="draw a ball-flight tracer line after impact (real-time clips only)")
    a = ap.parse_args()

    m = probe(a.input)
    S = max(0.0, a.impact - a.pre)
    E = min(m["duration"], a.impact + a.post)
    A = max(S, a.impact - a.slow_before)
    B = min(E, a.impact + a.slow_after)
    use_slow = a.slow < 1 and B > A
    out_len = (A - S) + ((B - A) / a.slow if use_slow else (B - A)) + (E - B)
    tmp = tempfile.mkdtemp()

    f = []
    segs = [("s1", S, A, False), ("s2", A, B, use_slow), ("s3", B, E, False)]
    segs = [s for s in segs if s[2] - s[1] > 0.01]
    for name, s0, s1, slow in segs:
        chain = f"[0:v]trim=start={s0:.3f}:end={s1:.3f},setpts=PTS-STARTPTS"
        if slow:
            if a.smooth:
                chain += f",minterpolate=fps={int(m['fps'] / a.slow)}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir"
            chain += f",setpts=PTS/{a.slow}"
        f.append(f"{chain}[{name}]")
    f.append("".join(f"[{s[0]}]" for s in segs) + f"concat=n={len(segs)}:v=1:a=0[vc]")

    post = [crop_filter(m["width"], m["height"], a.cx), f"scale={W}:{H}:flags=lanczos", "fps=30", "format=yuv420p"]
    if a.title:
        post.append(drawtext(a.title, "h*0.10", 72, tmp, "title.txt"))
    if a.caption:
        post.append(drawtext(a.caption, "h*0.80", 56, tmp, "caption.txt"))
    post += match_overlays(a, tmp, out_len)
    if not a.no_fade:
        post.append(f"fade=t=in:d=0.25,fade=t=out:st={max(0, out_len - 0.4):.2f}:d=0.4")
    extra_inputs = []   # list of (ffmpeg input args, filter-building fn(idx, src_label, dst_label))
    if a.tracer and use_slow:
        print("tracer skipped: needs --slow 1", file=sys.stderr)
    elif a.tracer:
        import tracer as _tracer
        crop_f = crop_filter(m["width"], m["height"], a.cx)
        cw, ch, cx0, cy0 = (int(v) for v in crop_f.split("=")[1].split(":"))
        tdir = os.path.join(tmp, "trace")
        info = _tracer.build(a.input, a.impact, S, tdir, out_fps=30, out_len=out_len, crop=(cx0, cy0, cw, ch),
                             out_size=(W, H), cx=a.cx)
        print(json.dumps({"tracer": info}), file=sys.stderr)
        if info["tracked_points"]:
            extra_inputs.append((["-framerate", "30", "-i", os.path.join(tdir, "trace_%04d.png")],
                                 lambda i, src, dst: [f"[{i}:v]format=rgba[trc{i}]",
                                                      f"{src}[trc{i}]overlay=x=0:y=0:eof_action=pass{dst}"]))
    if a.stats:
        stats = [tuple(x.strip() for x in item.split("=")) for item in a.stats.split(",")][:3]
        png = stat_cards_png(stats, os.path.join(tmp, "stats.png"))
        if a.stats_at:
            s0, s1 = (float(x) for x in a.stats_at.split("-"))
        else:
            s0, s1 = (A - S) + (a.slow_before if use_slow else 0) + 0.6, out_len
        s0, s1 = max(0.0, s0), min(out_len, s1)
        extra_inputs.append((["-loop", "1", "-i", png],
                             lambda i, src, dst, s0=s0, s1=s1: [
                                 f"[{i}:v]format=rgba,fade=t=in:st={s0:.2f}:d=0.3:alpha=1,setpts=PTS-STARTPTS[cards{i}]",
                                 f"{src}[cards{i}]overlay=x=0:y=H*0.085:enable='between(t,{s0:.2f},{s1:.2f})':eof_action=pass{dst}"]))
    f.append("[vc]" + ",".join(post) + ("[v0]" if extra_inputs else "[vout]"))

    audio_label = None
    if m["has_audio"] and not a.mute:
        al = []
        for name, s0, s1, slow in segs:
            chain = f"[0:a]atrim=start={s0:.3f}:end={s1:.3f},asetpts=PTS-STARTPTS"
            if slow:
                chain += "," + atempo_chain(a.slow)
            f.append(f"{chain}[a{name}]")
            al.append(f"[a{name}]")
        f.append("".join(al) + f"concat=n={len(segs)}:v=0:a=1[ac]")
        audio_label = "[ac]"
    inputs = ["-i", a.input]
    src = "[v0]"
    for n, (args, mk) in enumerate(extra_inputs):
        inputs += args
        dst = "[vout]" if n == len(extra_inputs) - 1 else f"[v{n + 1}]"
        f += mk(n + 1, src, dst)
        src = dst
    music_idx = 1 + len(extra_inputs)
    if a.music:
        inputs += ["-stream_loop", "-1", "-i", a.music]
        f.append(f"[{music_idx}:a]atrim=0:{out_len:.3f},asetpts=PTS-STARTPTS,volume={a.music_vol}[mus]")
        if audio_label:
            f.append(f"{audio_label}[mus]amix=inputs=2:duration=first:dropout_transition=0,"
                     f"afade=t=out:st={max(0, out_len - 0.6):.2f}:d=0.6[aout]")
        else:
            f.append(f"[mus]afade=t=out:st={max(0, out_len - 0.6):.2f}:d=0.6[aout]")
        audio_label = "[aout]"

    cmd = ["ffmpeg", "-v", "error", "-y"] + inputs + ["-filter_complex", ";".join(f), "-map", "[vout]"]
    if audio_label:
        cmd += ["-map", audio_label, "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", "-shortest", a.out]
    subprocess.run(cmd, check=True)
    print(json.dumps(dict(out=os.path.abspath(a.out), seconds=round(out_len, 2), slow=use_slow)))


if __name__ == "__main__":
    main()
