#!/usr/bin/env python3
"""Static site generator for yauhen.org.

    python build.py                 # build into _site/
    python build.py --serve         # build, serve on :8000 and rebuild on changes
    python build.py --strict        # fail on broken internal links (used in CI)

The path of every Markdown file in content/ is its URL
(content/releases/virgin-mind/index.md -> /releases/virgin-mind/), so the old
WordPress URLs stay the same. Archives keep WordPress pagination:
/category/<path>/page/<n>/, /tag/<slug>/page/<n>/, /page/<n>/ (home matrix).
Every internal link is written as a relative link, so the site works on any domain.
"""
from __future__ import annotations

import argparse
import colorsys
import datetime as dt
import hashlib
import html
import http.server
import json
import math
import os
import posixpath
import re
import shutil
import sys
import threading
import time
import unicodedata
import urllib.parse
from functools import partial
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

try:  # covers: thumbnails + colour themes (optional: the site still builds without Pillow)
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
DATA = ROOT / "data"
CACHE = ROOT / ".cache"
SITE_CONFIG = ROOT / "site.yaml"

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
THUMB_WIDTHS = (360, 720)


# --------------------------------------------------------------------------- helpers

def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower().replace("&", "")).strip("-")


def parse_date(value) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def date_long(d) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}" if d else ""


def strip_tags(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))).strip()


def truncate(text: str, length: int) -> str:
    text = text or ""
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0].rstrip(",.;:—–-") + "…"


def relative_url(target: str, from_dir: str, index_links: bool = False) -> str:
    """Site-absolute URL (/a/b/) -> relative to from_dir (/x/y/)."""
    path, sep, frag = target.partition("#")
    path, qsep, query = path.partition("?")
    suffix = (qsep + query if qsep else "") + (sep + frag if sep else "")
    if not path:
        return target
    path = urllib.parse.unquote(path)
    is_dir = path.endswith("/")
    rel = posixpath.relpath(path, from_dir)
    rel = "./" if rel == "." else (rel + "/" if is_dir else rel)
    if index_links and rel.endswith("/"):
        rel += "index.html"
    return urllib.parse.quote(rel, safe="/.-_~!$&'()*+,;=:@%") + suffix


def vt_name(url: str) -> str:
    """Stable CSS view-transition-name for a page's cover (ASCII only)."""
    return "c" + hashlib.md5(url.encode()).hexdigest()[:10]


# --------------------------------------------------------------------------- model

class Page:
    """One Markdown file from content/."""

    def __init__(self, source: Path, meta: dict, body: str):
        self.source, self.meta, self.body_md = source, meta, body
        parts = list(source.relative_to(CONTENT).with_suffix("").parts)
        if parts[-1] == "index":
            parts = parts[:-1]
        self.url = "/" + "/".join(parts) + ("/" if parts else "")
        self.title = str(meta.get("title", parts[-1] if parts else "Home"))
        self.type = meta.get("type", "page")
        self.date = parse_date(meta.get("date"))
        self.updated = parse_date(meta.get("updated"))
        self.tags = [str(t) for t in meta.get("tags", []) or []]
        self.categories = [str(c).strip("/") for c in meta.get("categories", []) or []]
        self.lang = meta.get("lang")
        self.draft = bool(meta.get("draft", False))
        self.release = meta.get("release")              # Album / EP / Single
        self.artist = meta.get("artist")                # other artist (e.g. LullaSoono)
        self.listen = meta.get("listen") or []
        self.cover = self.resolve(meta.get("cover"))
        self.cover_alt = meta.get("cover_alt")
        self.cover_inherited = False
        self.summary = str(meta.get("summary", "") or "")
        self.description = str(meta.get("description", "") or self.summary)
        self.seo_title = str(meta.get("seo_title", "") or "")
        self.content, self.toc, self.text, self.lyrics = Markup(""), [], "", ""
        self.album_page = None
        self.track_pages: list[Page] = []
        self.track_no = None
        self.anthology_page = None
        self.release_pages: list[Page] = []
        self.color = None
        self.thumbs: dict[int, str] = {}
        self.vt = vt_name(self.url)

    def resolve(self, ref):
        if not ref:
            return None
        ref = str(ref)
        if re.match(r"^[a-z]+:|^/", ref):
            return ref
        return posixpath.normpath(posixpath.join(self.url, ref))

    @property
    def year(self):
        return self.date.year if self.date else None

    @property
    def label(self) -> str:
        """Short type label for cards and eyebrows."""
        if self.type == "album":
            return self.release or "Album"
        return {"song": "Song", "anthology": "Anthology", "post": "Blog"}.get(self.type, "")

    def __getattr__(self, item):
        meta = self.__dict__.get("meta", {})
        if item in meta:
            return meta[item]
        raise AttributeError(item)

    def __repr__(self):
        return f"<Page {self.url}>"


def load_pages(include_drafts: bool) -> list[Page]:
    pages = []
    for path in sorted(CONTENT.rglob("*.md")):
        raw = path.read_text(encoding="utf-8")
        m = FRONT_MATTER.match(raw)
        meta = (yaml.safe_load(m.group(1)) if m else {}) or {}
        page = Page(path, meta, raw[m.end():] if m else raw)
        if page.draft and not include_drafts:
            continue
        pages.append(page)
    return pages


IMG_RE = re.compile(r"<img\b[^>]*>")
ALT_TOKEN_RE = re.compile(r"<h[23][^>]*>(.*?)</h[23]>|<figure\b.*?</figure>|<img\b[^>]*>", re.S)


def fill_alt_text(out: str, title: str) -> str:
    """Every content image gets an alt: its caption, else '<page> – <section heading>'."""
    state = {"heading": ""}

    def fix(tag: str, caption: str = "") -> str:
        if re.search(r'\salt="[^"]+"', tag):
            return tag
        h = state["heading"]
        alt = caption or (f"{title} – {h}" if h and h.lower() != title.lower() else title)
        alt = html.escape(alt, quote=True)
        if re.search(r'\salt=""', tag):
            return re.sub(r'\salt=""', f' alt="{alt}"', tag, count=1)
        return tag.replace("<img ", f'<img alt="{alt}" ', 1)

    def token(m: re.Match) -> str:
        text = m.group(0)
        if m.group(1) is not None:
            state["heading"] = strip_tags(m.group(1))
            return text
        if text.startswith("<figure"):
            cap = re.search(r"<figcaption>(.*?)</figcaption>", text, re.S)
            return IMG_RE.sub(lambda im: fix(im.group(0), strip_tags(cap.group(1)) if cap else ""), text)
        return fix(text)

    return ALT_TOKEN_RE.sub(token, out)


def render_markdown(page: Page):
    md = markdown.Markdown(extensions=["extra", "sane_lists", "toc"],
                           extension_configs={"toc": {"toc_depth": "2-3"}}, output_format="html")
    out = md.convert(page.body_md)
    out = re.sub(r"<img (?![^>]*\bloading=)", '<img loading="lazy" decoding="async" ', out)
    out = fill_alt_text(out, page.title)
    out = re.sub(r'<a href="(https?://[^"]+)"(?![^>]*\btarget=)', r'<a href="\1" target="_blank" rel="noopener"', out)
    out = out.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    out = out.replace('<pre class="lyrics">', '<pre class="lyrics" id="lyrics">', 1)
    page.content = Markup(out)
    page.toc = md.toc_tokens
    page.lyrics = "\n\n".join(html.unescape(x).strip() for x in re.findall(r'<pre class="lyrics"[^>]*>(.*?)</pre>', out, re.S))
    page.text = strip_tags(out)
    page.words = len(page.text.split())
    page.reading_time = max(1, round(page.words / 220))
    if not page.summary:
        page.summary = truncate(page.text, 200)
    desc = page.meta.get("description") or page.summary
    if page.type in ("song", "album") and len(desc) < 90:   # short first paragraphs: say what the page is
        what = "Song with lyrics" if page.type == "song" else (page.release or "Album")
        desc = f"{desc.rstrip('.…')}. {what} by {page.artist or 'Yauhen Panimatchanka'}."
    page.description = truncate(desc, 158)


def markdown_inline(text: str) -> Markup:
    out = markdown.markdown(str(text or "").strip(), extensions=["nl2br"])
    return Markup(re.sub(r"^<p>|</p>$", "", out.strip()))


# --------------------------------------------------------------------------- covers: colours + thumbnails

class Media:
    """Dominant colour of every cover (page theme) and WebP thumbnails for grids. Cached in .cache/."""

    def __init__(self):
        CACHE.mkdir(exist_ok=True)
        (CACHE / "thumbs").mkdir(exist_ok=True)
        self.index_file = CACHE / "colors.json"
        try:
            self.colors = json.loads(self.index_file.read_text())
        except Exception:
            self.colors = {}

    @staticmethod
    def key(path: Path) -> str:
        st = path.stat()
        return f"{path.relative_to(ROOT)}:{st.st_size}:{int(st.st_mtime)}"

    def color(self, path: Path) -> str | None:
        if not Image or not path.exists():
            return None
        k = self.key(path)
        if k not in self.colors:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((96, 96))
                q = im.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
                pal = q.getpalette()
                best, best_score = (228, 184, 14), -1.0
                for count, idx in q.getcolors():
                    r, g, b = pal[idx * 3: idx * 3 + 3]
                    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
                    if l < 0.1 or l > 0.93:
                        continue
                    score = count * (0.25 + s) * (1 - abs(l - 0.5))
                    if score > best_score:
                        best, best_score = (r, g, b), score
            self.colors[k] = "#%02x%02x%02x" % best
        return self.colors[k]

    def thumb(self, path: Path, width: int, out_file: Path) -> bool:
        if not Image or not path.exists():
            return False
        cached = CACHE / "thumbs" / (hashlib.md5(self.key(path).encode()).hexdigest() + f"-{width}.webp")
        if not cached.exists():
            with Image.open(path) as im:
                im = im.convert("RGB")
                if im.width > width:
                    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
                im.save(cached, "WEBP", quality=80, method=5)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, out_file)
        return True

    def save(self):
        self.index_file.write_text(json.dumps(self.colors, indent=0, ensure_ascii=False))


def theme(hex_color: str | None) -> dict:
    """Readable accent + tints derived from a cover colour (for the dark design)."""
    if not hex_color:
        return {}
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    def mk(l2, s2):
        rr, gg, bb = colorsys.hls_to_rgb(h, l2, s2)
        return "#%02x%02x%02x" % (round(rr * 255), round(gg * 255), round(bb * 255))
    return {"accent": mk(min(max(l, 0.58), 0.72), min(max(s, 0.45), 0.9)),
            "glow": mk(0.32, min(max(s, 0.35), 0.85)),
            "deep": mk(0.09, min(s, 0.6))}


# --------------------------------------------------------------------------- site

class Site:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out).resolve()
        self.site_url = (args.site_url or os.environ.get("SITE_URL", "")).rstrip("/")
        self.config = yaml.safe_load(SITE_CONFIG.read_text(encoding="utf-8"))
        ads = self.config.get("adsense") or {}
        ads["slots"] = {n: ({"id": str(v.get("id") or ""), "format": v.get("format") or "display"} if isinstance(v, dict)
                            else {"id": str(v or ""), "format": "display"}) for n, v in (ads.get("slots") or {}).items()}
        self.categories_def = yaml.safe_load((DATA / "categories.yaml").read_text(encoding="utf-8")) or []
        self.pages = load_pages(args.drafts)
        self.by_url = {p.url: p for p in self.pages}
        self.errors: list[str] = []
        self.media = Media()
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "xml"]),
                               trim_blocks=True, lstrip_blocks=True)
        self.env.filters.update(date_long=date_long, slugify=slugify, truncate_text=truncate,
                                markdown_inline=markdown_inline, theme=theme,
                                tojson_rel=lambda v: Markup(json.dumps(v, ensure_ascii=False)))
        self.env.globals.update(site=self.config, build_year=dt.date.today().year, dev=bool(args.serve),
                                icon=self.icon, abs_url=self.abs_url, site_url=self.site_url, platform=self.platform)

    # ---- helpers for templates

    def icon(self, name: str, cls: str = "icon") -> Markup:
        path = TEMPLATES / "icons" / f"{name}.svg"
        if not path.exists():
            path = TEMPLATES / "icons" / "music.svg"
        svg = path.read_text(encoding="utf-8").strip()
        return Markup(svg.replace("<svg ", f'<svg class="{cls}" aria-hidden="true" focusable="false" ', 1))

    def platform(self, key: str) -> dict:
        return (self.config.get("platforms") or {}).get(key, {"label": key.replace("_", " ").title(), "icon": "music"})

    def abs_url(self, url: str) -> str:
        if not self.site_url or not url:
            return ""
        if re.match(r"^[a-z]+:", url):
            return url
        return self.site_url + urllib.parse.quote(url, safe="/")

    # ---- relations between releases, songs and anthologies

    def link_relations(self):
        get = lambda u: self.by_url.get(u) if u else None
        for p in self.pages:
            p.album_page = get(p.meta.get("album"))
            p.track_pages = [t for t in (get(u) for u in p.meta.get("tracks") or []) if t]
            p.anthology_page = get(p.meta.get("anthology"))
            p.release_pages = [r for r in (get(u) for u in p.meta.get("releases") or []) if r]
        for album in self.pages:
            for i, t in enumerate(album.track_pages, 1):
                t.album_page = t.album_page or album
                if t.album_page is album:
                    t.track_no = i
        for p in self.pages:
            if not p.cover and p.album_page and p.album_page.cover:
                p.cover, p.cover_inherited = p.album_page.cover, True
            if not p.anthology_page and p.album_page and p.album_page.anthology_page:
                p.anthology_page = p.album_page.anthology_page

    def process_covers(self):
        """Thumbnails + colour theme for every cover (inherited covers reuse the album's)."""
        done: dict[str, tuple] = {}
        for p in self.pages:
            if not p.cover or p.cover.startswith(("http:", "https:")):
                continue
            if p.cover not in done:
                src = CONTENT / urllib.parse.unquote(p.cover).lstrip("/")
                if not src.exists():
                    src = STATIC / urllib.parse.unquote(p.cover).lstrip("/")
                thumbs = {}
                for w in THUMB_WIDTHS:
                    stem, _ = posixpath.splitext(p.cover)
                    rel = f"{stem}.w{w}.webp"
                    if self.media.thumb(src, w, self.out / urllib.parse.unquote(rel).lstrip("/")):
                        thumbs[w] = rel
                done[p.cover] = (thumbs, self.media.color(src))
            p.thumbs, p.color = done[p.cover]
            if p.meta.get("color"):
                p.color = p.meta["color"]

    # ---- taxonomies

    def build_taxonomies(self):
        tags: dict[str, dict] = {}
        for p in self.pages:
            for name in p.tags:
                s = slugify(name)
                if s:
                    t = tags.setdefault(s, {"slug": s, "name": name, "url": f"/tag/{s}/", "pages": []})
                    t["pages"].append(p)
        for t in tags.values():
            t["count"] = len(t["pages"])
            t["pages"].sort(key=lambda p: p.date or dt.date.min, reverse=True)
        self.tags = dict(sorted(tags.items(), key=lambda kv: kv[1]["name"].lower()))
        for p in self.pages:
            p.tag_items = [self.tags[slugify(n)] for n in p.tags if slugify(n) in self.tags]
        cats = {}
        for c in self.categories_def:
            path = c["path"].strip("/")
            members = sorted([p for p in self.pages if any(pc == path or pc.startswith(path + "/") for pc in p.categories)],
                             key=lambda p: p.date or dt.date.min, reverse=True)
            cats[path] = {**c, "path": path, "url": f"/category/{path}/", "pages": members}
        self.categories = cats
        for p in self.pages:
            for c in p.categories:
                if c not in cats:
                    self.errors.append(f"{p.source.relative_to(ROOT)}: unknown category '{c}' (add it to data/categories.yaml)")
            p.category_items = [cats[c] for c in p.categories if c in cats]

    def collections(self) -> dict:
        newest = lambda items: sorted(items, key=lambda p: p.date or dt.date.min, reverse=True)
        by_type = lambda *t: newest([p for p in self.pages if p.type in t])
        albums = by_type("album")
        matrix_cat = self.config.get("home", {}).get("matrix_category", "releases/music")
        songs = sorted(by_type("song"), key=lambda p: p.title.lower())
        return {
            "albums": albums,
            "own_albums": [a for a in albums if not a.artist],
            "produced": [a for a in albums if a.artist],
            "matrix": [p for p in self.categories.get(matrix_cat, {}).get("pages", []) if p.type == "album"] or albums,
            "songs": songs,
            "anthologies": by_type("anthology"),
            "posts": by_type("post"),
            "featured": sorted([p for p in self.pages if p.meta.get("featured")], key=lambda p: p.meta["featured"]),
            "tags": self.tags,
            "categories": self.categories,
            "page_by_url": self.by_url,
            "shuffle": [s.url for s in songs] + [a.url for a in albums if not a.track_pages],
        }

    # ---- breadcrumbs and labels

    def breadcrumbs(self, page: Page) -> list[dict]:
        trail = [{"title": "Home", "url": "/"}]
        if page.type in ("album", "song"):
            trail.append({"title": "Music", "url": "/music/"})
            if page.type == "song" and page.album_page:
                trail.append({"title": page.album_page.title, "url": page.album_page.url})
        elif page.type == "anthology":
            trail.append({"title": "Anthologies", "url": "/category/anthologies/"})
        elif page.type == "post":
            cat = page.category_items[0] if page.category_items else None
            if cat:
                trail.append({"title": cat["name"], "url": cat["url"]})
        return trail

    def siblings(self, page: Page):
        """Previous/next neighbour: tracks inside an album, otherwise same-type items by date."""
        if page.type == "song" and page.album_page and page in page.album_page.track_pages:
            seq = page.album_page.track_pages
        elif page.type in ("album", "anthology", "post"):
            seq = sorted([p for p in self.pages if p.type == page.type and bool(p.artist) == bool(page.artist)],
                         key=lambda p: (p.date or dt.date.min, p.title))
        else:
            return None, None
        i = seq.index(page)
        return (seq[i - 1] if i > 0 else None), (seq[i + 1] if i + 1 < len(seq) else None)

    # ---- rendering

    def render(self, template: str, url: str, **ctx) -> str:
        page = ctx.get("page")
        get = (lambda k, d="": page.get(k, d)) if isinstance(page, dict) else (lambda k, d="": getattr(page, k, d))
        ctx.setdefault("head_title", self.head_title(get("title"), get("seo_title")))
        ctx.setdefault("jsonld", self.jsonld(url, ctx))
        ctx.setdefault("canonical", url)
        html_out = self.env.get_template(template).render(url=url, **self.coll, **ctx)
        return self.relativize(html_out, url)

    def head_title(self, title: str, seo_title: str = "") -> str:
        t = (seo_title or title or "").strip()
        suffix = self.config.get("title_suffix", "")
        return t + suffix if suffix and len(t + suffix) <= 60 else t

    ATTR_RE = re.compile(r'(\s(?:href|src|action|poster|data-[\w-]+)=)(["\'])(/(?!/)[^"\']*)\2')
    SRCSET_RE = re.compile(r'(\ssrcset=)(["\'])([^"\']*)\2')
    CSS_URL_RE = re.compile(r'url\((["\']?)(/(?!/)[^)"\']*)\1\)')

    def relativize(self, text: str, url: str) -> str:
        from_dir = url if url.endswith("/") else posixpath.dirname(url) + "/"
        rel = partial(relative_url, from_dir=from_dir, index_links=self.args.index_links)
        text = self.ATTR_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{rel(m.group(3))}{m.group(2)}", text)

        def srcset(m):
            parts = []
            for cand in m.group(3).split(","):
                bits = cand.strip().split()
                if bits and bits[0].startswith("/") and not bits[0].startswith("//"):
                    bits[0] = rel(bits[0])
                parts.append(" ".join(bits))
            return f"{m.group(1)}{m.group(2)}{', '.join(parts)}{m.group(2)}"

        text = self.SRCSET_RE.sub(srcset, text)
        return self.CSS_URL_RE.sub(lambda m: f"url({m.group(1)}{rel(m.group(2))}{m.group(1)})", text)

    def write(self, url: str, content: str):
        rel = urllib.parse.unquote(url).lstrip("/")
        target = self.out / rel / "index.html" if url.endswith("/") else self.out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    @staticmethod
    def pages_of(items: list, per_page: int, base: str):
        """WordPress-style pagination: base, base/page/2/, base/page/3/ ..."""
        total = max(1, math.ceil(len(items) / per_page))
        urls = [base if n == 1 else f"{base}page/{n}/" for n in range(1, total + 1)]
        for n in range(1, total + 1):
            yield {"n": n, "total": total, "url": urls[n - 1], "items": items[(n - 1) * per_page: n * per_page],
                   "prev": urls[n - 2] if n > 1 else None, "next": urls[n] if n < total else None,
                   "links": [{"n": i + 1, "url": u} for i, u in enumerate(urls)]}

    def build(self):
        start = time.time()
        final = self.final_out = self.out
        self.out = final.parent / f".{final.name}.tmp-{os.getpid()}-{time.time_ns()}"
        self.out.mkdir(parents=True)
        try:
            self._build_into_out(start)
        except BaseException:
            shutil.rmtree(self.out, ignore_errors=True)
            self.out = final
            raise
        tmp, old = self.out, final.parent / f".{final.name}.old-{os.getpid()}-{time.time_ns()}"
        for _ in range(5):
            try:
                if final.exists():
                    os.rename(final, old)
                os.rename(tmp, final)
                break
            except OSError:
                time.sleep(0.2)
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
        self.out = final

    def _build_into_out(self, start: float):
        shutil.copytree(STATIC, self.out, dirs_exist_ok=True)
        for f in CONTENT.rglob("*"):
            if f.is_file() and f.suffix != ".md" and not f.name.startswith("."):
                dest = self.out / f.relative_to(CONTENT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        for p in self.pages:
            render_markdown(p)
        self.link_relations()
        self.build_taxonomies()
        self.process_covers()
        self.media.save()
        self.coll = self.collections()
        urls: list[tuple[str, dt.date | None]] = []

        templates = {"home": "home.html", "album": "album.html", "song": "song.html", "anthology": "anthology.html",
                     "post": "post.html", "music": "music.html"}
        per_home = int(self.config.get("home", {}).get("per_page", 12))
        for p in self.pages:
            tpl = p.meta.get("template") or templates.get(p.type, "page.html")
            prev, nxt = self.siblings(p)
            ctx = {"page": p, "crumbs": self.breadcrumbs(p), "prev_page": prev, "next_page": nxt}
            if p.type == "home":
                for pg in self.pages_of(self.coll["matrix"], per_home, "/"):
                    extra = {} if pg["n"] == 1 else {"head_title": f"{self.config.get('artist')} – Music, page {pg['n']}"}
                    self.write(pg["url"], self.render(tpl, pg["url"], **ctx, pager=pg, **extra))
                    urls.append((pg["url"], None))
                continue
            self.write(p.url, self.render(tpl, p.url, **ctx))
            urls.append((p.url, p.updated or p.date))

        per = int(self.config.get("per_page", 10))
        content_urls = set(self.by_url)
        for c in self.categories.values():
            if not c["pages"]:
                continue
            parts = c["path"].split("/")
            crumbs = [{"title": "Home", "url": "/"}] + [
                {"title": self.categories[a]["name"], "url": self.categories[a]["url"]}
                for a in ("/".join(parts[:i]) for i in range(1, len(parts))) if a in self.categories]
            subcats = [s for s in self.categories.values() if s["pages"] and s["path"].startswith(c["path"] + "/")
                       and s["path"].count("/") == c["path"].count("/") + 1]
            trail = " – ".join([c["name"]] + [x["title"] for x in reversed(crumbs[1:])])
            for base, canonical_base in ((c["url"], c["url"]), (f"/{c['path']}/", c["url"])):
                if base != c["url"] and base in content_urls:
                    continue  # a real page lives there (e.g. /music/)
                for pg in self.pages_of(c["pages"], per, base):
                    canon = canonical_base if pg["n"] == 1 else f"{canonical_base}page/{pg['n']}/"
                    fake = {"title": c["name"] + (f" – Page {pg['n']}" if pg["n"] > 1 else ""),
                            "seo_title": trail + (f" – Page {pg['n']}" if pg["n"] > 1 else ""),
                            "description": c.get("description") or self.list_description(c["name"], c["pages"])}
                    self.write(pg["url"], self.render("list.html", pg["url"], page=fake, pager=pg, heading=c["name"],
                                                      eyebrow="Category", crumbs=crumbs, subcats=subcats,
                                                      lead=c.get("description", ""), canonical=canon))
                    if base == c["url"]:
                        urls.append((pg["url"], None))
        for t in self.tags.values():
            for pg in self.pages_of(t["pages"], per, t["url"]):
                fake = {"title": t["name"] + (f" – Page {pg['n']}" if pg["n"] > 1 else ""),
                        "seo_title": f"{t['name']} – songs, albums and notes" + (f" – Page {pg['n']}" if pg["n"] > 1 else ""),
                        "description": self.list_description(f"Music and notes about {t['name']}", t["pages"])}
                self.write(pg["url"], self.render("list.html", pg["url"], page=fake, pager=pg, heading=t["name"],
                                                  eyebrow="Tag", crumbs=[{"title": "Home", "url": "/"}], subcats=[], lead=""))
                urls.append((pg["url"], None))
        # old duplicate tag URLs (e.g. /tag/electro-pop-2/) keep working, canonical -> the merged tag
        for alias, target in (self.config.get("tag_aliases") or {}).items():
            t = self.tags.get(target)
            if not t:
                self.errors.append(f"site.yaml tag_aliases: unknown tag '{target}'")
                continue
            for pg in self.pages_of(t["pages"], per, f"/tag/{alias}/"):
                canon = t["url"] if pg["n"] == 1 else f"{t['url']}page/{pg['n']}/"
                fake = {"title": t["name"], "seo_title": f"{t['name']} – songs, albums and notes",
                        "description": self.list_description(f"Music and notes about {t['name']}", t["pages"])}
                self.write(pg["url"], self.render("list.html", pg["url"], page=fake, pager=pg, heading=t["name"],
                                                  eyebrow="Tag", crumbs=[{"title": "Home", "url": "/"}], subcats=[],
                                                  lead="", canonical=canon))
        everything = sorted([p for p in self.pages if p.type in ("album", "song", "anthology", "post")],
                            key=lambda p: p.date or dt.date.min, reverse=True)
        extra = [("/author/" + self.config.get("author_slug", "yauhen") + "/", self.config.get("artist"), "Author", everything),
                 ("/type/audio/", "Audio", "Format", [p for p in everything if p.meta.get("format") == "audio"])]
        for base, name, eyebrow, items in extra:
            for pg in self.pages_of(items, per, base):
                fake = {"title": name + (f" – Page {pg['n']}" if pg["n"] > 1 else ""),
                        "description": self.list_description(name, items)}
                self.write(pg["url"], self.render("list.html", pg["url"], page=fake, pager=pg, heading=name,
                                                  eyebrow=eyebrow, crumbs=[{"title": "Home", "url": "/"}], subcats=[], lead=""))

        self.write("/search/", self.render("search.html", "/search/", page={"title": "Search", "noindex": True}))
        self.write("/404.html", self.render("404.html", "/", page={"title": "Page not found", "noindex": True}))
        self.write_search_index()
        self.write_feed()
        self.write_seo(urls)
        self.check_links()
        n_html = sum(1 for _ in self.out.rglob("*.html"))
        where = self.final_out.relative_to(ROOT) if self.final_out.is_relative_to(ROOT) else self.final_out
        print(f"Built {n_html} HTML files into {where} in {time.time() - start:.2f}s"
              + (f" (site URL {self.site_url})" if self.site_url else " (no SITE_URL: canonical/sitemap skipped)"))
        for e in self.errors:
            print("  ERROR:", e)

    @staticmethod
    def list_description(intro: str, pages: list) -> str:
        names = [p.title for p in pages]
        shown = ", ".join(names[:4]) + (" and more" if len(names) > 4 else "")
        return truncate(f"{intro} by Yauhen Panimatchanka: {shown}." if names else f"{intro}.", 158)

    # ---- structured data

    def jsonld(self, url: str, ctx: dict) -> Markup:
        if not self.site_url:
            return Markup("")
        su, cfg = self.site_url, self.config
        A = lambda u: self.abs_url(u)
        person_id = su + "/#artist"
        artist = {"@type": ["Person", "MusicGroup"], "@id": person_id, "name": cfg.get("artist"), "url": su + "/",
                  "sameAs": [s["url"] for s in cfg.get("social", []) if s["url"].startswith("http")]}
        page = ctx.get("page")
        is_page = isinstance(page, Page)
        graph = []

        def by(p):
            if p.artist:
                return {"@type": "MusicGroup", "name": p.artist, "producer": {"@id": person_id}}
            return {"@id": person_id}

        def album_ld(p, full=True):
            kind = {"Single": "SingleRelease", "EP": "EPRelease"}.get(p.release, "AlbumRelease")
            d = {"@type": "MusicAlbum", "name": p.title, "url": A(p.url), "byArtist": by(p),
                 "albumReleaseType": f"https://schema.org/{kind}"}
            if full:
                d["description"] = p.description
                if p.date:
                    d["datePublished"] = p.date.isoformat()
                if p.cover:
                    d["image"] = A(p.cover)
                if p.track_pages:
                    d["numTracks"] = len(p.track_pages)
                    d["track"] = {"@type": "ItemList", "numberOfItems": len(p.track_pages), "itemListElement": [
                        {"@type": "ListItem", "position": i, "item": {"@type": "MusicRecording", "name": t.title, "url": A(t.url)}}
                        for i, t in enumerate(p.track_pages, 1)]}
                if p.listen:
                    d["sameAs"] = [l["url"] for l in p.listen]
                if p.tags:
                    d["keywords"] = ", ".join(p.tags)
            return d

        if is_page and page.type == "home":
            artist.update({"image": A(cfg.get("portrait")), "description": cfg.get("description"),
                           "jobTitle": "Composer, songwriter and music producer"})
            graph += [artist, {"@type": "WebSite", "@id": su + "/#website", "url": su + "/", "name": cfg.get("artist"),
                               "potentialAction": {"@type": "SearchAction",
                                                   "target": {"@type": "EntryPoint", "urlTemplate": su + "/search/?q={search_term_string}"},
                                                   "query-input": "required name=search_term_string"}}]
        crumbs = ctx.get("crumbs") or []
        if crumbs and not (is_page and page.type == "home"):
            name = ctx.get("heading") or (page.title if is_page else (page or {}).get("title"))
            graph.append({"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": c["title"], "item": A(c["url"])}
                for i, c in enumerate(crumbs + [{"title": name, "url": url}])]})
        if is_page and page.type == "album":
            graph.append(album_ld(page))
        elif is_page and page.type == "song":
            rec = {"@type": "MusicRecording", "name": page.title, "url": A(page.url), "byArtist": by(page),
                   "description": page.description}
            if page.date:
                rec["datePublished"] = page.date.isoformat()
            if page.cover:
                rec["image"] = A(page.cover)
            if page.album_page:
                rec["inAlbum"] = album_ld(page.album_page, full=False)
            if page.listen:
                rec["sameAs"] = [l["url"] for l in page.listen]
            if page.lyrics:
                rec["recordingOf"] = {"@type": "MusicComposition", "name": page.title,
                                      "composer": {"@id": person_id} if not page.artist else {"@type": "Person", "name": cfg.get("artist")},
                                      "lyrics": {"@type": "CreativeWork", "text": page.lyrics[:5000]}}
            graph.append(rec)
        elif is_page and page.type == "anthology":
            graph.append({"@type": "CreativeWorkSeries", "name": page.title, "url": A(page.url), "description": page.description,
                          "image": A(page.cover) if page.cover else None, "author": {"@id": person_id},
                          "hasPart": [album_ld(r, full=False) for r in page.release_pages]})
        elif is_page and page.type == "post":
            art = {"@type": "BlogPosting", "headline": page.title[:110], "description": page.description, "url": A(page.url),
                   "mainEntityOfPage": A(page.url), "author": {"@id": person_id, "@type": "Person", "name": cfg.get("artist")}}
            if page.date:
                art["datePublished"] = page.date.isoformat()
                art["dateModified"] = (page.updated or page.date).isoformat()
            if page.cover:
                art["image"] = [A(page.cover)]
            graph.append(art)
        elif not is_page or page.type not in ("home",):
            graph.append({"@type": "CollectionPage" if not is_page else "WebPage", "url": A(url),
                          "name": ctx.get("heading") or (page.title if is_page else (page or {}).get("title"))})
        data = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
        return Markup(data.replace("</", "<\\/"))

    # ---- extra outputs

    def write_search_index(self):
        docs = [{"t": p.title, "u": urllib.parse.unquote(p.url.lstrip("/")), "s": p.summary, "k": p.tags, "y": p.year,
                 "l": p.label, "c": p.thumbs.get(360) and urllib.parse.unquote(p.thumbs[360].lstrip("/")),
                 "x": truncate(p.text, 6000)}
                for p in self.pages if p.type not in ("home",) and not p.meta.get("noindex")]
        (self.out / "search-index.json").write_text(json.dumps(docs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    def write_feed(self):
        if not self.site_url:
            return
        items = sorted([p for p in self.pages if p.type in ("album", "song", "anthology", "post")],
                       key=lambda p: p.date or dt.date.min, reverse=True)[:30]
        rows = []
        for p in items:
            d = dt.datetime.combine(p.date or dt.date.today(), dt.time(12)).strftime("%a, %d %b %Y %H:%M:%S +0000")
            rows.append(f"<item><title>{html.escape(p.title)}</title><link>{self.abs_url(p.url)}</link>"
                        f"<guid>{self.abs_url(p.url)}</guid><pubDate>{d}</pubDate>"
                        f"<description>{html.escape(p.summary)}</description></item>")
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
               f"<title>{html.escape(self.config.get('artist', ''))}</title><link>{self.site_url}/</link>"
               f"<description>{html.escape(self.config.get('description', ''))}</description>"
               + "".join(rows) + "</channel></rss>\n")
        (self.out / "feed.xml").write_text(xml, encoding="utf-8")

    def write_seo(self, urls):
        robots = ["User-agent: *", "Allow: /", "Disallow: /search/"]
        if self.site_url:
            rows = []
            for url, lastmod in sorted(urls):
                lm = f"<lastmod>{lastmod.isoformat()}</lastmod>" if lastmod else ""
                rows.append(f"  <url><loc>{html.escape(self.abs_url(url))}</loc>{lm}</url>")
            (self.out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                                  '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                                  + "\n".join(rows) + "\n</urlset>\n", encoding="utf-8")
            robots.append(f"Sitemap: {self.site_url}/sitemap.xml")
        (self.out / "robots.txt").write_text("\n".join(robots) + "\n", encoding="utf-8")
        (self.out / ".nojekyll").write_text("", encoding="utf-8")

    LINK_RE = re.compile(r'\s(?:href|src|action|poster)=["\']([^"\']+)["\']')

    def check_links(self):
        own = [d.lower() for d in self.config.get("own_domains", [])]
        for f in self.out.rglob("*.html"):
            if f.name == "404.html" and f.parent == self.out:
                continue
            text = re.sub(r'<link rel="canonical"[^>]*>|<base\b[^>]*>', "", f.read_text(encoding="utf-8"))
            for link in self.LINK_RE.findall(text):
                link = html.unescape(link)
                low = link.lower()
                if re.match(r"^(mailto:|tel:|data:|javascript:|#)", low):
                    continue
                if re.match(r"^(https?:)?//", low):
                    host = re.sub(r"^(https?:)?//", "", low).split("/")[0].split(":")[0]
                    if host in own:
                        self.errors.append(f"{f.relative_to(self.out)}: absolute link to own domain: {link}")
                    continue
                if link.startswith("/"):
                    self.errors.append(f"{f.relative_to(self.out)}: root-absolute link left: {link}")
                    continue
                path = urllib.parse.unquote(link.split("#")[0].split("?")[0])
                if not path:
                    continue
                target = (f.parent / path).resolve()
                if target.is_dir():
                    target = target / "index.html"
                if not target.exists():
                    self.errors.append(f"{f.relative_to(self.out)}: broken link: {link}")


# --------------------------------------------------------------------------- CLI

def snapshot():
    files = [SITE_CONFIG, Path(__file__)]
    for d in (CONTENT, TEMPLATES, STATIC, DATA):
        files.extend(p for p in d.rglob("*") if p.is_file())
    return {str(p): p.stat().st_mtime for p in files}


def serve(args):
    out = Path(args.out).resolve()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_error(self, code, message=None, explain=None):
            if code == 404 and (out / "404.html").exists():
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write((out / "404.html").read_bytes())
                return
            super().send_error(code, message, explain)

    httpd = http.server.ThreadingHTTPServer((args.host, args.port), partial(Quiet, directory=str(out)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Serving {out} at http://localhost:{args.port}/ (Ctrl+C to stop)")
    last, me = snapshot(), str(Path(__file__))
    try:
        while True:
            time.sleep(0.7)
            now = snapshot()
            if now != last:
                if now.get(me) != last.get(me):
                    print("build.py changed, restarting…")
                    httpd.shutdown()
                    httpd.server_close()
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                last = now
                try:
                    Site(args).build()
                except Exception as exc:
                    print("Build failed:", exc)
    except KeyboardInterrupt:
        httpd.shutdown()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "_site"))
    ap.add_argument("--site-url", default="", help="public base URL (canonical links, sitemap, feed); defaults to $SITE_URL")
    ap.add_argument("--index-links", action="store_true", help="link to dir/index.html (for browsing via file://)")
    ap.add_argument("--drafts", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit with an error when link checks fail")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    site = Site(args)
    site.build()
    if site.errors and args.strict:
        sys.exit(1)
    if args.serve:
        serve(args)


if __name__ == "__main__":
    main()
