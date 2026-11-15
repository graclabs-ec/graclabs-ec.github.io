import argparse
import re
import subprocess

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

ap = argparse.ArgumentParser()
ap.add_argument("script")
ap.add_argument("out_mp3")
ap.add_argument("model_dir")
ap.add_argument("--lang", default="en", choices=["en", "es"])
ap.add_argument("--voice", default=None)
ap.add_argument("--speed", type=float, default=0.9)
ap.add_argument("--model", default="kokoro-v1.0.onnx")
args = ap.parse_args()

MAIN, OTHER = ("en-us", "es-419") if args.lang == "en" else ("es-419", "en-us")
voice = args.voice or ("af_heart" if args.lang == "en" else "ef_dora")
k = Kokoro(f"{args.model_dir}/{args.model}", f"{args.model_dir}/voices-v1.0.bin")
SR = 24000


def silence(sec):
    return np.zeros(int(sec * SR), dtype=np.float32)


def chime():
    t = np.arange(int(1.1 * SR)) / SR
    out = np.zeros_like(t)
    for start, f in ((0.0, 880.0), (0.16, 1318.5)):
        tt = np.clip(t - start, 0, None)
        env = (t >= start) * (1 - np.exp(-tt / 0.004)) * np.exp(-tt / 0.28)
        tone = (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt)
                + 0.08 * np.sin(6 * np.pi * f * tt))
        out += env * tone
    out = 0.22 * out / np.max(np.abs(out))
    return np.concatenate([silence(0.5), out.astype(np.float32), silence(0.6)])


# Fixed pronunciations, used whatever the surrounding language or braces:
# "GRAC Labs" is always said the English way, "grack labs" (rhymes with "crack").
FIXED = {
    "en": [(r"GRAC\s+Labs", "ɡɹˈæk lˈæbz"), (r"GRAC", "ɡɹˈæk")],
    "es": [(r"GRAC\s+Labs", "ɡɹˈæk lˈæbz"), (r"GRAC", "ɡɹˈæk")],
}[args.lang]
FIXED_RE = re.compile(r"\b(?:" + "|".join(p for p, _ in FIXED) + r")\b", re.I)


def say(text, lang):
    out, last = [], 0
    for m in FIXED_RE.finditer(text):
        if text[last:m.start()].strip():
            out.append(k.tokenizer.phonemize(text[last:m.start()], lang))
        out.append(next(ph for pat, ph in FIXED if re.fullmatch(pat, m.group(0), flags=re.I)))
        last = m.end()
    if text[last:].strip():
        out.append(k.tokenizer.phonemize(text[last:], lang))
    return " ".join(o.strip() for o in out if o.strip())


def phonemes(sentence):
    out = []
    for part in re.split(r"(\{[^}]*\})", sentence):
        if not part:
            continue
        if part.startswith("{"):
            ph = say(part[1:-1], OTHER)
        else:
            ph = say(part, MAIN)
        if ph.strip():
            out.append(ph.strip())
    return re.sub(r"\s+([,.;:!?])", r"\1", " ".join(out))


text = open(args.script, encoding="utf-8").read()
paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
audio, pos, chapters = [], 0, []


def add(x):
    global pos
    audio.append(x)
    pos += len(x)


for p in paragraphs:
    m = re.fullmatch(r"\[(cue|chapter)(?::\s*(.+?))?\]", p, flags=re.I)
    if m:
        if m.group(2):
            chapters.append([pos / SR, m.group(2).strip()])
        if m.group(1).lower() == "cue":
            add(chime())
        continue
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡{"])', p.replace("\n", " "))
    for s in sentences:
        ph = phonemes(s)
        if not ph:
            continue
        samples, _ = k.create(ph, voice=voice, speed=args.speed, is_phonemes=True)
        add(samples.astype(np.float32))
        add(silence(0.35))
    add(silence(0.9))

wav = np.concatenate(audio)
total = len(wav) / SR
base = args.out_mp3.rsplit(".", 1)[0]
sf.write(base + ".wav", wav, SR)
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-i", base + ".wav",
     "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
     "-ac", "1", "-ar", "44100", "-b:a", "64k", args.out_mp3],
    check=True,
)

# Spotify chapter rules: first at 00:00, at least 3, at least 30 s apart.
if chapters:
    chapters[0][0] = 0.0
kept = [list(c) for c in chapters]
merged = True
while merged and len(kept) > 1:
    merged = False
    for i in range(len(kept)):
        end = kept[i + 1][0] if i + 1 < len(kept) else total
        if end - kept[i][0] < 30:
            if i + 1 < len(kept):  # too short: fold it into the next chapter
                kept[i + 1][0] = kept[i][0]
            del kept[i]
            merged = True
            break
lines = []
if len(kept) >= 3:
    lines = [f"({int(t // 60):02d}:{int(t % 60):02d}) {title}" for t, title in kept]
open(base + ".chapters.txt", "w", encoding="utf-8").write("\n".join(lines) + ("\n" if lines else ""))
print(f"{args.out_mp3}: {total / 60:.1f} min, {len(lines)} chapters")
