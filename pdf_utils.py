"""PDF 파싱: 페이지 이미지 렌더링, 문장 단위 추출(좌표 포함), figure/table 캡션 탐지."""
import json
import re
from pathlib import Path

import pymupdf
import pysbd

import paths  # noqa: F401 — Windows cp949 대응 UTF-8 파일 I/O 패치 적용

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


YEAR_RE = re.compile(r"\b((?:19|20)\d{2})([a-z])?\b")


def _numeric_raw_entries(lines):
    """[N] 라벨 스타일 항목 파싱. {n: {"n","text","lines"}}"""
    entries = {}
    cur = None
    for pno, bbox, text in lines:
        m = ENTRY_RE.match(text)
        if m:
            n = int(m.group(1))
            if n not in entries:
                cur = {"n": n, "text": text, "lines": [(pno, bbox)]}
                entries[n] = cur
            else:
                cur = None
        elif cur is not None and len(cur["lines"]) < 8:
            cur["text"] = (cur["text"] + " " + text)[:800]
            cur["lines"].append((pno, bbox))
    return entries


def _author_year_raw_entries(lines, doc):
    """라벨 없는 author-year 스타일: hanging indent(왼쪽 여백 시작)로 항목 분리."""
    # 페이지 상단 러닝 헤더(학회명·연도 등)는 항목으로 오인되므로 제외
    lines = [
        (p, b, t) for p, b, t in lines
        if b[1] > doc[p - 1].rect.height * 0.06
    ]
    if not lines:
        return {}

    def colkey(pno, bbox):
        return (pno, 0 if bbox[0] < doc[pno - 1].rect.width / 2 else 1)

    col_min = {}
    for pno, bbox, _ in lines:
        k = colkey(pno, bbox)
        col_min[k] = min(col_min.get(k, 1e9), bbox[0])

    raw = []
    cur = None
    for pno, bbox, text in lines:
        is_start = bbox[0] - col_min[colkey(pno, bbox)] < 4.0
        if is_start:
            if cur:
                raw.append(cur)
            cur = {"text": text, "lines": [(pno, bbox)]}
        elif cur is not None and len(cur["lines"]) < 10:
            cur["text"] = (cur["text"] + " " + text)[:800]
            cur["lines"].append((pno, bbox))
    if cur:
        raw.append(cur)

    # 연도가 없는 덩어리(섹션 제목·부록 본문)는 제외하고,
    # 연도 없는 덩어리가 연속으로 이어지면 References가 끝난 것으로 본다
    entries = {}
    n = 0
    dry = 0
    for e in raw:
        m = YEAR_RE.search(e["text"])
        # 첫 저자의 성 추출: 이니셜("I.")의 마침표를 지운 뒤 첫 쉼표/마침표 전까지,
        # "A and B" 형식이면 첫 저자만
        head80 = re.sub(r"\b([A-Z])\.", r"\1", e["text"][:80])
        seg = re.split(r"[.,]", head80, 1)[0][:60]
        first_author = re.split(r"\s+(?:and|&)\s+", seg)[0]
        words = re.findall(r"[A-Za-z\-']+", first_author)
        if not m or not words:
            dry += 1
            if dry >= 15:
                break
            continue
        dry = 0
        n += 1
        e["n"] = n
        e["ay"] = {"surname": words[-1], "year": m.group(1), "suffix": m.group(2)}
        entries[n] = e
    return entries


def parse_references(doc):
    """References 섹션 파싱 — [N] 숫자 스타일과 author-year 스타일 모두 지원.

    반환: {"style": "numeric"|"ay", "head": {...}, "entries": [...]} 또는 None
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
    region = all_lines[head_idx + 1:]

    entries = _numeric_raw_entries(region)
    style = "numeric"
    if not entries:
        entries = _author_year_raw_entries(region, doc)
        style = "ay"
    if not entries:
        return None

    result = []
    end_page, end_y = head_page, head_bbox[3]
    for n in sorted(entries):
        e = entries[n]
        page = e["lines"][0][0]
        rects = [b for p, b in e["lines"] if p == page]
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[2] for r in rects)
        y1 = max(r[3] for r in rects)
        pr = doc[page - 1].rect
        ay = e.get("ay")
        result.append(
            {
                "n": n,
                "text": e["text"],
                "page": page,
                "rect": _norm_rect((x0, y0, x1, y1), pr.width, pr.height),
                "continues": e["lines"][-1][0] != page,
                "label": (f"{ay['surname']} {ay['year']}{ay['suffix'] or ''}"
                          if ay else f"[{n}]"),
                **({"ay": ay} if ay else {}),
            }
        )
        lp, lb = e["lines"][-1]
        if (lp, lb[3]) > (end_page, end_y if lp == end_page else -1):
            end_page, end_y = lp, lb[3]
    return {
        "style": style,
        "head": {"page": head_page, "y": head_bbox[1] / doc[head_page - 1].rect.height},
        # 참고문헌 목록의 끝 — 이 뒤(부록 등)의 인용은 본문 인용으로 취급
        "end": {"page": end_page, "y": end_y / doc[end_page - 1].rect.height},
        "entries": result,
    }


# author-year 본문 인용: (Wu et al., 2016; Chen & Lee, 2017) / Wu et al. (2016)
AY_PAREN_RE = re.compile(r"\(([^()]*?(?:19|20)\d{2}[a-z]?[^()]*?)\)")
AY_SEG_RE = re.compile(r"([A-Z][A-Za-z\-']+)[^;()]*?((?:19|20)\d{2})([a-z])?")
AY_NARR_RE = re.compile(
    r"([A-Z][A-Za-z\-']+)(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z\-']+)?"
    r"\s*\(((?:19|20)\d{2})([a-z])?\)"
)


def _in_ref_region(pno, rects, head, end):
    """참고문헌 목록 내부인가 (목록 뒤 부록의 인용은 본문 인용으로 취급)."""
    if not head or not rects:
        return False
    p, y = pno + 1, rects[0][1]
    after_head = p > head["page"] or (p == head["page"] and y >= head["y"])
    if not after_head:
        return False
    if not end:
        return True
    return p < end["page"] or (p == end["page"] and y <= end["y"] + 0.01)


def detect_citations(doc, refs: dict):
    """본문 인용 표기를 좌표와 함께 탐지 (숫자/author-year 스타일 자동)."""
    head = refs.get("head")
    end = refs.get("end")
    citations = []

    if refs.get("style") == "ay":
        # (성 소문자, 연도) → 등장 순 항목 번호 목록
        ay_index = {}
        for e in refs["entries"]:
            key = (e["ay"]["surname"].lower(), e["ay"]["year"])
            ay_index.setdefault(key, []).append(e["n"])

        def resolve(surname, year, suffix):
            lst = ay_index.get((surname.lower(), year), [])
            if not lst:
                return None
            if suffix:
                idx = ord(suffix) - 97
                return lst[idx] if idx < len(lst) else lst[0]
            return lst[0]

        for pno in range(len(doc)):
            page = doc[pno]
            words, text, spans = _words_and_text(page)
            if not words:
                continue
            pw, ph = page.rect.width, page.rect.height
            word_starts = [s[0] for s in spans]
            found = []  # (start, end, nums)
            for m in AY_PAREN_RE.finditer(text):
                nums = []
                for seg in m.group(1).split(";"):
                    sm = AY_SEG_RE.search(seg)
                    if sm:
                        n = resolve(sm.group(1), sm.group(2), sm.group(3))
                        if n and n not in nums:
                            nums.append(n)
                if nums:
                    found.append((m.start(), m.end(), nums))
            for m in AY_NARR_RE.finditer(text):
                n = resolve(m.group(1), m.group(2), m.group(3))
                if n:
                    found.append((m.start(), m.end(), [n]))
            for start, stop, nums in found:
                rects = _range_to_rects(words, spans, word_starts, start, stop, pw, ph)
                if rects and not _in_ref_region(pno, rects, head, end):
                    citations.append({"page": pno + 1, "nums": nums, "rects": rects})
        return citations

    # 숫자 스타일 [N], [N-M], [N, M]
    valid_nums = {e["n"] for e in refs["entries"]}
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
            if rects and not _in_ref_region(pno, rects, head, end):
                citations.append({"page": pno + 1, "nums": nums, "rects": rects})
    return citations


def ensure_citations(paper_dir: Path) -> dict:
    """citations.json / references.json 을 (없으면 생성 후) 반환.

    기존에 파싱된 논문도 처음 조회 시 자동 생성된다(lazy migration).
    """
    cf = paper_dir / "citations.json"
    rf = paper_dir / "references.json"
    if cf.exists() and rf.exists():
        refs_cached = json.loads(rf.read_text())
        # 구버전 캐시(style/end 필드 없음)는 재파싱
        if "style" in refs_cached and "end" in refs_cached:
            return {
                "citations": json.loads(cf.read_text()),
                "references": refs_cached,
            }
    doc = pymupdf.open(paper_dir / "paper.pdf")
    try:
        refs = parse_references(doc)
        if refs:
            cites = detect_citations(doc, refs)
        else:
            refs = {"style": "none", "head": None, "entries": []}
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


def find_citing_sentences(fulltext: str, entry: dict, limit: int = 4):
    """본문에서 해당 참고문헌을 인용한 문장들 (숫자/author-year 스타일 모두)."""
    ay = entry.get("ay")
    entry_head = entry.get("text", "")[:25]

    if ay:
        # "Vaswani et al., 2017" — et al.의 마침표 때문에 문장 분리가 깨지므로
        # 전문에서 (성 … 연도) 매치 주변 문맥을 잘라서 사용
        pat = re.compile(
            re.escape(ay["surname"]) + r"[^);\n]{0,60}?" + ay["year"]
        )
        out = []
        for m in pat.finditer(fulltext):
            s = max(0, m.start() - 180)
            e = min(len(fulltext), m.end() + 120)
            ctx = " ".join(fulltext[s:e].split())
            if entry_head and entry_head in ctx:  # 참고문헌 항목 자체 제외
                continue
            out.append(("…" + ctx + "…")[:350])
            if len(out) >= limit:
                break
        return out

    out = []
    for raw in re.split(r"(?<=[.!?])\s+", fulltext):
        line = raw.strip()
        if ENTRY_RE.match(line):  # 참고문헌 항목 자체는 제외
            continue
        for m in CITE_RE.finditer(line):
            if entry["n"] in _expand_nums(m.group(1)):
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
