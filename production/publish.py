"""Publish one day's episodes of The Cuenca Brief and Cuenca en Breve.

Copies the MP3s into <show>/audio/, records each episode in <show>/episodes.json,
drops episodes (and their audio) older than the retention window in shows.json,
rebuilds both RSS feeds and show pages, then commits and pushes so GitHub Pages
serves the update.

Example:
  python3 -I production/publish.py --repo /path/to/graclabs-ec.github.io --date 2026-11-17 \
      --en cuenca-brief-2026-11-17.mp3 --en-notes cuenca-brief-2026-11-17-notes.txt \
      --es cuenca-en-breve-2026-11-17.mp3 --es-notes cuenca-en-breve-2026-11-17-notes.txt

Re-running for the same date replaces that day's episodes instead of duplicating them.
Use --rebuild-only to regenerate feeds and pages from episodes.json without uploading.
"""
import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape, quoteattr

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True, help="path to the graclabs-ec.github.io clone")
ap.add_argument("--date", help="episode date, YYYY-MM-DD (Cuenca time)")
ap.add_argument("--kind", default="full", choices=["full", "trailer"])
ap.add_argument("--en")
ap.add_argument("--en-notes")
ap.add_argument("--es")
ap.add_argument("--es-notes")
ap.add_argument("--rebuild-only", action="store_true")
ap.add_argument("--no-push", action="store_true")
ap.add_argument("--commit-note", default="", help="extra lines appended to the commit message")
args = ap.parse_args()

REPO = os.path.abspath(args.repo)
CFG = json.load(open(os.path.join(REPO, "production", "shows.json"), encoding="utf-8"))
SITE = CFG["site"]
GH_REPO = CFG["repo"]


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def duration_seconds(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        check=True, capture_output=True, text=True).stdout.strip()
    return int(round(float(out)))


def read_notes(path):
    lines = open(path, encoding="utf-8").read().strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    title = re.sub(r"^\s*(title|t[ií]tulo)\s*:\s*", "", lines[0], flags=re.I).strip()
    body = "\n".join(lines[1:]).strip()
    return title, body


# The GRAC Labs WhatsApp number (any spacing, with or without +593 or a leading 0) becomes a
# WhatsApp link, swallowing a "(https://wa.me/...)" that follows it; other URLs and email
# addresses become plain links. One pass, so nothing is linked twice.
WA_DIGITS = re.sub(r"\D", "", CFG.get("whatsapp", "+593 99 535 8888"))
_local = WA_DIGITS[3:] if WA_DIGITS.startswith("593") else WA_DIGITS
_sep = r"[\s-]?"
WA_NUM = r"(?:\+?593" + _sep + _sep.join(_local) + r"|0" + _sep.join(_local) + r")"
TOKEN_RE = re.compile(
    r"(?P<wa>" + WA_NUM + r")(?P<waurl>\s*\(https?://wa\.me/\d+\))?"
    r"|(?P<url>https?://[^\s<>\"]+[^\s<>\".,;:!?)\]])"
    r"|(?P<email>[\w.+-]+@[\w-]+(?:\.[\w-]+)+)")


def _link(m):
    if m.group("wa"):
        return f'<a href="https://wa.me/{WA_DIGITS}">{m.group("wa")}</a>'
    if m.group("url"):
        return f'<a href="{m.group("url")}">{m.group("url")}</a>'
    return f'<a href="mailto:{m.group("email")}">{m.group("email")}</a>'


def notes_html(text):
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    out = []
    for p in paras:
        lines = []
        for line in p.splitlines():
            esc = html.escape(line.strip(), quote=False)
            lines.append(TOKEN_RE.sub(_link, esc))
        out.append("<p>" + "<br>".join(lines) + "</p>")
    return "\n".join(out)


def cdata(s):
    return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def load_episodes(slug):
    path = os.path.join(REPO, slug, "episodes.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return []


def save_episodes(slug, eps):
    eps.sort(key=lambda e: e["published"], reverse=True)
    with open(os.path.join(REPO, slug, "episodes.json"), "w", encoding="utf-8") as f:
        json.dump(eps, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_feed(show, eps):
    slug = show["slug"]
    base = f"{SITE}/{slug}/"
    cover = f"{SITE}/{slug}/cover.jpg"
    now = email.utils.format_datetime(dt.datetime.now(dt.timezone.utc), usegmt=True)
    cat, sub = show["category"]
    x = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
         'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
         'xmlns:atom="http://www.w3.org/2005/Atom">',
         "<channel>",
         f"<title>{escape(show['title'])}</title>",
         f"<link>{escape(base)}</link>",
         f'<atom:link href={quoteattr(base + "feed.xml")} rel="self" type="application/rss+xml"/>',
         f"<language>{show['language']}</language>",
         f"<copyright>{escape('© ' + str(dt.date.today().year) + ' ' + CFG['owner_name'])}</copyright>",
         f"<description>{cdata(show['description'])}</description>",
         f"<itunes:author>{escape(CFG['owner_name'])}</itunes:author>",
         "<itunes:owner>",
         f"<itunes:name>{escape(CFG['owner_name'])}</itunes:name>",
         f"<itunes:email>{escape(CFG['owner_email'])}</itunes:email>",
         "</itunes:owner>",
         f"<itunes:image href={quoteattr(cover)}/>",
         f"<image><url>{escape(cover)}</url><title>{escape(show['title'])}</title><link>{escape(base)}</link></image>",
         f"<itunes:category text={quoteattr(cat)}><itunes:category text={quoteattr(sub)}/></itunes:category>",
         "<itunes:explicit>false</itunes:explicit>",
         "<itunes:type>episodic</itunes:type>",
         f"<lastBuildDate>{now}</lastBuildDate>",
         "<generator>GRAC Labs publish.py</generator>"]
    for e in eps:
        body = notes_html(e["notes"])
        x += ["<item>",
              f"<title>{escape(e['title'])}</title>",
              f"<link>{escape(base + '#' + e['guid'])}</link>",
              f"<description>{cdata(body)}</description>",
              f"<content:encoded>{cdata(body)}</content:encoded>",
              f'<enclosure url={quoteattr(e["url"])} length="{e["bytes"]}" type="audio/mpeg"/>',
              f'<guid isPermaLink="false">{escape(e["guid"])}</guid>',
              f"<pubDate>{e['pub_date']}</pubDate>",
              f"<itunes:duration>{e['seconds']}</itunes:duration>",
              f"<itunes:episodeType>{e['kind']}</itunes:episodeType>",
              "<itunes:explicit>false</itunes:explicit>",
              "</item>"]
    x += ["</channel>", "</rss>"]
    with open(os.path.join(REPO, slug, "feed.xml"), "w", encoding="utf-8") as f:
        f.write("\n".join(x) + "\n")


def build_page(show, eps):
    slug, lab = show["slug"], show["labels"]
    items = []
    for e in eps:
        when = e["published"][:10]
        mins = max(1, round(e["seconds"] / 60))
        items.append(
            f'<article class="ep" id="{html.escape(e["guid"])}">\n'
            f'<h3>{html.escape(e["title"])}</h3>\n'
            f'<p class="meta">{when} · {mins} {lab["minutes"]}</p>\n'
            f'<audio controls preload="none" src="{html.escape(e["url"])}"></audio>\n'
            f'<details open><summary>{html.escape(lab.get("notes", "Notes"))}</summary>\n{notes_html(e["notes"])}\n</details>\n'
            f'</article>')
    links = show.get("links", {})
    listen = ""
    if links.get("spotify"):
        listen = (f'<p class="listen"><a class="btn spotify" href="{html.escape(links["spotify"])}">'
                  f'{html.escape(lab.get("spotify", "Spotify"))}</a></p>')
    page = f"""<!DOCTYPE html>
<html lang="{show['language']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(show['title'])} · GRAC Labs</title>
<meta name="description" content="{html.escape(show['page_intro'])}">
<link rel="alternate" type="application/rss+xml" title="{html.escape(show['title'])}" href="feed.xml">
<link rel="icon" href="cover.jpg">
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
<header class="show">
<a class="home" href="../">← {html.escape(lab['home'])}</a>
<img src="cover.jpg" alt="{html.escape(show['title'])}" width="220" height="220">
<div>
<h1>{html.escape(show['title'])}</h1>
<p>{html.escape(show['page_intro'])}</p>
<p class="feed">{html.escape(lab['subscribe'])}: <a href="feed.xml">{SITE}/{slug}/feed.xml</a></p>
{listen}
</div>
</header>
<main>
<h2>{html.escape(lab['episodes'])}</h2>
{chr(10).join(items)}
</main>
<footer>GRAC Labs · <a href="https://wa.me/593995358888">WhatsApp +593 99 535 8888</a> · <a href="mailto:graclabs.ec@gmail.com">graclabs.ec@gmail.com</a></footer>
</body>
</html>
"""
    with open(os.path.join(REPO, slug, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def git_publish(msg):
    g = ["git", "-C", REPO, "-c", "user.name=GRAC Labs", "-c", f"user.email={CFG['owner_email']}"]
    run(g + ["add", "-A"])
    if subprocess.run(["git", "-C", REPO, "diff", "--cached", "--quiet"]).returncode == 0:
        print("nothing to commit")
        return
    if args.commit_note.strip():
        msg += "\n\n" + args.commit_note.strip()
    run(g + ["commit", "-q", "-m", msg])
    if args.no_push:
        return
    push = ["git", "-C", REPO, "-c", "push.negotiate=true", "push", "origin", "HEAD:main"]
    if subprocess.run(push).returncode != 0:
        run(["git", "-C", REPO, "fetch", "--depth=50", "origin", "main"])
        run(g + ["rebase", "origin/main"])
        run(push)


if args.rebuild_only:
    for show in CFG["shows"].values():
        eps = load_episodes(show["slug"])
        build_feed(show, eps)
        build_page(show, eps)
    git_publish("Rebuild feeds and pages")
    sys.exit(0)

if not args.date:
    sys.exit("--date is required")
pairs = [(lang, mp3, notes) for lang, mp3, notes in
         (("en", args.en, args.en_notes), ("es", args.es, args.es_notes)) if mp3]
for lang, mp3, notes in pairs:
    if not notes or not os.path.exists(mp3) or not os.path.exists(notes):
        sys.exit(f"missing file for {lang}: {mp3} / {notes}")

now = dt.datetime.now(dt.timezone.utc)
keep_days = int(CFG.get("audio_retention_days", 84))
for lang, mp3, notes in pairs:
    show = CFG["shows"][lang]
    slug = show["slug"]
    title, body = read_notes(notes)
    guid = f"{slug}-{args.date}" + ("-trailer" if args.kind == "trailer" else "")
    ep = {
        "guid": guid,
        "title": title,
        "notes": body,
        "kind": args.kind,
        "url": f"{SITE}/{slug}/audio/{os.path.basename(mp3)}",
        "bytes": os.path.getsize(mp3),
        "seconds": duration_seconds(mp3),
        "published": now.isoformat(timespec="seconds"),
        "pub_date": email.utils.format_datetime(now, usegmt=True),
    }
    audio_dir = os.path.join(REPO, slug, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    shutil.copyfile(mp3, os.path.join(audio_dir, os.path.basename(mp3)))
    eps = [e for e in load_episodes(slug) if e["guid"] != guid]
    eps.append(ep)
    # Retention: keep trailers, drop full episodes older than the window, with their audio.
    cutoff = now - dt.timedelta(days=keep_days)
    kept = []
    for e in eps:
        if e["kind"] != "trailer" and dt.datetime.fromisoformat(e["published"]) < cutoff:
            old = os.path.join(audio_dir, e["url"].rsplit("/", 1)[-1])
            if os.path.exists(old):
                os.remove(old)
            print(f"retired {e['guid']}")
        else:
            kept.append(e)
    eps = kept
    save_episodes(slug, eps)
    build_feed(show, eps)
    build_page(show, eps)
    print(f"{show['title']}: {title} ({ep['seconds']} s) -> {ep['url']}")

git_publish(f"Publish episodes {args.date}" + (" (trailer)" if args.kind == "trailer" else ""))
print("Feeds:", ", ".join(f"{SITE}/{s['slug']}/feed.xml" for s in CFG["shows"].values()))
