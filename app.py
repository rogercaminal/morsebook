from __future__ import annotations

import re
import sqlite3
import textwrap
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "morsebook.sqlite3"
for p in (DATA_DIR, RAW_DIR):
    p.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Morse Audiobook Player")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class ImportRequest(BaseModel):
    gutenberg_id: int = Field(..., ge=1)
    target_segment_seconds: int = Field(default=90, ge=30, le=600)
    wpm_for_split: int = Field(default=60, ge=5, le=80)
    title: str | None = None
    force_refresh: bool = False


class GutenbergLookup(BaseModel):
    gutenberg_id: int
    title: str
    authors: list[str] = []
    languages: list[str] = []
    url: str
    source: str


class CWParams(BaseModel):
    wpm: int = Field(default=20, ge=5, le=80)
    eff: int = Field(default=15, ge=0, le=80)
    freq: int = Field(default=700, ge=200, le=1200)
    volume: int = Field(default=70, ge=0, le=100)
    ews: int = Field(default=0, ge=0, le=20)
    real: bool = False


class ProfileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    params: CWParams


class StateUpdate(BaseModel):
    chapter_index: int | None = Field(default=None, ge=0)
    segment_index: int | None = Field(default=None, ge=0)
    char_offset: int | None = Field(default=None, ge=0)
    params: CWParams | None = None
    profile_name: str | None = None


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS books (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              gutenberg_id INTEGER UNIQUE NOT NULL,
              title TEXT NOT NULL,
              source TEXT NOT NULL,
              txt_url TEXT NOT NULL,
              imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS chapters (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
              chapter_index INTEGER NOT NULL,
              title TEXT NOT NULL,
              UNIQUE(book_id, chapter_index)
            );
            CREATE TABLE IF NOT EXISTS segments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
              chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
              chapter_index INTEGER NOT NULL,
              segment_index INTEGER NOT NULL,
              title TEXT NOT NULL,
              text TEXT NOT NULL,
              char_count INTEGER NOT NULL,
              UNIQUE(book_id, chapter_index, segment_index)
            );
            CREATE TABLE IF NOT EXISTS progress (
              book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
              chapter_index INTEGER NOT NULL DEFAULT 0,
              segment_index INTEGER NOT NULL DEFAULT 0,
              char_offset INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS segment_params (
              book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
              chapter_index INTEGER NOT NULL,
              segment_index INTEGER NOT NULL,
              wpm INTEGER NOT NULL,
              eff INTEGER NOT NULL,
              freq INTEGER NOT NULL,
              volume INTEGER NOT NULL,
              ews INTEGER NOT NULL,
              real INTEGER NOT NULL,
              profile_name TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(book_id, chapter_index, segment_index)
            );
            CREATE TABLE IF NOT EXISTS cw_profiles (
              name TEXT PRIMARY KEY,
              wpm INTEGER NOT NULL,
              eff INTEGER NOT NULL,
              freq INTEGER NOT NULL,
              volume INTEGER NOT NULL,
              ews INTEGER NOT NULL,
              real INTEGER NOT NULL
            );
            """
        )
        defaults = {
            "Beginner": CWParams(wpm=18, eff=10, freq=600, volume=50, ews=1, real=False),
            "HSC": CWParams(wpm=25, eff=0, freq=600, volume=50, ews=0, real=False),
            "VHSC": CWParams(wpm=40, eff=0, freq=600, volume=50, ews=0, real=False),
            "SHSC": CWParams(wpm=50, eff=0, freq=600, volume=50, ews=0, real=False),
            "EHSC": CWParams(wpm=60, eff=0, freq=600, volume=50, ews=0, real=False),
        }
        for name, p in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO cw_profiles VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, p.wpm, p.eff, p.freq, p.volume, p.ews, int(p.real)),
            )


init_db()


def row_params(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return CWParams().model_dump()
    return {
        "wpm": row["wpm"], "eff": row["eff"], "freq": row["freq"],
        "volume": row["volume"], "ews": row["ews"], "real": bool(row["real"]),
    }


def normalize_gutenberg_text(raw: str) -> tuple[str, dict[str, str]]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    meta: dict[str, str] = {}
    sample = raw[:9000]
    for key in ("Title", "Author", "Language"):
        m = re.search(rf"^{key}:\s*(.+)$", sample, flags=re.M | re.I)
        if m:
            meta[key.lower()] = m.group(1).strip()
    start = re.search(r"\*\*\*\s*START OF (?:THE|THIS)?\s*PROJECT GUTENBERG EBOOK.*?\*\*\*", raw, flags=re.I | re.S)
    if start:
        raw = raw[start.end():]
    end = re.search(r"\*\*\*\s*END OF (?:THE|THIS)?\s*PROJECT GUTENBERG EBOOK.*?\*\*\*", raw, flags=re.I | re.S)
    if end:
        raw = raw[:end.start()]
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n[ \t]+", "\n", raw)
    raw = re.sub(r"\n{4,}", "\n\n\n", raw)
    return raw.strip(), meta


def chapterize(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"(?m)^(CHAPTER\s+(?:[IVXLCDM]+|\d+|[A-Z]+)\b[^\n]*)$", re.I)
    matches = list(pattern.finditer(text))
    if not matches:
        return [("Book", text.strip())]
    chapters: list[tuple[str, str]] = []
    if matches[0].start() > 200:
        preface = text[: matches[0].start()].strip()
        if preface:
            chapters.append(("Front matter", preface))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            chapters.append((m.group(1).strip().title(), body))
    return chapters or [("Book", text.strip())]


def chars_for_seconds(seconds: int, wpm: int) -> int:
    return max(400, int(seconds * wpm * 5 / 60))


def split_segments(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    segments, cur = [], ""
    for para in paragraphs:
        if len(para) > max_chars:
            if cur:
                segments.append(cur.strip()); cur = ""
            segments.extend(c.strip() for c in textwrap.wrap(para, width=max_chars, break_long_words=False, replace_whitespace=False) if c.strip())
        elif cur and len(cur) + len(para) + 2 > max_chars:
            segments.append(cur.strip()); cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        segments.append(cur.strip())
    return segments or [text[:max_chars]]


def display_author_name(name: str) -> str:
    parts = [p.strip() for p in name.split(",", 1)]
    if len(parts) == 2 and all(parts):
        return f"{parts[1]} {parts[0]}"
    return name.strip()


async def fetch_gutenberg_txt(gid: int, force_refresh: bool = False) -> tuple[str, str]:
    cached = RAW_DIR / f"gutenberg_{gid}.txt"
    if cached.exists() and not force_refresh:
        return cached.read_text(encoding="utf-8"), f"cache:gutenberg_{gid}.txt"
    candidates = [
        f"https://www.gutenberg.org/cache/epub/{gid}/pg{gid}.txt",
        f"https://www.gutenberg.org/files/{gid}/{gid}-0.txt",
        f"https://www.gutenberg.org/files/{gid}/{gid}.txt",
    ]
    async with httpx.AsyncClient(follow_redirects=True, timeout=45, headers={"User-Agent": "morsebook/0.3"}) as client:
        last = ""
        for url in candidates:
            try:
                r = await client.get(url)
                if r.status_code < 400 and "html" not in r.headers.get("content-type", "").lower():
                    cached.write_text(r.text, encoding="utf-8")
                    return r.text, url
                last = f"{url}: HTTP {r.status_code} {r.headers.get('content-type','')}"
            except Exception as e:
                last = f"{url}: {e}"
    raise HTTPException(502, f"Could not fetch Gutenberg TXT for id {gid}. Last error: {last}")


async def fetch_gutenberg_metadata(gid: int) -> GutenbergLookup:
    rdf_url = f"https://www.gutenberg.org/cache/epub/{gid}/pg{gid}.rdf"
    book_url = f"https://www.gutenberg.org/ebooks/{gid}"
    ns = {
        "dcterms": "http://purl.org/dc/terms/",
        "pgterms": "http://www.gutenberg.org/2009/pgterms/",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers={"User-Agent": "morsebook/0.3"}) as client:
            r = await client.get(rdf_url)
        if r.status_code < 400:
            root = ET.fromstring(r.text)
            title = (root.findtext(".//dcterms:title", namespaces=ns) or "").strip()
            authors = [
                display_author_name(name.text)
                for name in root.findall(".//dcterms:creator/pgterms:agent/pgterms:name", namespaces=ns)
                if name.text and name.text.strip()
            ]
            languages = [
                value.text.strip()
                for value in root.findall(".//dcterms:language/rdf:Description/rdf:value", namespaces=ns)
                if value.text and value.text.strip()
            ]
            if title:
                return GutenbergLookup(
                    gutenberg_id=gid,
                    title=title,
                    authors=authors,
                    languages=languages,
                    url=book_url,
                    source=rdf_url,
                )
    except Exception:
        pass

    raw, source = await fetch_gutenberg_txt(gid)
    _, meta = normalize_gutenberg_text(raw)
    title = meta.get("title")
    if not title:
        raise HTTPException(404, f"Could not find metadata for Gutenberg id {gid}")
    authors = [meta["author"]] if meta.get("author") else []
    languages = [meta["language"]] if meta.get("language") else []
    return GutenbergLookup(
        gutenberg_id=gid,
        title=title,
        authors=authors,
        languages=languages,
        url=book_url,
        source=source,
    )


MORSE_UNITS = {
    "a":".-","b":"-...","c":"-.-.","d":"-..","e":".","f":"..-.","g":"--.","h":"....","i":"..","j":".---","k":"-.-","l":".-..","m":"--","n":"-.","o":"---","p":".--.","q":"--.-","r":".-.","s":"...","t":"-","u":"..-","v":"...-","w":".--","x":"-..-","y":"-.--","z":"--..",
    "0":"-----","1":".----","2":"..---","3":"...--","4":"....-","5":".....","6":"-....","7":"--...","8":"---..","9":"----.",
    ".":".-.-.-", ",":"--..--", "?":"..--..", "'":".----.", "!":"-.-.--", "/":"-..-.", "(":"-.--.", ")":"-.--.-", "&":".-...", ":":"---...", ";":"-.-.-.", "=":"-...-", "+":".-.-.", "-":"-....-", "_":"..--.-", '"':".-..-.", "$":"...-..-", "@":".--.-."
}


def timing_metadata(text: str, p: CWParams, max_marks: int = 2000) -> dict[str, Any]:
    # Approximate PARIS timing. Good for resume/bookmarks; jscw remains the audio source of truth.
    code_wpm = max(1, p.wpm)
    eff_wpm = p.eff if p.eff and p.eff > 0 else p.wpm
    dot = 1.2 / code_wpm
    gap_dot = dot
    char_gap = max(3 * dot, (60 / max(1, eff_wpm) - 31 * dot) / 19)  # rough Farnsworth correction
    word_gap = max(7 * dot, char_gap * 2 + (p.ews * dot))
    t = 0.0
    marks = []
    last_was_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not last_was_space:
                t += word_gap
            last_was_space = True
            continue
        last_was_space = False
        code = MORSE_UNITS.get(ch.lower())
        start = t
        if not code:
            t += char_gap
        else:
            for j, sym in enumerate(code):
                t += dot if sym == "." else 3 * dot
                if j < len(code) - 1:
                    t += gap_dot
            t += char_gap
        if len(marks) < max_marks:
            marks.append({"i": i, "ch": ch, "start": round(start, 3), "end": round(t, 3)})
    return {"duration": round(t, 3), "marks": marks, "truncated": len(text) > max_marks}


def book_or_404(conn, book_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Book not found")
    return row


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/profiles")
def profiles() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM cw_profiles ORDER BY CASE name WHEN 'Beginner' THEN 1 WHEN 'Normal' THEN 2 WHEN 'Contest' THEN 3 WHEN 'QRQ' THEN 4 ELSE 5 END, name").fetchall()
        return [{"name": r["name"], "params": row_params(r)} for r in rows]


@app.post("/api/profiles")
def upsert_profile(profile: ProfileIn) -> dict[str, Any]:
    p = profile.params
    with db() as conn:
        conn.execute("REPLACE INTO cw_profiles VALUES (?, ?, ?, ?, ?, ?, ?)", (profile.name, p.wpm, p.eff, p.freq, p.volume, p.ews, int(p.real)))
    return {"name": profile.name, "params": p.model_dump()}


@app.get("/api/gutenberg/{gutenberg_id}")
async def get_gutenberg_metadata(gutenberg_id: int) -> GutenbergLookup:
    if gutenberg_id < 1:
        raise HTTPException(422, "Gutenberg ID must be a positive integer")
    return await fetch_gutenberg_metadata(gutenberg_id)


@app.get("/api/books")
def list_books() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("""
            SELECT b.*, COUNT(DISTINCT c.id) chapters, COUNT(s.id) segments
            FROM books b
            LEFT JOIN chapters c ON c.book_id=b.id
            LEFT JOIN segments s ON s.book_id=b.id
            GROUP BY b.id ORDER BY b.imported_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/import")
async def import_book(req: ImportRequest) -> dict[str, Any]:
    raw, source = await fetch_gutenberg_txt(req.gutenberg_id, req.force_refresh)
    text, meta = normalize_gutenberg_text(raw)
    title = req.title or meta.get("title") or f"Gutenberg {req.gutenberg_id}"
    max_chars = chars_for_seconds(req.target_segment_seconds, req.wpm_for_split)
    chapters = chapterize(text)
    with db() as conn:
        existing = conn.execute("SELECT id FROM books WHERE gutenberg_id=?", (req.gutenberg_id,)).fetchone()
        if existing:
            book_id = existing["id"]
            conn.execute("UPDATE books SET title=?, source=?, txt_url=?, imported_at=CURRENT_TIMESTAMP WHERE id=?", (title, source, f"https://www.gutenberg.org/cache/epub/{req.gutenberg_id}/pg{req.gutenberg_id}.txt", book_id))
            conn.execute("DELETE FROM chapters WHERE book_id=?", (book_id,))
            conn.execute("DELETE FROM segments WHERE book_id=?", (book_id,))
        else:
            cur = conn.execute("INSERT INTO books(gutenberg_id,title,source,txt_url) VALUES(?,?,?,?)", (req.gutenberg_id, title, source, f"https://www.gutenberg.org/cache/epub/{req.gutenberg_id}/pg{req.gutenberg_id}.txt"))
            book_id = cur.lastrowid
        total = 0
        for ci, (ctitle, ctext) in enumerate(chapters):
            cur = conn.execute("INSERT INTO chapters(book_id,chapter_index,title) VALUES(?,?,?)", (book_id, ci, ctitle))
            chapter_id = cur.lastrowid
            for si, seg_text in enumerate(split_segments(ctext, max_chars)):
                conn.execute("INSERT INTO segments(book_id,chapter_id,chapter_index,segment_index,title,text,char_count) VALUES(?,?,?,?,?,?,?)", (book_id, chapter_id, ci, si, f"{ctitle} · {si+1}", seg_text, len(seg_text)))
                total += 1
        conn.execute("INSERT OR REPLACE INTO progress(book_id, chapter_index, segment_index, char_offset) VALUES (?,0,0,0)", (book_id,))
    return {"id": book_id, "gutenberg_id": req.gutenberg_id, "title": title, "chapters": len(chapters), "segments": total, "source": source}


@app.get("/api/books/{book_id}")
def get_book(book_id: int) -> dict[str, Any]:
    with db() as conn:
        b = book_or_404(conn, book_id)
        chapters = conn.execute("""
          SELECT c.chapter_index, c.title, COUNT(s.id) segments
          FROM chapters c LEFT JOIN segments s ON s.chapter_id=c.id
          WHERE c.book_id=? GROUP BY c.id ORDER BY c.chapter_index
        """, (book_id,)).fetchall()
        progress = conn.execute("SELECT * FROM progress WHERE book_id=?", (book_id,)).fetchone()
        return {**dict(b), "chapters": [dict(c) for c in chapters], "progress": dict(progress) if progress else None}


@app.get("/api/books/{book_id}/segment/{chapter_index}/{segment_index}")
def get_segment(book_id: int, chapter_index: int, segment_index: int) -> dict[str, Any]:
    with db() as conn:
        book_or_404(conn, book_id)
        seg = conn.execute("SELECT s.*, c.title chapter_title FROM segments s JOIN chapters c ON c.id=s.chapter_id WHERE s.book_id=? AND s.chapter_index=? AND s.segment_index=?", (book_id, chapter_index, segment_index)).fetchone()
        if not seg:
            raise HTTPException(404, "Segment not found")
        chapter_segments = conn.execute("SELECT COUNT(*) n FROM segments WHERE book_id=? AND chapter_index=?", (book_id, chapter_index)).fetchone()["n"]
        progress = conn.execute("SELECT * FROM progress WHERE book_id=?", (book_id,)).fetchone()
        param_row = conn.execute("SELECT * FROM segment_params WHERE book_id=? AND chapter_index=? AND segment_index=?", (book_id, chapter_index, segment_index)).fetchone()
        params_dict = row_params(param_row)
        params = CWParams(**params_dict)
        char_offset = 0
        if progress and progress["chapter_index"] == chapter_index and progress["segment_index"] == segment_index:
            char_offset = progress["char_offset"]
        return {
            "book_id": book_id,
            "chapter_index": chapter_index,
            "segment_index": segment_index,
            "chapter_title": seg["chapter_title"],
            "segment_title": seg["title"],
            "chapter_segments": chapter_segments,
            "text": seg["text"],
            "char_count": seg["char_count"],
            "char_offset": char_offset,
            "params": params_dict,
            "profile_name": param_row["profile_name"] if param_row else None,
            "timing": timing_metadata(seg["text"], params),
        }


@app.patch("/api/books/{book_id}/state")
def patch_state(book_id: int, update: StateUpdate) -> dict[str, Any]:
    with db() as conn:
        book_or_404(conn, book_id)
        old = conn.execute("SELECT * FROM progress WHERE book_id=?", (book_id,)).fetchone()
        ci = update.chapter_index if update.chapter_index is not None else (old["chapter_index"] if old else 0)
        si = update.segment_index if update.segment_index is not None else (old["segment_index"] if old else 0)
        offset = update.char_offset if update.char_offset is not None else (old["char_offset"] if old else 0)
        conn.execute("INSERT OR REPLACE INTO progress(book_id,chapter_index,segment_index,char_offset,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)", (book_id, ci, si, offset))
        if update.params:
            p = update.params
            conn.execute("""
              INSERT OR REPLACE INTO segment_params(book_id,chapter_index,segment_index,wpm,eff,freq,volume,ews,real,profile_name,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """, (book_id, ci, si, p.wpm, p.eff, p.freq, p.volume, p.ews, int(p.real), update.profile_name))
        row = conn.execute("SELECT * FROM progress WHERE book_id=?", (book_id,)).fetchone()
        return dict(row)


@app.get("/api/books/{book_id}/state")
def get_state(book_id: int) -> dict[str, Any]:
    with db() as conn:
        book_or_404(conn, book_id)
        row = conn.execute("SELECT * FROM progress WHERE book_id=?", (book_id,)).fetchone()
        if not row:
            conn.execute("INSERT INTO progress(book_id) VALUES(?)", (book_id,))
            row = conn.execute("SELECT * FROM progress WHERE book_id=?", (book_id,)).fetchone()
        return dict(row)
