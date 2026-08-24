"""PDF 파싱: 페이지 이미지 렌더링, 문장 단위 추출(좌표 포함), figure/table 캡션 탐지."""
import json
import re
from pathlib import Path

import pymupdf
import pysbd

RENDER_SCALE = 2.0  # 페이지 PNG 렌더링 배율 (144dpi 상당)

_segmenter = pysbd.Segmenter(language="en", clean=False, char_span=True)

CAPTION_RE = re.compile(
    r"^\s*(Figure|Fig\.|FIGURE|Table|TABLE|Tab\.)\s*([0-9]+|[IVXL]+)\s*([.:|])?(\s|$)",
)


def _norm_rect(rect, page_w, page_h):
    x0, y0, x1, y1 = rect
    return [
        round(x0 / page_w, 5),
        round(y0 / page_h, 5),
        round(x1 / page_w, 5),
        round(y1 / page_h, 5),
    ]


def _extract_title(doc):
    """1페이지 상단에서 가장 큰 폰트의 라인들을 제목으로 추정."""
    try:
        page = doc[0]
        d = page.get_text("dict")
        cand = []  # (size, y, text)
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if len(text) < 4 or line["bbox"][1] > page.rect.height * 0.55:
                    continue
                # 회전 텍스트(arXiv 세로 워터마크 등)와 arXiv 스탬프 제외
                dx, dy = line.get("dir", (1, 0))
                if abs(dx) < 0.99 or abs(dy) > 0.01 or text.lower().startswith("arxiv:"):
                    continue
                size = max(s.get("size", 0) for s in line.get("spans", []))
                cand.append((size, line["bbox"][1], text))
        if not cand:
            return None
        max_size = max(c[0] for c in cand)
        title_lines = [c for c in cand if c[0] >= max_size - 0.5]
        title_lines.sort(key=lambda c: c[1])
        title = " ".join(c[2] for c in title_lines[:3]).strip()
        return title[:250] if title else None
    except Exception:
        return None


def _page_words(page):
    """읽기 순서로 정렬된 단어 목록: (x0,y0,x1,y1,word,block,line,word_no)"""
    words = page.get_text("words")
    words.sort(key=lambda w: (w[5], w[6], w[7]))
    return words


def _words_and_text(page):
    """단어 목록 + 이어붙인 페이지 텍스트 + 각 단어의 char 범위."""
    words = _page_words(page)
    parts = []
    spans = []  # (start, end, word_idx)
    pos = 0
    for i, w in enumerate(words):
        token = w[4]
        if parts:
            pos += 1  # 공백
        start = pos
        pos += len(token)
        parts.append(token)
        spans.append((start, pos, i))
    return words, " ".join(parts), spans


def _range_to_rects(words, spans, word_starts, start, end, pw, ph):
    """텍스트 char 범위 [start, end) 를 라인 단위 병합 rect(정규화)로 변환."""
    import bisect

    lo = max(bisect.bisect_right(word_starts, start) - 1, 0)
    by_line = {}
    for j in range(lo, len(spans)):
        s, e, wi = spans[j]
        if s >= end:
            break
        if e > start:
            w = words[wi]
            key = (w[5], w[6])
            r = by_line.get(key)
            if r is None:
                by_line[key] = [w[0], w[1], w[2], w[3]]
            else:
                r[0] = min(r[0], w[0])
                r[1] = min(r[1], w[1])
                r[2] = max(r[2], w[2])
                r[3] = max(r[3], w[3])
    return [_norm_rect(r, pw, ph) for r in by_line.values()]


def _first_block(words, spans, word_starts, start, end):
    """char 범위에 걸치는 첫 단어의 block 번호 (문단 근사)."""
    import bisect

    lo = max(bisect.bisect_right(word_starts, start) - 1, 0)
    for j in range(lo, len(spans)):
        s, e, wi = spans[j]
        if s >= end:
            break
        if e > start:
            return words[wi][5]
    return None


def _page_sentences(page):
    """페이지의 문장 목록. 각 문장은 텍스트 + 라인 단위 병합 bbox(정규화 좌표) + 문단 번호."""
    words, text, spans = _words_and_text(page)
    if not words:
        return [], ""
    pw, ph = page.rect.width, page.rect.height

    word_starts = [s[0] for s in spans]

    sentences = []
    try:
        segs = _segmenter.segment(text)
    except Exception:
        segs = []
    if not segs:
        segs = [type("S", (), {"sent": text, "start": 0, "end": len(text)})()]

    for seg in segs:
        sent_text = seg.sent.strip()
        if len(sent_text) < 2:
            continue
        rects = _range_to_rects(words, spans, word_starts, seg.start, seg.end, pw, ph)
        if rects:
            sentences.append({
                "text": sent_text,
                "rects": rects,
                "b": _first_block(words, spans, word_starts, seg.start, seg.end),
            })

    for i, s in enumerate(sentences):
        s["i"] = i
    return sentences, text


def _page_lines(page):
    """텍스트 선택 레이어용 라인 목록: [{text, rect(정규화)}]"""
    pw, ph = page.rect.width, page.rect.height
    out = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            # 회전 텍스트(세로 워터마크)는 선택 레이어에서 제외
            dx, dy = line.get("dir", (1, 0))
            if abs(dx) < 0.99 or abs(dy) > 0.01:
                continue
            text = "".join(s.get("text", "") for s in line.get("spans", []))
            if text.strip():
                out.append({"text": text, "rect": _norm_rect(line["bbox"], pw, ph)})
    return out


def ensure_sentences(paper_dir: Path, n: int):
    """페이지 문장 데이터 (lazy 캐시). 업로드 때 만들지 않고 첫 조회 때 생성."""
    f = paper_dir / "sentences" / f"{n}.json"
    if f.exists():
        return json.loads(f.read_text())
    doc = pymupdf.open(paper_dir / "paper.pdf")
    try:
        if n < 1 or n > len(doc):
            raise ValueError(f"page {n} out of range")
        sentences, _ = _page_sentences(doc[n - 1])
    finally:
        doc.close()
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(sentences, ensure_ascii=False))
    return sentences


def ensure_lines(paper_dir: Path, n: int):
    """페이지 라인 데이터 (lazy 캐시) — 기존 논문도 첫 조회 때 생성."""
    f = paper_dir / "lines" / f"{n}.json"
    if f.exists():
        return json.loads(f.read_text())
    doc = pymupdf.open(paper_dir / "paper.pdf")
    try:
        if n < 1 or n > len(doc):
            raise ValueError(f"page {n} out of range")
        lines = _page_lines(doc[n - 1])
    finally:
        doc.close()
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(lines, ensure_ascii=False))
    return lines


def _page_figures(page, page_no):
    """블록 시작이 'Figure N' / 'Table N' 형태인 캡션 블록 탐지."""
    figures = []
    pw, ph = page.rect.width, page.rect.height
    d = page.get_text("dict")
    for bi, block in enumerate(d.get("blocks", [])):
        lines = block.get("lines", [])
        if not lines:
            continue
        text = " ".join(
            "".join(s.get("text", "") for s in line.get("spans", [])) for line in lines
        ).strip()
        m = CAPTION_RE.match(text + " ")
        if not m:
            continue
        kind = "table" if m.group(1).lower().startswith("tab") else "figure"
        label = f"{'Table' if kind == 'table' else 'Figure'} {m.group(2)}"
        figures.append(
            {
                "id": f"p{page_no}-{kind}{m.group(2)}-{bi}",
                "page": page_no,
                "kind": kind,
                "num": m.group(2),
                "sep": bool(m.group(3)),  # "Figure 1:" 처럼 구분자가 있으면 진짜 캡션일 확률 높음
                "label": label,
                "caption": text[:600],
                "rect": _norm_rect(block["bbox"], pw, ph),
            }
        )
    return figures


def _dedup_figures(figures):
    """같은 (kind, 번호)가 여러 번 잡히면 구분자(:/.) 있는 캡션 우선, 그다음 첫 등장."""
    best = {}
    for f in figures:
        key = (f["kind"], f["num"])
        cur = best.get(key)
        if cur is None or (f["sep"] and not cur["sep"]):
            best[key] = f
    result = sorted(best.values(), key=lambda f: (f["page"], f["rect"][1]))
    for f in result:
        f.pop("num", None)
        f.pop("sep", None)
    return result


def parse_pdf(pdf_path: Path, out_dir: Path) -> dict:
    """PDF 파싱. meta/figures/fulltext 생성 (문장 분할은 페이지별 lazy — ensure_sentences)."""
    doc = pymupdf.open(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sentences").mkdir(exist_ok=True)
    (out_dir / "pages").mkdir(exist_ok=True)

    pages_meta = []
    figures = []
    fulltext_parts = []

    for n in range(len(doc)):
        page = doc[n]
        pages_meta.append(
            {
                "n": n + 1,
                "w": round(page.rect.width, 2),
                "h": round(page.rect.height, 2),
            }
        )
        _, text, _ = _words_and_text(page)  # pysbd 없이 텍스트만 (대형 논문 업로드 수 초 단축)
        figures.extend(_page_figures(page, n + 1))
        fulltext_parts.append(f"=== Page {n + 1} ===\n{text}")

    title = _extract_title(doc) or pdf_path.stem
    meta = {"title": title, "npages": len(doc), "pages": pages_meta}

    figures = _dedup_figures(figures)
    (out_dir / "figures.json").write_text(json.dumps(figures, ensure_ascii=False))
    (out_dir / "fulltext.txt").write_text("\n\n".join(fulltext_parts))
    doc.close()
    return meta


def ensure_page_image(paper_dir: Path, n: int) -> Path:
    """페이지 PNG 렌더링 (캐시). n은 1-base."""
    png = paper_dir / "pages" / f"page-{n}.png"
    if png.exists():
        return png
    doc = pymupdf.open(paper_dir / "paper.pdf")
    try:
        if n < 1 or n > len(doc):
            raise ValueError(f"page {n} out of range")
        pix = doc[n - 1].get_pixmap(matrix=pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE))
        png.parent.mkdir(exist_ok=True)
        pix.save(png)
    finally:
        doc.close()
    return png


# ---------------------------------------------------------------- 인용/참고문헌

CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[,;–—-]\s*\d{1,3})*)\]")
REF_HEAD_RE = re.compile(r"^\s*(?:[0-9IVX]+\.?\s+)?(references|bibliography)\s*$", re.I)
ENTRY_RE = re.compile(r"^\[(\d{1,3})\]\s+\S")


def _expand_nums(group: str):
    """'1, 3-5' → [1, 3, 4, 5]"""
    nums = []
    for part in re.split(r"[,;]", group):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[–—-]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b <= a + 50:
                nums.extend(range(a, b + 1))
        elif part.isdigit():
            nums.append(int(part))
    return nums


def parse_references(doc):
    """References 섹션을 찾아 [N] 형식 항목을 파싱.

    반환: {"head": {"page", "y"}, "entries": [{n, text, page, rect}]} 또는 None
    (author-year 스타일 등 [N] 항목이 없으면 None)
    """
    all_lines = []  # (page_no, bbox, text)
    for pno in range(len(doc)):
        d = doc[pno].get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if text:
                    all_lines.append((pno + 1, line["bbox"], text))

    head_idx = None
    for i, (_, _, text) in enumerate(all_lines):
        if REF_HEAD_RE.match(text):
            head_idx = i  # 마지막 매치(본문 목차 언급 배제)
    if head_idx is None:
        return None
    head_page, head_bbox, _ = all_lines[head_idx]

    entries = {}
    cur = None
    for pno, bbox, text in all_lines[head_idx + 1:]:
        m = ENTRY_RE.match(text)
        if m:
            n = int(m.group(1))
            if n not in entries:
                cur = {"n": n, "text": text, "lines": [(pno, bbox)]}
                entries[n] = cur
            else:
                cur = None
        elif cur is not None and len(cur["lines"]) < 8:
            # 항목 이어짐 (다음 [N] 전까지, 과도한 흡수 방지 위해 8라인 제한)
            cur["text"] = (cur["text"] + " " + text)[:800]
            cur["lines"].append((pno, bbox))

    if not entries:
        return None

    result = []
    for n in sorted(entries):
        e = entries[n]
        page = e["lines"][0][0]
        rects = [b for p, b in e["lines"] if p == page]
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[2] for r in rects)
        y1 = max(r[3] for r in rects)
        pr = doc[page - 1].rect
        result.append(
            {
                "n": n,
                "text": e["text"],
                "page": page,
                "rect": _norm_rect((x0, y0, x1, y1), pr.width, pr.height),
                "continues": e["lines"][-1][0] != page,  # 다음 페이지로 이어짐
            }
        )
    return {"head": {"page": head_page, "y": head_bbox[1] / doc[head_page - 1].rect.height},
            "entries": result}


def detect_citations(doc, valid_nums: set, head: dict | None):
    """본문에서 [N], [N-M], [N, M] 인용 표기를 좌표와 함께 탐지.

    valid_nums 에 없는 번호만 있는 매치는 버림(오탐 방지).
    head 이후(References 영역)의 매치는 제외.
    """
    citations = []
    for pno in range(len(doc)):
        page = doc[pno]
        words, text, spans = _words_and_text(page)
        if not words:
            continue
        pw, ph = page.rect.width, page.rect.height
        word_starts = [s[0] for s in spans]
        for m in CITE_RE.finditer(text):
            nums = [n for n in _expand_nums(m.group(1)) if n in valid_nums]
            if not nums:
                continue
            rects = _range_to_rects(words, spans, word_starts, m.start(), m.end(), pw, ph)
            if not rects:
                continue
            # References 영역 내부(항목 라벨 등)는 제외
            if head and (
                pno + 1 > head["page"]
                or (pno + 1 == head["page"] and rects[0][1] >= head["y"])
            ):
                continue
            citations.append({"page": pno + 1, "nums": nums, "rects": rects})
    return citations


def ensure_citations(paper_dir: Path) -> dict:
    """citations.json / references.json 을 (없으면 생성 후) 반환.

    기존에 파싱된 논문도 처음 조회 시 자동 생성된다(lazy migration).
    """
    cf = paper_dir / "citations.json"
    rf = paper_dir / "references.json"
    if cf.exists() and rf.exists():
        return {
            "citations": json.loads(cf.read_text()),
            "references": json.loads(rf.read_text()),
        }
    doc = pymupdf.open(paper_dir / "paper.pdf")
    try:
        refs = parse_references(doc)
        if refs:
            valid = {e["n"] for e in refs["entries"]}
            cites = detect_citations(doc, valid, refs["head"])
        else:
            refs = {"head": None, "entries": []}
            cites = []
    finally:
        doc.close()
    rf.write_text(json.dumps(refs, ensure_ascii=False))
    cf.write_text(json.dumps(cites, ensure_ascii=False))
    return {"citations": cites, "references": refs}


def ensure_ref_crop(paper_dir: Path, entry: dict) -> Path:
    """참고문헌 항목 영역을 페이지에서 잘라낸 PNG (캐시)."""
    (paper_dir / "refs").mkdir(exist_ok=True)
    png = paper_dir / "refs" / f"ref-{entry['n']}.png"
    if png.exists():
        return png
    doc = pymupdf.open(paper_dir / "paper.pdf")
    try:
        page = doc[entry["page"] - 1]
        pw, ph = page.rect.width, page.rect.height
        x0, y0, x1, y1 = entry["rect"]
        clip = pymupdf.Rect(
            max(0, x0 * pw - 4), max(0, y0 * ph - 3),
            min(pw, x1 * pw + 4), min(ph, y1 * ph + 3),
        )
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), clip=clip)
        pix.save(png)
    finally:
        doc.close()
    return png


def find_citing_sentences(fulltext: str, n: int, limit: int = 4):
    """본문에서 [n] 을 인용한 문장들."""
    out = []
    for raw in re.split(r"(?<=[.!?])\s+", fulltext):
        line = raw.strip()
        if ENTRY_RE.match(line):  # 참고문헌 항목 자체는 제외
            continue
        for m in CITE_RE.finditer(line):
            if n in _expand_nums(m.group(1)):
                out.append(line[:350])
                break
        if len(out) >= limit:
            break
    return out


def find_mentions(fulltext: str, label: str, caption: str, limit: int = 3):
    """본문에서 해당 figure/table을 언급한 문장 추출 (캡션 자체 제외)."""
    pat = re.compile(re.escape(label).replace(r"Figure", r"(?:Figure|Fig\.?)"), re.I)
    mentions = []
    cap_head = caption[:60]
    for raw in re.split(r"(?<=[.!?])\s+", fulltext):
        line = raw.strip()
        if not pat.search(line) or line.startswith(cap_head[:30]):
            continue
        mentions.append(line[:400])
        if len(mentions) >= limit:
            break
    return mentions
