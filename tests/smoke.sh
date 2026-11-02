#!/usr/bin/env bash
# Builds a synthetic "swing" clip (moving bar + strike sound at 5 s and 9 s) and runs the whole pipeline on it.
set -euo pipefail
cd "$(dirname "$0")/.."
T=work/test; mkdir -p $T
python3 - <<'PY'
import numpy as np, wave
sr=16000; d=12; t=np.arange(sr*d)/sr; a=np.random.randn(len(t))*0.01
for imp in (5.0,9.0):
    i=int(imp*sr); n=int(0.03*sr); a[i:i+n]+=np.random.randn(n)*0.8*np.exp(-np.arange(n)/(0.005*sr))
w=wave.open("work/test/a.wav","wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
w.writeframes((np.clip(a,-1,1)*32767).astype(np.int16).tobytes()); w.close()
PY
ffmpeg -v error -y -f lavfi -i "color=c=0x2e7d32:s=1920x1080:r=30:d=12" -f lavfi -i "color=c=white:s=50x400:r=30:d=12" -i $T/a.wav \
  -filter_complex "[0][1]overlay=x='if(between(t,4.6,5.2),1200+(t-4.6)*1000,if(between(t,8.6,9.2),500+(t-8.6)*1000,if(gt(t,7),500,1200)))':y=300:eval=frame[v]" \
  -map "[v]" -map 2:a -c:v libx264 -crf 23 -c:a aac -shortest $T/swing.mp4
python3 pipeline/analyze.py $T/swing.mp4 --sheet $T/sheets --json $T/swing.json > /dev/null
python3 pipeline/render.py $T/swing.mp4 --impact 5.0 --cx 0.77 --title "7 iron" --caption "Range" --out $T/c1.mp4
python3 pipeline/render.py $T/swing.mp4 --impact 9.0 --cx 0.40 --title "Driver" --out $T/c2.mp4
python3 pipeline/montage.py $T/c1.mp4 $T/c2.mp4 --intro "Smoke test" --out $T/reel.mp4
ffprobe -v error -show_entries stream=width,height -of csv=p=0 $T/reel.mp4 | head -1 | grep -q '^1080,1920$' && echo "SMOKE OK: $T/reel.mp4"
