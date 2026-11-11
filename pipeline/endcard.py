#!/usr/bin/env python3
"""End card with the hole result: the reel's last frame, blurred and darkened with a slow push-in, and a
scoreboard panel that fades up.

  endcard.py --from LAST_CLIP.mp4 --out card.mp4 --title "158 YDS | PAR 3" \\
      --team "Juniors=Aarav:3,Harrison:3" --team "Pros=Evan:3,Chase:4" --result "ALL SQUARE" [--secs 3]
"""
import argparse, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import FONT, FONT_HEAVY, FONT_ITALIC, W, H

GOLD = (255, 210, 60, 255)


def panel(title, teams, result, path):
    from PIL import Image, ImageDraw, ImageFont
    f_title = ImageFont.truetype(FONT, 34)
    f_team = ImageFont.truetype(FONT_HEAVY, 64)
    f_player = ImageFont.truetype(FONT, 38)
    f_score = ImageFont.truetype(FONT_HEAVY, 120)
    f_result = ImageFont.truetype(FONT_HEAVY, 86)
    pw, pad = 940, 56
    rows = len(teams)
    ph = pad + 50 + rows * 190 + 40 + 120 + pad
    img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=40, fill=(16, 20, 28, 200))
    d.text((pw / 2, pad + 10), title.upper(), font=f_title, fill=(220, 220, 220, 255), anchor="mm")
    y = pad + 60
    for name, players, total in teams:
        d.rounded_rectangle((pad, y, pw - pad, y + 160), radius=28, fill=(255, 255, 255, 18))
        d.text((pad + 36, y + 46), name.upper(), font=f_team, fill=GOLD, anchor="lm")
        d.text((pad + 36, y + 112), "  ".join(f"{p} {s}" for p, s in players), font=f_player,
               fill=(225, 225, 225, 255), anchor="lm")
        d.text((pw - pad - 40, y + 80), str(total), font=f_score, fill="white", anchor="rm")
        y += 190
    d.line((pad, y + 10, pw - pad, y + 10), fill=(255, 255, 255, 60), width=2)
    d.text((pw / 2, y + 80), result.upper(), font=f_result, fill="white", anchor="mm")
    img.save(path)
    return pw, ph


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="clip whose last frame becomes the background")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--team", action="append", required=True, help='"Name=Player:score,Player:score"')
    ap.add_argument("--result", required=True)
    ap.add_argument("--secs", type=float, default=3.0)
    a = ap.parse_args()
    teams = []
    for t in a.team:
        name, rest = t.split("=", 1)
        players = [(p.split(":")[0].strip(), int(p.split(":")[1])) for p in rest.split(",")]
        teams.append((name.strip(), players, min(s for _, s in players)))   # best ball: lowest score counts
    tmp = tempfile.mkdtemp()
    png = os.path.join(tmp, "panel.png")
    pw, ph = panel(a.title, teams, a.result, png)
    last = os.path.join(tmp, "last.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", a.src, "-frames:v", "1", "-update", "1", last], check=True)
    n = int(a.secs * 30)
    vf = (f"scale={W}:{H},zoompan=z='1+0.08*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps=30,"
          f"boxblur=18:2,eq=brightness=-0.18:saturation=0.85,format=yuv420p[bg];"
          f"[1:v]format=rgba,fade=t=in:st=0.15:d=0.45:alpha=1[p];[bg][p]overlay=x=(W-w)/2:y=(H-h)/2-60:format=auto")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", last, "-loop", "1", "-i", png,
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex", vf, "-t", f"{a.secs}",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", a.out], check=True)
    print({"out": a.out, "seconds": a.secs, "teams": [(t[0], t[2]) for t in teams]})


if __name__ == "__main__":
    main()
