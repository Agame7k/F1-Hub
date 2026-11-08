"""Official FIA paperwork for a stewards' decision: the decision document itself and the rule it cites.

Sources (all fia.com, nothing invented here):
  * the F1 documents page for the event: "Doc 25 - Infringement - Car 51 - Unnecessarily impeding of Car 16"
  * each decision PDF: Fact / Infringement / Decision / Reason, e.g. "Breach of Article B4.1.1 of the FIA F1 Regulations"
  * the F1 regulations PDFs (2026 on: Sections A-F with articles like B4.1.1; before: the Sporting Regulations)

PDF text comes from poppler's `pdftotext` when it is installed; without it you still get the document list and links.
"""
import hashlib
import html
import json
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .api import CACHE_DIR, APIError

BASE = "https://www.fia.com"
DOCS = BASE + "/documents/championships/fia-formula-one-world-championship-14"
REGS = BASE + "/regulation/category/110"
CACHE = CACHE_DIR / "fia"
UA = "Mozilla/5.0 (X11; Linux x86_64) f1hub/1.0 (personal dashboard)"
SECTIONS = {"A": "General Provisions", "B": "Sporting", "C": "Technical", "D": "Financial", "E": "Financial", "F": "Operational"}


class FIAError(APIError):
    pass


def is_fia_url(url):
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return u.scheme == "https" and u.hostname in ("www.fia.com", "fia.com")


# ----------------------------------------------------------------------------- fetching + cache
BLOCK_PAUSE = 1800       # s to leave fia.com alone after it says "too many requests" / shows a bot check
blocked_until = 0.0


class FIABlocked(FIAError):
    pass


def blocked():
    """Seconds left of the pause after fia.com pushed back (0 = fine to ask)."""
    return max(0, blocked_until - time.time())


def _fetch(url, ttl, binary=False):
    global blocked_until
    if not is_fia_url(url):
        raise FIAError("Only fia.com documents can be fetched")
    CACHE.mkdir(parents=True, exist_ok=True)
    ext = ".pdf" if binary else ".html"
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ext)
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl or blocked()):
        return path.read_bytes() if binary else path.read_text()
    if blocked():
        raise FIABlocked(f"fia.com asked for fewer requests - trying again in {int(blocked() // 60) + 1} min")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except (urllib.error.URLError, OSError) as e:
        if isinstance(e, urllib.error.HTTPError) and (e.code in (403, 429) or e.headers.get("cf-mitigated")):
            # rate limit or a bot check: back off completely for a while (never try to get past it)
            blocked_until = time.time() + BLOCK_PAUSE
        if path.exists():                     # fia.com down: an old copy beats nothing
            return path.read_bytes() if binary else path.read_text()
        if blocked():
            raise FIABlocked(f"fia.com asked for fewer requests - trying again in {int(blocked() // 60) + 1} min") from None
        raise FIAError(f"Could not reach fia.com: {e}") from None
    path.write_bytes(data)
    return data if binary else data.decode("utf-8", "replace")


def pdf_text(url):
    """Plain text of an FIA PDF (cached for good: published documents don't change, new versions get new names)."""
    txt = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".txt")
    if txt.exists():
        return txt.read_text()
    if not shutil.which("pdftotext"):
        raise FIAError("Reading PDFs needs poppler's pdftotext (sudo apt install poppler-utils)")
    data = _fetch(url, None, binary=True)
    try:
        out = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError) as e:
        raise FIAError(f"Could not read the PDF: {e}") from None
    text = out.decode("utf-8", "replace")
    txt.write_text(text)
    return text


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ----------------------------------------------------------------------------- event documents
def _options(page):
    return [(html.unescape(v), html.unescape(t).strip()) for v, t in re.findall(r'<option value="([^"]+)">([^<]+)', page)]


def season_events(year):
    """[(path, event name)] of every Grand Prix fia.com has documents for that season."""
    index = _fetch(DOCS, 6 * 3600)
    season = next((v for v, t in _options(index) if t.strip().upper() == f"SEASON {year}"), None)
    if not season:
        raise FIAError(f"fia.com has no F1 documents for {year}")
    page = _fetch(BASE + season, 6 * 3600)
    return [(v, t) for v, t in _options(page) if "/event/" in v and "/season/" in v]


def event_url(path):
    return BASE + urllib.parse.quote(path, safe="/%")


def event_name(year, meeting_name):
    """fia.com's name for the Grand Prix OpenF1 calls meeting_name."""
    want = _norm(meeting_name)
    for v, t in season_events(year):
        if _norm(t) == want:
            return t
    words = set(want.split()) - {"grand", "prix"}
    scored = sorted(((len(words & set(_norm(t).split())), t) for v, t in season_events(year)), reverse=True)
    return scored[0][1] if scored and scored[0][0] else meeting_name


def event_page(year, meeting_name):
    """URL of the documents page for one Grand Prix."""
    want = _norm(meeting_name)
    events = season_events(year)
    best = next((v for v, t in events if _norm(t) == want), None)
    if not best:          # "Mexico City Grand Prix" vs "Mexican Grand Prix" and the like: most shared words wins
        words = set(want.split()) - {"grand", "prix"}
        scored = sorted(((len(words & set(_norm(t).split())), v) for v, t in events), reverse=True)
        best = scored[0][1] if scored and scored[0][0] else None
    if not best:
        raise FIAError(f"No FIA documents page for {meeting_name} {year}")
    return event_url(best)


DOC_RE = re.compile(r'<a href="([^"]+\.pdf)"[^>]*>.*?<div class="title">\s*(.*?)\s*</div>.*?'
                    r'date-display-single">([^<]+)</span>', re.S)


def parse_title(title):
    """'Doc 25 - Infringement - Car 51 - Unnecessarily impeding of Car 16' -> kind, cars, offence."""
    m = re.match(r"Doc\s+(\d+)\s*-\s*(.*)", title)
    no, rest = (int(m.group(1)), m.group(2)) if m else (None, title)
    parts = [p.strip() for p in re.split(r"\s+-\s+", rest) if p.strip()]
    kind = parts[0].lower() if parts else ""
    cars = [int(c) for c in re.findall(r"\bCars?\s+(\d{1,2})\b", rest, re.I)]
    for extra in re.findall(r"\bCars?\s+\d{1,2}((?:\s*(?:,|and|&)\s*\d{1,2})+)", rest, re.I):
        cars += [int(c) for c in re.findall(r"\d{1,2}", extra)]
    offence = parts[-1] if len(parts) > 1 and not re.match(r"Cars?\s+\d", parts[-1], re.I) else ""
    stewarding = kind in ("infringement", "decision", "offence", "summons", "penalty", "reprimand", "right of review")
    return {"no": no, "kind": kind, "cars": list(dict.fromkeys(cars)), "offence": offence, "stewarding": stewarding}


def event_documents(year, meeting_name, ttl=120, url=None):
    """Every document published for the event, newest first."""
    page = _fetch(url or event_page(year, meeting_name), ttl)
    docs = []
    for href, title, published in DOC_RE.findall(page):
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title))).strip()
        url = href if href.startswith("http") else BASE + href
        docs.append(dict(parse_title(title), title=title, url=url, published=published.strip() + " CET"))
    return docs


STOP = {"the", "of", "a", "an", "in", "on", "to", "for", "and", "car", "cars", "alleged", "allegedly", "incident",
        "involving", "under", "investigation", "noted", "fia", "stewards", "will", "be", "investigated", "after",
        "session", "race", "lap", "turn", "time", "by", "with", "at", "no", "further", "action", "penalty", "second",
        "seconds", "drive", "through", "stop", "go", "reviewed", "decision", "infringement", "summons"}


def _words(s):
    return {w for w in _norm(s).split() if w not in STOP and not w.isdigit() and len(w) > 2}


def match(docs, cars, text):
    """Documents about these cars, best match to the race-control text first (decisions before summonses)."""
    want, cars = _words(text), set(cars or ())
    out = []
    for d in docs:
        if not d["stewarding"] or (cars and not cars & set(d["cars"])):
            continue
        overlap = len(want & _words(d["offence"] or d["title"]))
        final = d["kind"] in ("infringement", "decision", "offence")
        out.append((overlap, final, d["no"] or 0, d))
    out.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [dict(d, score=o) for o, _, _, d in out]


# ----------------------------------------------------------------------------- decision documents
FIELDS = ("No / Driver", "Competitor", "Time", "Session", "Fact", "Infringement", "Decision", "Reason")
FIELD_RE = re.compile(r"^\s{0,3}(" + "|".join(re.escape(f) for f in FIELDS) + r")\b\s*(.*)$")
BOILER = re.compile(r"Competitors are reminded|Decisions of the Stewards are taken independently|^\s*The Stewards\s*$")


def parse_decision(text):
    """Fields of a stewards' decision, plus the articles it cites."""
    fields, cur = {}, None
    for line in text.splitlines():
        if BOILER.search(line):
            cur = None if cur != "Reason" else "_end"
            continue
        m = FIELD_RE.match(line)
        if m and m.group(1) not in fields:
            cur = m.group(1)
            fields[cur] = [m.group(2).strip()]
        elif cur in fields and line.strip():
            fields[cur].append(line.strip())
        elif cur == "Reason" and not line.strip() and len(fields[cur]) > 1:
            fields[cur].append("")
    out = {k.split(" /")[0].lower(): re.sub(r"[ \t]+", " ", "\n".join(v)).strip() for k, v in fields.items()}
    out["articles"] = cited_articles(out.get("infringement", ""))
    head = re.search(r"Document\s+(\d+)", text)
    out["document"] = int(head.group(1)) if head else None
    return out


ART_RE = re.compile(r"Articles?\s+((?:[A-F]\d+|\d+)(?:\.\d+)*(?:\s*\(?[a-z]\)(?![a-z])|[a-z]\b)?)")
APPL_RE = re.compile(r"Appendix\s+L,?\s*Chapter\s+([IVX]+),?\s*Article\s+(\d+)(?:\.\s*|\s*)(?:\(?([a-z])\))?", re.I)


def cited_articles(infringement):
    """What the decision says was breached:
      'Breach of Article B4.1.1 of the FIA F1 Regulations'            -> {"ref": "B4.1.1", "source": "f1"}
      'Appendix L, Chapter IV, Article 2 d) of the ... Sporting Code' -> {"ref": "L-IV-2d", "source": "appendix_l"}
      'Article 12.2.1.l of the International Sporting Code'           -> {"source": "isc"} (linked, not quoted)"""
    s = re.sub(r"\s+", " ", infringement or "")
    refs, taken = [], []
    for m in APPL_RE.finditer(s):
        chap, art, item = m.group(1).upper(), m.group(2), (m.group(3) or "").lower()
        refs.append({"ref": f"L-{chap}-{art}{item}", "source": "appendix_l",
                     "label": f"Appendix L, Chapter {chap}, Article {art}{' ' + item + ')' if item else ''}"})
        taken.append((m.start(), m.end()))
    for m in ART_RE.finditer(s):
        if any(a <= m.start() < b for a, b in taken):
            continue
        ref = re.sub(r"\s+", "", m.group(1))
        tail = s[m.end():m.end() + 80].lower()
        isc = "sporting code" in tail.split("article")[0] and "f1 regulations" not in tail.split("article")[0]
        refs.append({"ref": ref, "source": "isc" if isc else "f1",
                     "label": f"Article {ref}" + (" of the International Sporting Code" if isc else "")})
    seen, uniq = set(), []
    for r in refs:
        if r["ref"] not in seen:
            seen.add(r["ref"])
            uniq.append(r)
    return uniq


def decision(url):
    if not is_fia_url(url):
        raise FIAError("Only fia.com documents can be opened")
    return dict(parse_decision(pdf_text(url)), url=url)


# ----------------------------------------------------------------------------- the regulations
ISC_PAGE = BASE + "/regulation/category/123"


def isc_document(year, appendix_l=False):
    """The International Sporting Code (or its Appendix L, the drivers' code of conduct) for that year."""
    page = _fetch(ISC_PAGE, 12 * 3600)
    links = re.findall(r'href="([^"]+\.pdf)"', page)
    if appendix_l:
        for y in (year, year - 1):
            hit = next((u for u in links if re.search(rf"appendix_l_{y}", u)), None)
            if hit:
                return {"title": f"FIA International Sporting Code - Appendix L {y}", "url": hit}
    for y in (year, year - 1):
        hit = next((u for u in links if re.search(rf"{y}.*(international_sporting_code|isc)", u, re.I)
                    and "marked" not in u.lower() and "esport" not in u.lower()), None)
        if hit:
            return {"title": f"FIA International Sporting Code {y}", "url": hit}
    return {"title": "FIA International Sporting Code", "url": ISC_PAGE}


def _english_column(text):
    """Appendix L is printed French | English side by side: keep the right-hand column."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(.*?\S)\s{3,}(\S.*)$", line)
        if m and len(m.group(1)) > 25:
            out.append(m.group(2))
        elif line.strip() and len(line) - len(line.lstrip()) > 40:
            out.append(line.strip())
        elif not line.strip():
            out.append("")
    return out


def appendix_l(ref, year):
    """'L-IV-2d' -> Appendix L, Chapter IV, Article 2 d), English text."""
    m = re.match(r"L-([IVX]+)-(\d+)([a-z]?)$", ref)
    if not m:
        raise FIAError(f"Not an Appendix L reference: {ref}")
    chap, art, item = m.groups()
    doc = isc_document(year, appendix_l=True)
    if not doc["url"].endswith(".pdf"):
        raise FIAError("Couldn't find Appendix L on fia.com")
    lines = _english_column(pdf_text(doc["url"]))
    i = next((k for k, l in enumerate(lines) if re.match(rf"CHAPTER {chap}\b", l) and k > 80), None)
    if i is None:
        raise FIAError(f"Chapter {chap} isn't in {doc['title']}")
    a = next((k for k in range(i, len(lines)) if re.match(rf"{art}\.\s", lines[k])), None)
    if a is None:
        raise FIAError(f"Chapter {chap} Article {art} isn't in {doc['title']}")
    end = next((k for k in range(a + 1, len(lines)) if re.match(rf"({int(art) + 1}\.\s|CHAPTER\s)", lines[k])), len(lines))
    block = [l for l in lines[a:end] if not re.search(r"APPENDIX L|Published on|^Page \d", l)]
    head = block[0]
    if item:
        s0 = next((k for k, l in enumerate(block) if re.match(rf"{item}\)\s", l)), None)
        if s0 is not None:
            s1 = next((k for k in range(s0 + 1, len(block)) if re.match(r"[a-z]\)\s", block[k])), len(block))
            block = [head, "…"] + block[s0:s1]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(block)).strip()
    return {"ref": f"Appendix L, Chapter {chap}, Article {art}{' ' + item + ')' if item else ''}", "text": body,
            "regulations": doc["title"], "url": doc["url"], "see_also": []}


def regulations(year, section="B"):
    """Newest issue of the regulations that hold this article: {'title', 'url'}."""
    page = _fetch(REGS, 12 * 3600)
    items = re.findall(r'href="([^"]+\.pdf)"[^>]*>.*?<div class="title">\s*([^<]+?)\s*</div>', page, re.S)
    if not items:   # layout fallback: pair the links with the titles in page order
        items = list(zip(re.findall(r'href="([^"]+\.pdf)"', page), re.findall(r'<div class="title">\s*([^<]+?)\s*<', page)[1:]))
    for url, title in items:
        t = html.unescape(title)
        if section and re.search(rf"\b{year}\b.*Section {section}\b", t):
            return {"title": t, "url": url if url.startswith("http") else BASE + url}
        if not section and re.search(rf"\b{year}\b.*Formula (One|1) Sporting Regulations", t, re.I) and "PU" not in t:
            return {"title": t, "url": url if url.startswith("http") else BASE + url}
    raise FIAError(f"Couldn't find the {year} F1 regulations on fia.com")


HEADER_RE = re.compile(r"Formula (?:1|One) Regulations - Section|Fédération Internationale de l|^SECTION [A-F]:|"
                       r"Sporting Regulations\s+\d|^\s*©|^\s*[A-F]?\s*$|^\s*\d+\s*$|Issue \d+\s+[A-F]?\d+\s*$")


def article(ref, year):
    """Text of one article, e.g. 'B4.1.1' (2026 on) or '33.3' (before), straight from the regulations PDF."""
    ref = ref.strip().rstrip(".")
    if ref.startswith("L-"):
        return appendix_l(ref, year)
    sub = re.match(r"^(.*?)\(?([a-z])\)?$", ref)
    letter = None
    if sub and re.search(r"\d", sub.group(1)):
        ref, letter = sub.group(1).rstrip("("), sub.group(2)
    section = ref[0].upper() if ref[:1].isalpha() else None
    regs = regulations(year, section)
    text = pdf_text(regs["url"])
    pages = text.split("\f")
    num = re.escape(ref)
    start_re = re.compile(rf"^\s*(?:ARTICLE\s+)?{num}(?:[:\s]|$)")
    level = ref.count(".")
    stop_re = re.compile(r"^\s*(?:ARTICLE\s+)?" + (rf"{section}\d+" if section else r"\d+") + r"(?:\.\d+)*(?:\)|[:\s]|$)")
    lines, page_no, found = [], None, False
    for p, page in enumerate(pages):
        for line in page.splitlines():
            if not found:
                if start_re.match(line) and "...." not in line and not re.search(r"\s{3,}\d+\s*$", line):
                    found, page_no = True, p + 1
                    lines.append(line.rstrip())
                continue
            m = stop_re.match(line)
            if m:
                other = re.match(r"^\s*(?:ARTICLE\s+)?([A-F]?\d+(?:\.\d+)*)", line).group(1)
                if other != ref and (other.count(".") <= level or not other.startswith(ref + ".")):
                    found = None
                    break
            if HEADER_RE.search(line):
                continue
            lines.append(line.rstrip())
        if found is None:
            break
    if not lines:
        raise FIAError(f"Article {ref} isn't in {regs['title']}")
    body = "\n".join(lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    # indentation from the PDF layout: trim the common left margin of continuation lines
    out = [body.splitlines()[0].strip()]
    rest = [l for l in body.splitlines()[1:]]
    margin = min((len(l) - len(l.lstrip()) for l in rest if l.strip()), default=0)
    out += [l[margin:] for l in rest]
    body = "\n".join(out)
    if letter:   # just one lettered item: "a." or "a)"
        m = re.search(rf"(?ms)^\s*{letter}[.)]\s.*?(?=^\s*[a-z][.)]\s|\Z)", body)
        if m:
            body = out[0] + "\n…\n" + m.group(0).rstrip()
    refs = sorted(set(re.findall(r"Articles?\s+((?:[A-F]\d+|\d+)(?:\.\d+)+)", body)) - {ref})
    return {"ref": ref + (f"({letter})" if letter else ""), "text": body, "regulations": regs["title"],
            "url": regs["url"] + (f"#page={page_no}" if page_no else ""), "see_also": refs[:8]}


def suggest(text, year, limit=3):
    """Before the stewards publish anything: articles of the Sporting regulations whose wording best matches
    the race-control message. Only a pointer - the decision document says which rule was actually applied."""
    text = re.sub(r"\b\d{1,2} \([A-Z]{3}\)|\([^)]*\)", " ", text or "")       # car numbers, driver codes, times
    words = {w[:max(4, len(w) - 3)] for w in _words(text)}                         # crude stems: reversing ~ reversed
    if not words:
        return []
    section = "B" if year >= 2026 else None
    regs = regulations(year, section)
    body = pdf_text(regs["url"])
    pat = re.compile(r"^((?:B)?\d+(?:\.\d+)+)\s{2,}(.*)$" if section else r"^(\d+\.\d+)\s+(.*)$")
    chunks, cur = [], None
    for line in body.splitlines():
        m = pat.match(line)
        if m and "...." not in line:
            cur = [m.group(1), m.group(2)]
            chunks.append(cur)
        elif cur and line.strip() and not HEADER_RE.search(line):
            cur[1] += " " + line.strip()
    scored = []
    for ref, t in chunks:
        if len(t) < 80:          # a bare heading, nothing to read
            continue
        tl = _norm(t)
        hits = sum(1 for w in words if (" " + w) in (" " + tl))
        if "pit lane" in text.lower() and "pit lane" in tl:
            hits += 0.5
        if hits:
            scored.append((hits / len(words), -len(t), ref, t))
    scored.sort(reverse=True)
    top = scored[0][0] if scored else 0
    scored = [x for x in scored if x[0] >= top * 0.9]       # only the clear front-runners
    return [{"ref": r, "preview": t[:220] + ("…" if len(t) > 220 else ""), "match": round(min(s, 1.0), 2)}
            for s, _, r, t in scored[:limit] if s >= 0.5]
