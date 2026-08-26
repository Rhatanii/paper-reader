/* Paper Reader frontend */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

/* ───────────────────────── 공통 유틸 ───────────────────────── */

async function jfetch(url, opts = {}) {
  const resp = await fetch(url, {
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return resp.json();
}

/* NDJSON 스트림 소비. onEvent(ev) 호출, 완료 시 resolve */
async function streamPost(url, body, onEvent) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; }
      if (ev.type === "error") throw new Error(ev.message || "오류");
      onEvent(ev);
    }
  }
}

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* 미니 마크다운 렌더러 (헤딩/볼드/이탤릭/코드/리스트/인용/표/링크) */
function md(src) {
  const lines = src.split("\n");
  const out = [];
  let i = 0;
  const inline = (s) =>
    esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');

  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {                       // 코드블록
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {             // 리스트
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<${ordered ? "ol" : "ul"}>${items.join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }
    if (line.startsWith(">")) {
      const buf = [];
      while (i < lines.length && lines[i].startsWith(">"))
        buf.push(inline(lines[i++].replace(/^>\s?/, "")));
      out.push(`<blockquote>${buf.join("<br>")}</blockquote>`);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
      const rows = [];
      const parse = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const head = parse(line);
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(parse(lines[i++]));
      out.push(
        `<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead>` +
        `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`
      );
      continue;
    }
    if (line.trim() === "") { i++; continue; }
    const buf = [];                                     // 문단
    while (i < lines.length && lines[i].trim() !== "" &&
           !/^(#{1,4}\s|```|>|\s*([-*]|\d+\.)\s|\s*\|.*\|\s*$)/.test(lines[i]))
      buf.push(inline(lines[i++]));
    if (buf.length) out.push(`<p>${buf.join("<br>")}</p>`);
  }
  return out.join("\n");
}

const spinner = (msg) => `<div class="working"><span class="spinner"></span>${esc(msg)}</div>`;

/* ───────────────────────── 전역 상태 ───────────────────────── */

const state = {
  models: [],
  model: localStorage.getItem("pr-model") || null,
  paper: null,          // 현재 열린 논문 detail
  translations: {},     // page → [{i, src, ko}]
  transPage: null,      // 번역 패널에 표시 중인 페이지
  chatBusy: false,
  sentences: {},        // page → 문장 배열 (팝오버 미리보기용)
  highlights: {},       // "page-senti" → {id, page, sent_i, text, note}
  notion: null,         // Notion 상태 캐시
  citations: [],        // [{page, nums, rects}]
  citesByPage: {},      // page → 인용 목록 (hitTest용 인덱스)
  lines: {},            // page → 텍스트 레이어 라인 캐시
  refs: {},             // n → 참고문헌 항목 {n, text, page, rect}
  refSummaries: {},     // n → 요약 md 캐시
};

const modelSelect = $("#model-select");
function currentModel() { return modelSelect.value; }

/* ───────────────────────── 라이브러리 ───────────────────────── */

const libraryView = $("#library-view");
const readerView = $("#reader-view");
const statusEl = $("#upload-status");

function setStatus(msg, isError = false) {
  statusEl.hidden = !msg;
  statusEl.textContent = msg || "";
  statusEl.classList.toggle("error", isError);
}

async function loadLibrary() {
  const { papers } = await jfetch("/api/papers");
  const grid = $("#paper-grid");
  grid.innerHTML = "";
  for (const p of papers) {
    const card = document.createElement("div");
    card.className = "paper-card";
    card.innerHTML = `
      <div class="t">${esc(p.title)}</div>
      <div class="m">${p.npages} 페이지${p.created ? " · " + new Date(p.created * 1000).toLocaleDateString("ko-KR") : ""}</div>
      <button class="del" title="삭제">✕</button>`;
    card.addEventListener("click", () => openPaper(p.id));
    $(".del", card).addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${p.title.slice(0, 40)}…" 삭제할까요?`)) return;
      await jfetch(`/api/papers/${p.id}`, { method: "DELETE" });
      loadLibrary();
    });
    grid.appendChild(card);
  }
}

async function uploadFile(file) {
  setStatus(`「${file.name}」 업로드 및 파싱 중…`);
  const fd = new FormData();
  fd.append("file", file);
  try {
    const resp = await fetch("/api/papers", { method: "POST", body: fd });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const meta = await resp.json();
    setStatus("");
    await loadLibrary();
    openPaper(meta.id);
  } catch (e) {
    setStatus(`업로드 실패: ${e.message}`, true);
  }
}

{
  const dz = $("#dropzone");
  const fi = $("#file-input");
  dz.addEventListener("click", () => fi.click());
  fi.addEventListener("change", () => fi.files[0] && uploadFile(fi.files[0]));
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    const f = e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });

  $("#url-btn").addEventListener("click", importUrl);
  $("#url-input").addEventListener("keydown", (e) => e.key === "Enter" && importUrl());
  async function importUrl() {
    const url = $("#url-input").value.trim();
    if (!url) return;
    setStatus("URL에서 PDF 다운로드 및 파싱 중…");
    try {
      const meta = await jfetch("/api/papers/from-url", { method: "POST", body: { url } });
      setStatus("");
      $("#url-input").value = "";
      await loadLibrary();
      openPaper(meta.id);
    } catch (e) {
      setStatus(`가져오기 실패: ${e.message}`, true);
    }
  }
}

/* ───────────────────────── 리더: 페이지 렌더링 ───────────────────────── */

const pagesEl = $("#pages");

async function openPaper(pid) {
  state.paper = await jfetch(`/api/papers/${pid}`);
  state.translations = {};
  state.transPage = null;
  state.sentences = {};
  state.highlights = {};
  state.citations = [];
  state.citesByPage = {};
  state.lines = {};
  state.refs = {};
  state.refSummaries = {};
  libraryView.hidden = true;
  readerView.hidden = false;
  $("#paper-title").textContent = state.paper.title;
  await loadCitations();   // 오버레이 생성 전에 인용 좌표 확보
  buildPages();
  resetPanels();
  await Promise.all([loadSummaryCached(), loadChatHistory(), loadHighlights()]);
  switchTab("summary");
  refreshNotion();
}

$("#back-btn").addEventListener("click", () => {
  readerView.hidden = true;
  libraryView.hidden = false;
  pagesEl.innerHTML = "";
  state.paper = null;
  loadLibrary();
});

/* 페이지 가상화: 화면 근처(600px)에서 마운트, 멀어지면(2400px 밖) 언마운트.
   대형 논문(100p+)에서 디코딩된 이미지·DOM이 수백 MB~GB로 쌓이는 것을 방지. */
let loadObs = null;
let unloadObs = null;

function buildPages() {
  pagesEl.innerHTML = "";
  loadObs?.disconnect();
  unloadObs?.disconnect();
  loadObs = new IntersectionObserver((entries) => {
    for (const en of entries)
      if (en.isIntersecting) {
        loadObs.unobserve(en.target);
        mountPage(en.target);
      }
  }, { root: pagesEl, rootMargin: "600px" });
  unloadObs = new IntersectionObserver((entries) => {
    for (const en of entries)
      if (!en.isIntersecting && en.target._mounted) {
        unmountPage(en.target);
        loadObs.observe(en.target);
      }
  }, { root: pagesEl, rootMargin: "2400px" });

  for (const pg of state.paper.pages) {
    const wrap = document.createElement("div");
    wrap.className = "page-wrap";
    wrap.dataset.page = pg.n;
    wrap.style.aspectRatio = `${pg.w} / ${pg.h}`;
    const translated = state.paper.translated_pages.includes(pg.n);
    wrap.innerHTML = `
      <div class="page-skeleton">p.${pg.n}</div>
      <div class="page-toolbar">
        <button class="pt-btn tr-btn ${translated ? "done" : ""}">${translated ? "✓ 번역됨" : "🌐 번역"}</button>
      </div>
      <div class="page-no">p.${pg.n}</div>`;
    $(".tr-btn", wrap).addEventListener("click", () => translatePage(pg.n));
    attachPageEvents(wrap, pg.n);
    pagesEl.appendChild(wrap);
    loadObs.observe(wrap);
    unloadObs.observe(wrap);
  }
}

async function mountPage(wrap) {
  if (wrap._mounted) return;
  wrap._mounted = true;
  const n = +wrap.dataset.page;
  const img = new Image();
  img.src = `/api/papers/${state.paper.id}/page/${n}.png`;
  img.addEventListener("load", () => $(".page-skeleton", wrap)?.remove());
  wrap.prepend(img);
  await buildOverlays(wrap, n);
}

function unmountPage(wrap) {
  wrap._mounted = false;
  if (wrap._ro) {
    wrap._ro.disconnect();
    wrap._ro = null;
  }
  $$("img, .text-layer, .sent-rect, .cite-rect, .fig-chip, .flash-rect", wrap)
    .forEach((el) => el.remove());
  if (!$(".page-skeleton", wrap)) {
    const sk = document.createElement("div");
    sk.className = "page-skeleton";
    sk.textContent = `p.${wrap.dataset.page}`;
    wrap.appendChild(sk);
  }
}

const rectCss = ([x0, y0, x1, y1]) =>
  `left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%`;

/* 마운트 시 오버레이 DOM 생성 — 데이터는 state에 캐시해 재마운트 때 refetch 없음 */
async function buildOverlays(wrap, n) {
  if (!state.sentences[n]) {
    try {
      state.sentences[n] = await jfetch(`/api/papers/${state.paper.id}/page/${n}/sentences`);
    } catch {
      state.sentences[n] = [];
    }
  }
  if (!wrap._mounted) return;   // 로드 중 언마운트되면 중단

  const frag = document.createDocumentFragment();
  for (const s of state.sentences[n]) {
    const underlined = !!state.highlights[`${n}-${s.i}`];
    for (const r of s.rects) {
      const d = document.createElement("div");
      d.className = "sent-rect" + (underlined ? " underlined" : "");
      d.dataset.s = s.i;
      d.style.cssText = rectCss(r);
      frag.appendChild(d);
    }
  }
  for (const c of state.citesByPage[n] || []) {
    for (const r of c.rects) {
      const d = document.createElement("div");
      d.className = "cite-rect";
      d.style.cssText = rectCss(r);
      frag.appendChild(d);
    }
  }
  for (const fig of state.paper.figures.filter((f) => f.page === n)) {
    const chip = document.createElement("button");
    chip.className = "fig-chip";
    chip.textContent = `✨ ${fig.label} 설명`;
    chip.style.left = `${fig.rect[0] * 100}%`;
    chip.style.top = `${fig.rect[1] * 100}%`;
    chip.addEventListener("click", () => explainFigure(fig));
    frag.appendChild(chip);
  }
  wrap.appendChild(frag);

  buildTextLayer(wrap, n);

  // 번역된 페이지면 캐시를 불러와 핵심 문장 형광펜
  if (state.translations[n]) {
    applyKeyMarks(n);
  } else if (state.paper.translated_pages.includes(n)) {
    jfetch(`/api/papers/${state.paper.id}/pages/${n}/translation`)
      .then(({ translation }) => {
        if (translation) {
          state.translations[n] = translation;
          applyKeyMarks(n);
        }
      })
      .catch(() => {});
  }
}

/* 좌표 기반 hover/클릭 — wrap당 1회만 등록 (재마운트에도 중복 없음) */
function attachPageEvents(wrap, n) {
  const hitTest = (ev) => {
    const rc = wrap.getBoundingClientRect();
    const nx = (ev.clientX - rc.left) / rc.width;
    const ny = (ev.clientY - rc.top) / rc.height;
    const inRect = (r) => nx >= r[0] && nx <= r[2] && ny >= r[1] && ny <= r[3];
    const cite = (state.citesByPage[n] || []).find((c) => c.rects.some(inRect));
    const sent = cite ? null
      : (state.sentences[n] || []).find((s) => s.rects.some(inRect));
    return { cite, sent };
  };

  let hoverSi = null;
  let hoverCite = null;
  let citeTimer = null;
  let raf = null;
  wrap.addEventListener("mousemove", (e) => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = null;
      if (e.buttons) return;               // 텍스트 드래그 중엔 hover 동작 없음
      const { cite, sent } = hitTest(e);
      wrap.style.cursor = cite ? "pointer" : "";

      // 인용 위에 잠시 머물면 팝업 자동 오픈
      if (cite) {
        cancelRefAutoClose();
        if (cite !== hoverCite) {
          hoverCite = cite;
          clearTimeout(citeTimer);
          citeTimer = setTimeout(() => {
            if (hoverCite === cite) openRefPopover(cite.nums, e, true);
          }, 350);
        }
      } else {
        hoverCite = null;
        clearTimeout(citeTimer);
        scheduleRefAutoClose();
      }

      const si = sent ? sent.i : null;
      if (si !== hoverSi) {
        if (hoverSi != null) highlightSentence(n, hoverSi, false);
        if (si != null) highlightSentence(n, si, true);
        hoverSi = si;
      }
    });
  });
  wrap.addEventListener("mouseleave", () => {
    if (hoverSi != null) highlightSentence(n, hoverSi, false);
    hoverSi = null;
    hoverCite = null;
    clearTimeout(citeTimer);
    scheduleRefAutoClose();
    wrap.style.cursor = "";
  });

  wrap.addEventListener("click", (e) => {
    if (window.getSelection().toString()) return;         // 드래그 선택 직후 클릭 무시
    if (e.target.closest(".fig-chip, .page-toolbar")) return;
    const { cite, sent } = hitTest(e);
    if (cite) {
      e.stopPropagation();
      openRefPopover(cite.nums, e);
      return;
    }
    if (sent) {
      e.stopPropagation();
      openHlPopover(n, sent.i, e);
    }
  });
}

/* 투명 텍스트 레이어 — 라인 span을 원본 위치에 맞춰 배치 */
async function buildTextLayer(wrap, n) {
  if (!state.lines[n]) {
    try {
      state.lines[n] = await jfetch(`/api/papers/${state.paper.id}/page/${n}/lines`);
    } catch {
      state.lines[n] = [];
    }
  }
  if (!wrap._mounted || $(".text-layer", wrap)) return;

  const layer = document.createElement("div");
  layer.className = "text-layer";
  const spans = [];
  for (const ln of state.lines[n]) {
    const s = document.createElement("span");
    s.textContent = ln.text;
    s.style.left = `${ln.rect[0] * 100}%`;
    s.style.top = `${ln.rect[1] * 100}%`;
    layer.appendChild(s);
    spans.push([s, ln.rect]);
    const br = document.createElement("span");   // 복사 시 라인 사이 개행 유지
    br.className = "tl-br";
    br.textContent = "\n";
    layer.appendChild(br);
  }
  wrap.appendChild(layer);

  const fit = () => {
    const w = wrap.clientWidth;
    const h = wrap.clientHeight;
    if (!w || !h) return;
    for (const [s, r] of spans) {              // 1패스: 폰트 크기
      s.style.fontSize = `${Math.max(4, (r[3] - r[1]) * h * 0.9)}px`;
      s.style.transform = "";
    }
    const widths = spans.map(([s]) => s.offsetWidth);   // 2패스: 측정
    spans.forEach(([s, r], i) => {             // 3패스: 폭 보정
      if (widths[i] > 0)
        s.style.transform = `scaleX(${((r[2] - r[0]) * w) / widths[i]})`;
    });
  };
  fit();
  let debounce = null;
  wrap._ro = new ResizeObserver(() => {   // 패널 드래그 중 과도한 재계산 방지
    clearTimeout(debounce);
    debounce = setTimeout(fit, 120);
  });
  wrap._ro.observe(wrap);
}

/* 번역의 문단별 핵심 문장 → 원문/번역 양쪽 형광펜 */
function applyKeyMarks(n) {
  const keys = new Set(
    (state.translations[n] || []).filter((it) => it.key).map((it) => it.i)
  );
  const wrap = $(`.page-wrap[data-page="${n}"]`);
  if (wrap)
    $$(".sent-rect", wrap).forEach((d) =>
      d.classList.toggle("key-sent", keys.has(+d.dataset.s)));
  if (state.transPage === n)
    $$("#translation-body .t-sent").forEach((li) =>
      li.classList.toggle("key-t", keys.has(+li.dataset.s)));
}

/* 원문 rect ↔ 번역 문장 상호 하이라이트 */
function highlightSentence(page, si, on, { scrollPanel = true } = {}) {
  const wrap = $(`.page-wrap[data-page="${page}"]`);
  if (wrap)
    $$(`.sent-rect[data-s="${si}"]`, wrap).forEach((d) => d.classList.toggle("hl", on));
  if (state.transPage === page) {
    const li = $(`#translation-body .t-sent[data-s="${si}"]`);
    if (li) {
      li.classList.toggle("hl", on);
      if (on && scrollPanel) li.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
}

/* ───────────────────────── 패널 공통 ───────────────────────── */

const panelViews = {
  summary: $("#view-summary"),
  translation: $("#view-translation"),
  highlights: $("#view-highlights"),
  chat: $("#view-chat"),
  figure: $("#view-figure"),
};

function switchTab(name) {
  for (const [k, el] of Object.entries(panelViews)) el.hidden = k !== name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
}
$$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
$("#figure-close").addEventListener("click", () => switchTab("summary"));

function resetPanels() {
  $("#summary-body").innerHTML = "";
  $("#summary-btn").hidden = false;
  $("#summary-regen").hidden = true;
  $("#hl-count").textContent = "";
  $("#highlights-body").innerHTML = "";
  $("#translation-body").innerHTML =
    `<div class="placeholder">각 페이지 왼쪽 상단의 <b>「번역」</b> 버튼을 누르면<br>이곳에 한글 번역이 표시됩니다.</div>`;
  $("#trans-page-label").textContent = "";
  $("#chat-body").innerHTML = "";
  $("#figure-body").innerHTML = "";
}

/* ───────────────────────── 요약 ───────────────────────── */

async function loadSummaryCached() {
  const { summary } = await jfetch(`/api/papers/${state.paper.id}/summary`);
  if (summary) {
    $("#summary-body").innerHTML = md(summary);
    $("#summary-btn").hidden = true;
    $("#summary-regen").hidden = false;
  }
}

async function generateSummary() {
  const body = $("#summary-body");
  const btn = $("#summary-btn");
  btn.disabled = true;
  $("#summary-regen").hidden = true;
  body.innerHTML = spinner("논문 전체를 읽고 개요를 작성하는 중… (모델에 따라 1~3분)");
  let acc = "";
  let raf = null;
  try {
    await streamPost(`/api/papers/${state.paper.id}/summary`, { model: currentModel() }, (ev) => {
      if (ev.type !== "delta") return;
      acc += ev.text;
      if (!raf) raf = requestAnimationFrame(() => { body.innerHTML = md(acc); raf = null; });
    });
    body.innerHTML = md(acc);
    btn.hidden = true;
    $("#summary-regen").hidden = false;
    refreshNotionSoon();
  } catch (e) {
    body.innerHTML = `<div class="placeholder">요약 생성 실패<br><span class="dim">${esc(e.message)}</span></div>`;
  } finally {
    btn.disabled = false;
  }
}
$("#summary-btn").addEventListener("click", generateSummary);
$("#summary-regen").addEventListener("click", generateSummary);

/* ───────────────────────── 번역 ───────────────────────── */

async function translatePage(n) {
  switchTab("translation");
  const body = $("#translation-body");
  $("#trans-page-label").textContent = `— p.${n}`;

  if (!state.translations[n]) {
    body.innerHTML = spinner(`p.${n} 번역 중… (문장 수에 따라 수십 초)`);
    try {
      const { translation } = await jfetch(
        `/api/papers/${state.paper.id}/pages/${n}/translate`,
        { method: "POST", body: { model: currentModel() } }
      );
      state.translations[n] = translation;
      const btn = $(`.page-wrap[data-page="${n}"] .tr-btn`);
      if (btn) { btn.textContent = "✓ 번역됨"; btn.classList.add("done"); }
    } catch (e) {
      body.innerHTML = `<div class="placeholder">번역 실패<br><span class="dim">${esc(e.message)}</span></div>`;
      return;
    }
  }

  state.transPage = n;
  renderTranslation(n);
  applyKeyMarks(n);
  maybeBackfillKeys(n);
}

/* 예전 번역 캐시에 핵심 문장 정보가 없으면 재번역 없이 백그라운드로 추출 */
function maybeBackfillKeys(n) {
  const items = state.translations[n];
  if (!items || !items.length || items.some((it) => "key" in it)) return;
  jfetch(`/api/papers/${state.paper.id}/pages/${n}/keys`, {
    method: "POST",
    body: { model: currentModel() },
  })
    .then(({ translation }) => {
      if (!translation) return;
      state.translations[n] = translation;
      applyKeyMarks(n);
    })
    .catch(() => {});
}

function renderTranslation(n) {
  const body = $("#translation-body");
  const items = state.translations[n];
  if (!items || !items.length) {
    body.innerHTML = `<div class="placeholder">이 페이지에서 번역할 문장을 찾지 못했습니다.</div>`;
    return;
  }
  body.innerHTML = "";
  for (const it of items) {
    const div = document.createElement("div");
    div.className = "t-sent"
      + (state.highlights[`${n}-${it.i}`] ? " underlined-t" : "")
      + (it.key ? " key-t" : "");
    div.dataset.s = it.i;
    div.innerHTML = `<span class="no">${it.i + 1}</span>${esc(it.ko)}`;
    div.title = it.src;
    div.addEventListener("mouseenter", () => highlightSentence(n, it.i, true, { scrollPanel: false }));
    div.addEventListener("mouseleave", () => highlightSentence(n, it.i, false));
    div.addEventListener("click", () => {
      const rect = $(`.page-wrap[data-page="${n}"] .sent-rect[data-s="${it.i}"]`);
      rect?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    body.appendChild(div);
  }
}

/* ───────────────────────── Figure 설명 ───────────────────────── */

async function explainFigure(fig) {
  switchTab("figure");
  $("#figure-label").textContent = `${fig.label} (p.${fig.page})`;
  const body = $("#figure-body");
  body.innerHTML = spinner(`${fig.label}을(를) 분석하는 중…`);
  let acc = "";
  let raf = null;
  try {
    await streamPost(
      `/api/papers/${state.paper.id}/figures/${fig.id}/explain`,
      { model: currentModel() },
      (ev) => {
        if (ev.type !== "delta") return;
        acc += ev.text;
        if (!raf) raf = requestAnimationFrame(() => { body.innerHTML = md(acc); raf = null; });
      }
    );
    body.innerHTML = md(acc);
  } catch (e) {
    body.innerHTML = `<div class="placeholder">설명 생성 실패<br><span class="dim">${esc(e.message)}</span></div>`;
  }
}

/* ───────────────────────── 채팅 ───────────────────────── */

const chatBody = $("#chat-body");
const chatInput = $("#chat-input");
const chatSend = $("#chat-send");
const chatJump = $("#chat-jump");

/* 사용자가 맨 아래 근처에 있을 때만 자동 스크롤 (위로 올려두면 그대로 유지) */
const chatNearBottom = () =>
  chatBody.scrollHeight - chatBody.scrollTop - chatBody.clientHeight < 80;

function updateChatJump() {
  if (state.chatBusy && !chatNearBottom()) {
    chatJump.style.bottom = `${$(".chat-input-row").offsetHeight + 18}px`;
    chatJump.hidden = false;
  } else {
    chatJump.hidden = true;
  }
}
chatJump.addEventListener("click", () => {
  chatBody.scrollTop = chatBody.scrollHeight;
  chatJump.hidden = true;
});
chatBody.addEventListener("scroll", updateChatJump);

function addBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  if (role === "user") div.textContent = text;
  else div.innerHTML = md(text);
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
  return div;
}

async function loadChatHistory() {
  const { messages } = await jfetch(`/api/papers/${state.paper.id}/chat`);
  chatBody.innerHTML = messages.length
    ? ""
    : `<div class="placeholder">이 논문에 대해 무엇이든 물어보세요.<br>Claude가 논문 전문과 페이지 이미지를 참고해 답합니다.</div>`;
  for (const m of messages) addBubble(m.role, m.content);
}

async function sendChat() {
  const text = chatInput.value.trim();
  if (!text || state.chatBusy) return;
  state.chatBusy = true;
  chatSend.disabled = true;
  chatInput.value = "";
  $(".placeholder", chatBody)?.remove();
  addBubble("user", text);
  const bubble = addBubble("assistant", "");
  bubble.innerHTML = spinner("생각 중…");

  let acc = "";
  let raf = null;
  try {
    await streamPost(
      `/api/papers/${state.paper.id}/chat`,
      { message: text, model: currentModel() },
      (ev) => {
        if (ev.type !== "delta") return;
        acc += ev.text;
        if (!raf) raf = requestAnimationFrame(() => {
          const stick = chatNearBottom();   // 갱신 전 위치 기준으로 판단
          bubble.innerHTML = md(acc);
          if (stick) chatBody.scrollTop = chatBody.scrollHeight;
          else updateChatJump();
          raf = null;
        });
      }
    );
    const stick = chatNearBottom();
    bubble.innerHTML = md(acc || "(응답 없음)");
    if (stick) chatBody.scrollTop = chatBody.scrollHeight;
  } catch (e) {
    bubble.innerHTML = `<span class="dim">오류: ${esc(e.message)}</span>`;
  } finally {
    state.chatBusy = false;
    chatSend.disabled = false;
    chatJump.hidden = true;
    chatInput.focus();
    refreshNotionSoon();
  }
}

chatSend.addEventListener("click", sendChat);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendChat();
  }
});
$("#chat-reset").addEventListener("click", async () => {
  if (!confirm("대화 기록을 삭제하고 새로 시작할까요?")) return;
  await jfetch(`/api/papers/${state.paper.id}/chat/reset`, { method: "POST" });
  loadChatHistory();
});

/* ───────────────────────── 밑줄(하이라이트) ───────────────────────── */

async function loadHighlights() {
  const { highlights } = await jfetch(`/api/papers/${state.paper.id}/highlights`);
  setHighlights(highlights);
}

function setHighlights(list) {
  state.highlights = {};
  for (const h of list) state.highlights[h.id] = h;
  applyHighlightClasses();
  renderHighlightsTab();
}

function applyHighlightClasses() {
  $$(".page-wrap .sent-rect").forEach((d) => {
    const key = `${d.closest(".page-wrap").dataset.page}-${d.dataset.s}`;
    d.classList.toggle("underlined", !!state.highlights[key]);
  });
  if (state.transPage != null)
    $$("#translation-body .t-sent").forEach((li) =>
      li.classList.toggle("underlined-t", !!state.highlights[`${state.transPage}-${li.dataset.s}`]));
}

function renderHighlightsTab() {
  const body = $("#highlights-body");
  const items = Object.values(state.highlights)
    .sort((a, b) => a.page - b.page || a.sent_i - b.sent_i);
  $("#hl-count").textContent = items.length ? `(${items.length})` : "";
  if (!items.length) {
    body.innerHTML = `<div class="placeholder">페이지에서 <b>문장을 클릭</b>하면 밑줄을 긋고<br>메모를 남길 수 있습니다.<br><br>밑줄과 메모는 Notion 논문 로그에도 기록됩니다.</div>`;
    return;
  }
  body.innerHTML = "";
  for (const h of items) {
    const div = document.createElement("div");
    div.className = "hl-item";
    div.innerHTML =
      `<div class="tx"><span class="pg">p.${h.page}</span>${esc(h.text)}</div>` +
      (h.note ? `<div class="nt">📝 ${esc(h.note)}</div>` : "") +
      `<button class="hl-del" title="밑줄 삭제">✕</button>`;
    div.addEventListener("click", () => {
      $(`.page-wrap[data-page="${h.page}"] .sent-rect[data-s="${h.sent_i}"]`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    $(".hl-del", div).addEventListener("click", async (e) => {
      e.stopPropagation();
      const { highlights } = await jfetch(
        `/api/papers/${state.paper.id}/highlights/${h.id}`, { method: "DELETE" });
      setHighlights(highlights);
    });
    body.appendChild(div);
  }
}

/* 팝오버 */
const hlPopover = $("#hl-popover");
let hlTarget = null;   // {page, si}

function openHlPopover(page, si, ev) {
  hlTarget = { page, si };
  const existing = state.highlights[`${page}-${si}`];
  const sent = (state.sentences[page] || []).find((s) => s.i === si);
  $("#hl-popover-text").textContent = (sent?.text || "").slice(0, 180);
  $("#hl-note").value = existing?.note || "";
  $("#hl-remove").hidden = !existing;
  $("#hl-save").textContent = existing ? "메모 저장" : "밑줄 저장";
  hlPopover.hidden = false;
  const w = 330, h = hlPopover.offsetHeight || 160;
  let x = Math.min(ev.clientX, innerWidth - w - 12);
  let y = ev.clientY + 12;
  if (y + h > innerHeight - 10) y = Math.max(10, ev.clientY - h - 12);
  hlPopover.style.left = `${Math.max(10, x)}px`;
  hlPopover.style.top = `${y}px`;
  $("#hl-note").focus();
}

function closeHlPopover() {
  hlPopover.hidden = true;
  hlTarget = null;
}

$("#hl-save").addEventListener("click", async () => {
  if (!hlTarget) return;
  const btn = $("#hl-save");
  btn.disabled = true;
  try {
    const { highlights } = await jfetch(
      `/api/papers/${state.paper.id}/highlights`,
      { method: "POST", body: { page: hlTarget.page, sent_i: hlTarget.si, note: $("#hl-note").value.trim() } });
    setHighlights(highlights);
    closeHlPopover();
    refreshNotionSoon();
  } catch (e) {
    alert("저장 실패: " + e.message);
  } finally {
    btn.disabled = false;
  }
});
$("#hl-remove").addEventListener("click", async () => {
  if (!hlTarget) return;
  const { highlights } = await jfetch(
    `/api/papers/${state.paper.id}/highlights/${hlTarget.page}-${hlTarget.si}`,
    { method: "DELETE" });
  setHighlights(highlights);
  closeHlPopover();
});
$("#hl-cancel").addEventListener("click", closeHlPopover);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeHlPopover(); closeRefPopover(); }
});
document.addEventListener("click", (e) => {
  if (!hlPopover.hidden && !hlPopover.contains(e.target) && !e.target.closest(".sent-rect"))
    closeHlPopover();
});

/* ───────────────────────── 인용/참고문헌 ───────────────────────── */

async function loadCitations() {
  try {
    const d = await jfetch(`/api/papers/${state.paper.id}/citations`);
    state.citations = d.citations || [];
    for (const e of (d.references && d.references.entries) || [])
      state.refs[e.n] = e;
  } catch { /* 인용 파싱 실패 시 기능만 비활성 */ }
  state.citesByPage = {};
  for (const c of state.citations)
    (state.citesByPage[c.page] = state.citesByPage[c.page] || []).push(c);
}

const refPopover = $("#ref-popover");
let refSeq = 0;   // 탭 전환/닫기 후 늦게 도착한 스트림이 DOM을 덮지 않게 하는 토큰
let refPinned = false;      // 클릭으로 열었거나 팝업 안을 조작하면 자동 닫힘 없음
let refCloseTimer = null;

function cancelRefAutoClose() {
  clearTimeout(refCloseTimer);
}
function scheduleRefAutoClose() {
  if (refPopover.hidden || refPinned) return;
  clearTimeout(refCloseTimer);
  refCloseTimer = setTimeout(() => {
    if (!refPinned) closeRefPopover();
  }, 550);
}
refPopover.addEventListener("mouseenter", cancelRefAutoClose);
refPopover.addEventListener("mouseleave", scheduleRefAutoClose);
refPopover.addEventListener("mousedown", () => {
  refPinned = true;
  cancelRefAutoClose();
});

function openRefPopover(nums, ev, hover = false) {
  refPinned = !hover;
  cancelRefAutoClose();
  const tabs = $("#ref-tabs");
  tabs.innerHTML = "";
  for (const n of nums) {
    const b = document.createElement("button");
    b.className = "rp-tab";
    b.dataset.n = n;
    b.textContent = (state.refs[n] && state.refs[n].label) || `[${n}]`;
    b.addEventListener("click", () => selectRef(n));
    tabs.appendChild(b);
  }
  refPopover.hidden = false;
  const w = 460, h = Math.min(innerHeight * 0.72, 540);
  const x = Math.max(10, Math.min(ev.clientX, innerWidth - w - 12));
  let y = ev.clientY + 12;
  if (y + h > innerHeight - 10) y = Math.max(10, innerHeight - h - 12);
  refPopover.style.left = `${x}px`;
  refPopover.style.top = `${y}px`;
  selectRef(nums[0]);
}

async function selectRef(n) {
  const seq = ++refSeq;
  $$(".rp-tab").forEach((t) => t.classList.toggle("active", +t.dataset.n === n));
  const entry = state.refs[n];
  const img = $("#ref-img");
  const sumEl = $("#ref-summary");
  if (!entry) {
    img.removeAttribute("src");
    $("#ref-loc").textContent = `[${n}] 항목을 찾지 못했습니다`;
    sumEl.innerHTML = "";
    return;
  }
  img.src = `/api/papers/${state.paper.id}/refs/${n}.png`;
  $("#ref-loc").textContent = `References · p.${entry.page}${entry.continues ? " (다음 페이지로 이어짐)" : ""}`;
  $("#ref-goto").onclick = () => { closeRefPopover(); gotoRef(entry); };

  // 요약은 자동 실행하지 않는다 — 캐시가 있으면 보여주고, 없으면 버튼만
  if (state.refSummaries[n]) {
    renderRefSummary(n, state.refSummaries[n]);
    return;
  }
  sumEl.innerHTML = "";
  try {
    const { summary } = await jfetch(`/api/papers/${state.paper.id}/refs/${n}/summary`);
    if (seq !== refSeq) return;
    if (summary) {
      state.refSummaries[n] = summary;
      renderRefSummary(n, summary);
      return;
    }
  } catch {}
  if (seq === refSeq) showRefSummaryButton(n);
}

function showRefSummaryButton(n) {
  const sumEl = $("#ref-summary");
  sumEl.innerHTML = "";
  const btn = document.createElement("button");
  btn.className = "btn primary small";
  btn.textContent = "✨ 핵심 기여 3줄 요약";
  btn.addEventListener("click", () => runRefSummary(n));
  sumEl.appendChild(btn);
}

function renderRefSummary(n, text) {
  const sumEl = $("#ref-summary");
  sumEl.innerHTML = md(text);
  const regen = document.createElement("button");
  regen.className = "btn ghost small";
  regen.textContent = "다시 요약";
  regen.addEventListener("click", () => runRefSummary(n, true));
  sumEl.appendChild(regen);
}

async function runRefSummary(n, force = false) {
  const seq = refSeq;   // 탭 전환/닫기 시 늦은 스트림이 DOM을 덮지 않게
  const sumEl = $("#ref-summary");
  sumEl.innerHTML = spinner("핵심 기여를 찾는 중… (웹 검색 포함, 수십 초)");
  let acc = "";
  try {
    await streamPost(
      `/api/papers/${state.paper.id}/refs/${n}/summary`,
      { model: currentModel(), force },
      (e2) => {
        if (e2.type !== "delta") return;
        acc += e2.text;
        if (seq === refSeq) sumEl.innerHTML = md(acc);
      }
    );
    if (acc) state.refSummaries[n] = acc;
    if (seq === refSeq) renderRefSummary(n, acc || "(요약 없음)");
  } catch (e) {
    if (seq !== refSeq) return;
    sumEl.innerHTML = `<div class="dim">요약 실패: ${esc(e.message)}</div>`;
    const retry = document.createElement("button");
    retry.className = "btn small";
    retry.textContent = "다시 시도";
    retry.addEventListener("click", () => runRefSummary(n, force));
    sumEl.appendChild(retry);
  }
}

function closeRefPopover() {
  refPopover.hidden = true;
  refSeq++;
  refPinned = false;
  cancelRefAutoClose();
}
$("#ref-close").addEventListener("click", closeRefPopover);
document.addEventListener("click", (e) => {
  if (!refPopover.hidden && !refPopover.contains(e.target) && !e.target.closest(".cite-rect"))
    closeRefPopover();
});

function gotoRef(entry) {
  const wrap = $(`.page-wrap[data-page="${entry.page}"]`);
  if (!wrap) return;
  const [x0, y0, x1, y1] = entry.rect;
  const top = wrap.getBoundingClientRect().top - pagesEl.getBoundingClientRect().top
    + pagesEl.scrollTop + y0 * wrap.offsetHeight - 120;
  pagesEl.scrollTo({ top, behavior: "smooth" });
  const f = document.createElement("div");
  f.className = "flash-rect";
  f.style.cssText = `left:${x0 * 100}%;top:${y0 * 100}%;` +
    `width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%`;
  wrap.appendChild(f);
  setTimeout(() => f.remove(), 2400);
}

/* ───────────────────────── 크기 조절 ───────────────────────── */

{ // 사이드 패널 폭
  const panelEl = $("#panel");
  const saved = +localStorage.getItem("pr-panel-w");
  if (saved >= 300 && saved <= 720) panelEl.style.width = `${saved}px`;
  $("#panel-resizer").addEventListener("mousedown", (e) => {
    e.preventDefault();
    const rz = e.currentTarget;
    rz.classList.add("active");
    document.body.classList.add("resizing");
    const move = (ev) => {
      const w = Math.min(720, Math.max(300, window.innerWidth - ev.clientX));
      panelEl.style.width = `${w}px`;
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      rz.classList.remove("active");
      document.body.classList.remove("resizing");
      localStorage.setItem("pr-panel-w", parseInt(panelEl.style.width) || 420);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

{ // 채팅 입력창 높이
  const savedH = +localStorage.getItem("pr-chat-h");
  if (savedH >= 44 && savedH <= 340) {
    chatInput.style.height = `${savedH}px`;
    chatInput.style.maxHeight = "none";
  }
  $("#chat-input-resizer").addEventListener("mousedown", (e) => {
    e.preventDefault();
    document.body.classList.add("resizing-v");
    const startY = e.clientY;
    const startH = chatInput.offsetHeight;
    const move = (ev) => {
      const h = Math.min(340, Math.max(44, startH + (startY - ev.clientY)));
      chatInput.style.height = `${h}px`;
      chatInput.style.maxHeight = "none";
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      document.body.classList.remove("resizing-v");
      localStorage.setItem("pr-chat-h", chatInput.offsetHeight);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

/* ───────────────────────── 드래그 선택 툴바 ───────────────────────── */

const selToolbar = $("#sel-toolbar");
let selText = "";

document.addEventListener("mouseup", (e) => {
  if (e.target.closest("#sel-toolbar")) return;
  setTimeout(() => {   // selection 확정 이후에 판단
    const sel = window.getSelection();
    const text = sel ? sel.toString().trim() : "";
    if (!text || !sel.rangeCount || readerView.hidden) return hideSelToolbar();
    const anchor = sel.anchorNode && sel.anchorNode.parentElement;
    if (!anchor || !anchor.closest(".text-layer")) return hideSelToolbar();
    selText = text;
    selToolbar.hidden = false;
    const r = sel.getRangeAt(0).getBoundingClientRect();
    const x = Math.max(8, Math.min(
      r.left + r.width / 2 - selToolbar.offsetWidth / 2,
      innerWidth - selToolbar.offsetWidth - 8));
    let y = r.top - selToolbar.offsetHeight - 8;
    if (y < 8) y = r.bottom + 8;
    selToolbar.style.left = `${x}px`;
    selToolbar.style.top = `${y}px`;
  }, 0);
});
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#sel-toolbar")) hideSelToolbar();
});
function hideSelToolbar() {
  selToolbar.hidden = true;
}

$("#sel-ask").addEventListener("click", () => {
  if (!selText) return;
  const quote = selText.length > 600 ? selText.slice(0, 600) + "…" : selText;
  switchTab("chat");
  chatInput.value = `"${quote}"\n\n이 부분에 대해 `;
  hideSelToolbar();
  window.getSelection().removeAllRanges();
  chatInput.focus();
  chatInput.selectionStart = chatInput.selectionEnd = chatInput.value.length;
});

$("#sel-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(selText);
  } catch {
    // clipboard API 실패 시 execCommand 폴백
    const ta = document.createElement("textarea");
    ta.value = selText;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  hideSelToolbar();
  window.getSelection().removeAllRanges();
});

/* ───────────────────────── Notion 로그 ───────────────────────── */

const notionMenu = $("#notion-menu");
let notionTimer = null;

$("#notion-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  notionMenu.hidden = !notionMenu.hidden;
  if (!notionMenu.hidden) refreshNotion();
});
document.addEventListener("click", (e) => {
  if (!notionMenu.hidden && !notionMenu.contains(e.target) && !e.target.closest("#notion-btn"))
    notionMenu.hidden = true;
});

async function refreshNotion() {
  try {
    const s = await jfetch(`/api/notion${state.paper ? "?pid=" + state.paper.id : ""}`);
    state.notion = s;
    renderNotionStatus(s);
  } catch {}
}

function refreshNotionSoon() {
  clearTimeout(notionTimer);
  notionTimer = setTimeout(refreshNotion, 7000);
}

function renderNotionStatus(s) {
  $("#notion-enabled").checked = !!s.enabled;
  $("#notion-dot").className = "dot " +
    (!s.enabled ? "" : s.last_error ? "error" : s.queued_total ? "queued" : "on");
  const parts = [];
  if (!s.enabled) parts.push("자동 기록 꺼짐");
  else {
    parts.push(s.queued_total ? `동기화 대기 ${s.queued_total}건` : "대기 없음");
    if (s.last_sync)
      parts.push("마지막 동기화 " + new Date(s.last_sync * 1000)
        .toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }));
  }
  if (s.last_error) parts.push("⚠ " + s.last_error.slice(0, 140));
  $("#notion-status").textContent = parts.join(" · ");
  const pl = $("#notion-page-link");
  pl.hidden = !s.paper_page_url;
  if (s.paper_page_url) pl.href = s.paper_page_url;
  const dl = $("#notion-db-link");
  dl.hidden = !s.database_url;
  if (dl.href !== s.database_url && s.database_url) dl.href = s.database_url;
  // 대기 건이 있으면 잠시 후 자동 갱신 (백그라운드 flush 반영)
  if (s.enabled && s.queued_total) {
    clearTimeout(notionTimer);
    notionTimer = setTimeout(refreshNotion, 15000);
  }
}

$("#notion-enabled").addEventListener("change", async (e) => {
  const s = await jfetch("/api/notion", { method: "POST", body: { enabled: e.target.checked } });
  renderNotionStatus({ ...s, paper_page_url: state.notion?.paper_page_url });
});

$("#notion-flush").addEventListener("click", async () => {
  const btn = $("#notion-flush");
  btn.disabled = true;
  btn.textContent = "동기화 중… (수십 초)";
  try {
    await jfetch("/api/notion/flush", { method: "POST" });
  } catch (e) {
    alert("동기화 실패: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "지금 동기화";
    refreshNotion();
  }
});

/* ───────────────────────── 초기화 ───────────────────────── */

(async function init() {
  const { models, default: def } = await jfetch("/api/models");
  state.models = models;
  modelSelect.innerHTML = models
    .map((m) => `<option value="${m.id}">${esc(m.name)}</option>`)
    .join("");
  modelSelect.value = state.model && models.some((m) => m.id === state.model)
    ? state.model : def;
  modelSelect.addEventListener("change", () =>
    localStorage.setItem("pr-model", modelSelect.value));
  await loadLibrary();
})();
