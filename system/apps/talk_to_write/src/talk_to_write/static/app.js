// Talk to Write: the page. Talk (the chat), Canvas (documents and pages), Garden (notes),
// and the floating mic that sends what you say, with where you are and what you're pointing at.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const TRASH_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M9 3h6l1 2h4v2H4V5h4zM6 9h12l-1 12H7z"/></svg>';
const PAGE_ICON = '<svg class="kind" width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z"/></svg>';
const LOCK_ICON = '<svg class="lock" width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M7 10V7a5 5 0 0 1 10 0v3h1v11H6V10zm2 0h6V7a3 3 0 0 0-6 0z"/></svg>';
// Side by side: the second pane is this same page in a frame (address ?beside=1). It runs on its
// own, but tells the outer page where it is, and the outer page passes that on to the chat.
const BESIDE = new URLSearchParams(location.search).has("beside");
if (BESIDE) document.body.classList.add("beside");
const isDesktop = () => BESIDE || matchMedia("(min-width: 761px)").matches;
const toOuter = (msg) => parent.postMessage({ ttw: true, ...msg }, location.origin);

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong. Try again.");
  return data;
}

function when(seconds) {
  const t = new Date(seconds * 1000), now = new Date();
  const days = Math.floor((new Date(now.toDateString()) - new Date(t.toDateString())) / 864e5);
  if (days === 0) return t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (days === 1) return "Yesterday";
  return t.toLocaleDateString([], { month: "short", day: "numeric", ...(t.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}

function toast(text, undo) {
  $("toastText").textContent = text;
  $("undoBtn").hidden = !undo;
  $("undoBtn").onclick = async () => { $("toast").hidden = true; await undo(); };
  $("toast").hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => ($("toast").hidden = true), undo ? 6000 : 3500);
}

// A second tap on the same button confirms something that can't be undone.
function armed(btn) {
  if (btn.classList.contains("armed")) return true;
  document.querySelectorAll(".armed").forEach((b) => { b.classList.remove("armed"); b.textContent = b.dataset.label; });
  btn.dataset.label = btn.textContent;
  btn.classList.add("armed");
  btn.textContent = "Delete for good";
  setTimeout(() => { if (btn.isConnected && btn.classList.contains("armed")) { btn.classList.remove("armed"); btn.textContent = btn.dataset.label; } }, 4000);
  return false;
}

// ---------------------------------------------------------------------------
// Where we are. The hash holds the right-hand side: #canvas, #canvas/doc/<id>,
// #canvas/page/<id>, #canvas/trash, #k, #k/<note id>, #k/trash.
// ---------------------------------------------------------------------------
const state = { side: "canvas", view: "list", kind: null, id: null, title: null, doc: null, editing: false };

function parseHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("k")) {
    const rest = h.slice(2);
    if (!rest) return { side: "k", view: "list" };
    if (rest === "trash") return { side: "k", view: "trash" };
    return { side: "k", view: "doc", kind: "note", id: rest };
  }
  const parts = h.split("/");
  if (parts[1] === "trash") return { side: "canvas", view: "trash" };
  if (parts[1] === "doc" && parts[2]) return { side: "canvas", view: "doc", kind: "doc", id: parts[2] };
  if (parts[1] === "page" && parts[2]) return { side: "canvas", view: "page", kind: "page", id: parts[2] };
  return { side: "canvas", view: "list" };
}

const hashFor = (side, view, kind, id) => {
  const s = side === "k" ? "k" : "canvas";
  if (view === "list") return "#" + s;
  if (view === "trash") return `#${s}/trash`;
  if (side === "k") return "#k/" + encodeURIComponent(id);
  return `#canvas/${kind}/${encodeURIComponent(id)}`;
};

function go(side, view, kind, id) {
  goHash(hashFor(side, view, kind, id));
}

function goHash(h) {
  if (location.hash !== h) history.pushState({}, "", h);
  route();
}

// Where the user was, kept on this device: the last screen on each tab, and for each document
// the paragraph that was at the top of the screen, so the app opens right back there.
const places = (() => { try { return JSON.parse(localStorage.getItem("ttw-places")) || {}; } catch { return {}; } })();
places.spots = places.spots || {};
function keepPlaces() { try { localStorage.setItem("ttw-places", JSON.stringify(places)); } catch { /* storage full or off */ } }
function rememberScreen(r) {
  if (BESIDE) { toOuter({ type: "at", hash: location.hash }); return; }
  const h = r.view === "trash" ? hashFor(r.side, "list") : location.hash;
  places.last = h; places[r.side] = h;
  keepPlaces();
}

function setTab(tab) {
  document.body.dataset.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
}

async function route() {
  const r = parseHash();
  clearTimeout(spotTimer); saveSpot();
  if (state.editing) await finishEditing();
  Object.assign(state, { side: r.side, view: r.view, kind: r.kind || null, id: r.id || null, title: null, doc: null });
  rememberScreen(r);
  if (document.body.dataset.tab !== "talk" || isDesktop()) setTab(r.side);
  document.body.classList.remove("tucked");
  document.body.classList.toggle("doc-open", r.view === "doc");
  if (r.view !== "doc") document.body.classList.remove("full");
  $("canvasList").hidden = !(r.side === "canvas" && r.view === "list");
  $("kList").hidden = !(r.side === "k" && r.view === "list");
  $("trashView").hidden = r.view !== "trash";
  $("pageView").hidden = r.view !== "page";
  $("docView").hidden = r.view !== "doc";
  document.querySelectorAll(".back-label").forEach((el) => (el.textContent = r.side === "k" ? "Garden" : "Canvas"));
  clearPick();
  if (r.view === "list") (r.side === "k" ? loadK() : loadCanvas());
  else if (r.view === "trash") loadTrash();
  else if (r.view === "page") loadPage();
  else loadDoc();
  reportScreen();
}

document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => {
  const tab = b.dataset.tab;
  if (tab === "talk") { if (!isDesktop()) setTab("talk"); reportScreen(); return; }
  if (tab === state.side && document.body.dataset.tab !== "talk") { go(tab, "list"); return; }
  setTab(tab);
  if (tab !== state.side) goHash(places[tab] || hashFor(tab, "list")); else reportScreen();
}));
// The back button always goes to this tab's list. (Retracing history could land on the other
// tab, or go nowhere when the app opened straight into a document.)
document.querySelectorAll("[data-back]").forEach((b) => (b.onclick = () => go(state.side, "list")));
addEventListener("popstate", route);

// ---------------------------------------------------------------------------
// Lists
// ---------------------------------------------------------------------------
const COLS_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/></svg>';
function rowHtml(title, right, icon = "") {
  return `<span class="t">${icon}<span>${esc(title)}</span></span><span class="right">${right}<button class="side" title="Open side by side">${COLS_ICON}</button><button class="trash" title="Move to Trash">${TRASH_ICON}</button></span><button class="row-del">Delete</button>`;
}

// A row can be dragged onto either pane, or onto the right edge to open it side by side.
const DRAG_TYPE = "text/x-ttw-place";
function wireRow(li, open, del, hash) {
  li.draggable = true;
  li.addEventListener("dragstart", (e) => { e.dataTransfer.setData(DRAG_TYPE, hash); e.dataTransfer.setData("text/plain", li.querySelector(".t").textContent); e.dataTransfer.effectAllowed = "copy"; });
  li.onclick = (e) => {
    if (e.target.closest(".side")) { e.stopPropagation(); openBeside(hash); return; }
    if (e.target.closest(".trash, .row-del")) { e.stopPropagation(); li.classList.remove("swiped"); del(); return; }
    if (li.classList.contains("swiped")) { li.classList.remove("swiped"); return; }
    open();
  };
  let x0 = null, y0 = null;
  li.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  li.addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dy) > Math.abs(dx)) return;
    if (dx < -40) { document.querySelectorAll(".swiped").forEach((o) => o.classList.remove("swiped")); li.classList.add("swiped"); }
    else if (dx > 40) li.classList.remove("swiped");
  });
}

async function loadCanvas() {
  const data = await api("/api/canvas");
  const ul = $("canvasItems");
  ul.innerHTML = "";
  for (const it of data.items) {
    const li = document.createElement("li");
    li.dataset.kind = it.kind; li.dataset.id = it.id;
    li.innerHTML = rowHtml(it.title, `<span class="when">${when(it.modified)}</span>`, it.kind === "page" ? PAGE_ICON : "");
    wireRow(li, () => go("canvas", it.kind, it.kind, it.id), () => trashItem("canvas", it.kind, it.id), hashFor("canvas", it.kind, it.kind, it.id));
    ul.append(li);
  }
  const add = document.createElement("li");
  add.className = "new"; add.textContent = "+ New";
  add.onclick = async () => { const made = await api("/api/canvas/new", { json: {} }); go("canvas", "doc", "doc", made.id); setTimeout(() => startEditing(true), 150); };
  ul.append(add);
  setTrashCount("canvas", data.trash);
}

let kQuery = "";
async function loadK() {
  const q = $("kSearch").value.trim();
  kQuery = q;
  const data = await api("/api/garden?q=" + encodeURIComponent(q));
  if (q !== kQuery) return;
  const ul = $("kItems");
  ul.innerHTML = "";
  if (!data.available) {
    $("kEmpty").hidden = false;
    $("kEmpty").textContent = data.connected
      ? "Your notes folder isn't reachable right now."
      : "Connect a notes folder to use the Garden: ask the chat to connect your notes folder.";
    return;
  }
  for (const n of data.items) {
    const li = document.createElement("li");
    li.dataset.id = n.id;
    li.innerHTML = rowHtml(n.title, `<span class="when">${when(n.modified)}${n.public ? "" : LOCK_ICON}</span>`);
    wireRow(li, () => go("k", "doc", "note", n.id), () => trashItem("k", "note", n.id), hashFor("k", "doc", "note", n.id));
    ul.append(li);
  }
  $("kEmpty").hidden = data.items.length > 0;
  $("kEmpty").textContent = q ? "No notes match." : "No notes yet.";
  setTrashCount("k", data.trash);
}
let searchTimer;
$("kSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { loadK(); reportScreen(); }, 180); };

function setTrashCount(side, n) {
  document.querySelector(`.trash-row[data-trash="${side}"] .count`).textContent = n;
}
document.querySelectorAll(".trash-row").forEach((b) => (b.onclick = () => go(b.dataset.trash, "trash")));

async function trashItem(side, kind, id) {
  try {
    const res = await api("/api/trash", { json: { side, kind, id } });
    if (state.view !== "list") go(side, "list"); else (side === "k" ? loadK() : loadCanvas());
    toast("Moved to Trash", async () => {
      const back = await api("/api/trash/restore", { json: { side, name: res.name } });
      (side === "k" ? loadK : loadCanvas)();
      void back;
    });
  } catch (err) { toast(err.message); }
}

async function loadTrash() {
  const side = state.side;
  const data = await api("/api/trash?side=" + side);
  const ul = $("trashList");
  ul.innerHTML = data.items.map((it) =>
    `<li data-name="${esc(it.id)}"><span>${esc(it.title)}<span class="gone">Deleted ${when(it.deleted)}</span></span><span class="acts"><button class="btn" data-restore>Restore</button><button class="btn danger" data-forever>Delete forever</button></span></li>`).join("");
  $("trashEmpty").hidden = data.items.length > 0;
  $("emptyTrash").hidden = !data.items.length;
}
$("trashList").onclick = async (e) => {
  const li = e.target.closest("li");
  if (!li) return;
  try {
    if (e.target.matches("[data-restore]")) { await api("/api/trash/restore", { json: { side: state.side, name: li.dataset.name } }); toast("Restored"); loadTrash(); }
    if (e.target.matches("[data-forever]") && armed(e.target)) { await api("/api/trash/purge", { json: { side: state.side, name: li.dataset.name } }); loadTrash(); }
  } catch (err) { toast(err.message); }
};
$("emptyTrash").onclick = async (e) => {
  if (!armed(e.currentTarget)) return;
  e.currentTarget.classList.remove("armed"); e.currentTarget.textContent = "Empty Trash";
  await api("/api/trash/empty", { json: { side: state.side } });
  loadTrash();
};
document.querySelectorAll("[data-delete]").forEach((b) => (b.onclick = () => trashItem(state.side, state.kind, state.id)));

// ---------------------------------------------------------------------------
// Pages the agent made
// ---------------------------------------------------------------------------
async function loadPage() {
  try {
    const data = await api(`/api/doc?side=canvas&kind=page&id=${encodeURIComponent(state.id)}`);
    state.title = data.title;
    $("pageFrame").srcdoc = data.source;
    reportScreen();
  } catch (err) { toast(err.message); go("canvas", "list"); }
}

// ---------------------------------------------------------------------------
// Documents and garden notes
// ---------------------------------------------------------------------------
const docQuery = () => `side=${state.side}&kind=${state.kind}&id=${encodeURIComponent(state.id)}`;

async function loadDoc(keepScroll) {
  pendingRefresh = false;  // this load brings everything up to date
  const scroll = $("docScroll").scrollTop;
  let data;
  try { data = await api("/api/doc?" + docQuery()); }
  catch (err) { toast(err.message); go(state.side, "list"); return; }
  showDoc(data);
  if (state.editing) {
    // While the user is typing (after the user accepted or skipped a suggestion): the changed words are
    // replaced in place and the suggestions redrawn; the page stays where it is.
    ed().replaceAll(data.body);
    ed().setMarks(editorMarks());
    $("docScroll").scrollTop = scroll;
    reopenInEditor();
  } else if (keepScroll) $("docScroll").scrollTop = scroll; else returnToSpot();
  reportScreen();
}

// The document as it came from the server, drawn on the page.
function showDoc(data) {
  state.doc = data; state.title = data.title;
  $("publicTag").hidden = !data.public;
  setSuggesting(!!data.suggesting);
  $("sourceLink").hidden = !data.source_url;
  if (data.source_url) $("sourceLink").href = data.source_url;
  closeHistory();
  $("saveStatus").textContent = "";
  renderArticle();
}

// --- their spot in each document -------------------------------------------------
const spotKey = () => `${state.side}/${state.id}`;
let spotTimer, returning = false;

function saveSpot() {
  if (state.view !== "doc" || !state.doc || historyOpen || returning) return;
  let text = "", offset = 0;
  if (state.editing) {
    const at = topIndexInEditor();
    text = squash(ed().value.slice(at, at + 160).replace(/^\S*\s+/, ""));
  } else {
    const place = topBlock();
    if (place) { text = squash(place.block.textContent); offset = place.offset; }
  }
  places.spots[spotKey()] = { text: text.slice(0, 80), offset, top: $("docScroll").scrollTop, at: Date.now() };
  // Keep the 60 most recent documents.
  const keys = Object.keys(places.spots).sort((a, b) => places.spots[b].at - places.spots[a].at);
  keys.slice(60).forEach((k) => delete places.spots[k]);
  keepPlaces();
}

function returnToSpot() {
  const scroller = $("docScroll"), spot = places.spots[spotKey()];
  if (!spot) { scroller.scrollTop = 0; return; }
  // Find the paragraph by its words (so edits above it don't throw the place off); fall back to the distance.
  const apply = () => {
    const want = spot.text.split(" ").slice(0, 6).join(" ");
    const block = want && [...art.querySelectorAll("p, li, h1, h2, h3, h4, blockquote, figcaption, td")]
      .find((b) => squash(b.textContent).includes(want));
    if (block) scroller.scrollTop += block.getBoundingClientRect().top - scroller.getBoundingClientRect().top - spot.offset;
    else scroller.scrollTop = spot.top;
  };
  returning = true;
  apply();
  // Pictures above their place load after this and push it down: follow them until the user scrolls themselves.
  let done = false;
  const stop = () => { done = true; returning = false; art.removeEventListener("load", follow, true); };
  const follow = () => { if (!done) apply(); };
  art.addEventListener("load", follow, true);
  scroller.addEventListener("touchstart", stop, { once: true, passive: true });
  scroller.addEventListener("wheel", stop, { once: true, passive: true });
  setTimeout(stop, 2500);
}

$("docScroll").addEventListener("scroll", () => { clearTimeout(spotTimer); spotTimer = setTimeout(saveSpot, 400); });
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") saveSpot(); });
addEventListener("pagehide", saveSpot);

// The title and text the page was last drawn from.
let drawnAs = "";
function renderArticle() {
  const art = $("article");
  const d = state.doc;
  art.innerHTML = (d.kind === "page" ? "" : `<h1>${esc(d.title)}</h1>`) + d.html;
  drawnAs = d.title + "\n" + d.body;
  art.querySelectorAll("a.wikilink").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault();
    const found = await api("/api/garden/resolve?name=" + encodeURIComponent(a.dataset.note));
    if (found.id) go("k", "doc", "note", found.id); else toast("That note isn't in your garden.");
  }));
  wrapWords(art);
  showSuggestions();
}

// --- pointing: tap picks one word, drag picks more; figures as a whole -------
let words = [], anchor = -1, focus = -1, pickedFig = null, dragging = false, moved = false, downAt = -1;
const SKIP_WRAP = "a, del, ins, code, pre, .sug-card, figure, .callout-title, sup";

function wrapWords(root) {
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement.closest(SKIP_WRAP) || !n.textContent.trim() ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes = [];
  while (walk.nextNode()) nodes.push(walk.currentNode);
  for (const n of nodes) {
    const frag = document.createDocumentFragment();
    for (const part of n.textContent.split(/(\s+)/)) {
      if (!part) continue;
      if (/^\s+$/.test(part)) { frag.append(part); continue; }
      const w = document.createElement("span");
      w.className = "w"; w.textContent = part;
      frag.append(w);
    }
    n.replaceWith(frag);
  }
  words = [...root.querySelectorAll(".w")];
  anchor = focus = -1;
}

function blockOf(el) { return el.closest("p, li, h1, h2, h3, h4, blockquote, figcaption, td, th") || el; }

function pickedText() {
  if (pickedFig) return null;
  if (anchor < 0) return null;
  const [a, b] = [Math.min(anchor, focus), Math.max(anchor, focus)];
  return words.slice(a, b + 1).map((w) => w.textContent).join(" ");
}

function saidWhen(text) {
  const said = (state.doc && state.doc.said) || [];
  const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const t = norm(text);
  if (!t) return "";
  const hit = said.find((s) => norm(s.text).includes(t));
  return hit ? "said " + when(Date.parse(hit.at) / 1000) : "";
}

function paint() {
  const [a, b] = [Math.min(anchor, focus), Math.max(anchor, focus)];
  words.forEach((w, i) => {
    const on = anchor >= 0 && i >= a && i <= b;
    w.classList.toggle("picked", on);
    w.classList.toggle("first", on && i === a);
    w.classList.toggle("last", on && i === b);
  });
  if (anchor >= 0) {
    const text = pickedText();
    showAbout("“" + text + "”", saidWhen(blockOf(words[a]).textContent.includes(text) ? text : text));
  }
  scopeChanged();
}

function showAbout(label, said) {
  if (BESIDE) { toOuter({ type: "picked" }); reportScreen(); }
  else if (besideOpen()) $("besideFrame").contentWindow.postMessage({ ttw: true, type: "clear-pick" }, location.origin);
  $("aboutText").textContent = label;
  if (said) { const w = document.createElement("span"); w.className = "when"; w.textContent = said; $("aboutText").append(w); }
  $("about").hidden = false;
  refreshBubble();
}

function clearPick() {
  anchor = focus = -1;
  words.forEach((w) => w.classList.remove("picked", "first", "last"));
  if (pickedFig) { pickedFig.classList.remove("picked"); pickedFig = null; }
  $("about").hidden = true;
  refreshBubble();
  scopeChanged();
  if (BESIDE) reportScreen(); // the outer page sends what the user points at here along with what the user says
}
$("clearAbout").onclick = clearPick;

const art = $("article");
const wordAt = (x, y) => { const el = document.elementFromPoint(x, y); return el && el.classList.contains("w") ? words.indexOf(el) : -1; };
art.addEventListener("touchstart", (e) => { if (!state.editing && e.target.classList && e.target.classList.contains("picked")) e.preventDefault(); }, { passive: false });
art.addEventListener("pointerdown", (e) => {
  if (state.editing) return;
  const i = words.indexOf(e.target);
  if (i >= 0 && (e.pointerType === "mouse" || e.target.classList.contains("picked"))) {
    dragging = true; moved = false;
    if (e.pointerType === "mouse" || anchor < 0) downAt = i;
    else {
      const a = Math.min(anchor, focus), b = Math.max(anchor, focus);
      if (i - a < b - i) { anchor = b; focus = a; } else { anchor = a; focus = b; }
    }
    e.preventDefault();
  }
});
art.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const i = wordAt(e.clientX, e.clientY);
  if (i < 0) return;
  if (downAt >= 0) { if (i === downAt) return; clearPick(); anchor = downAt; downAt = -1; }
  if (!moved) art.setPointerCapture(e.pointerId);
  if (i !== focus) { focus = i; moved = true; paint(); }
});
art.addEventListener("pointerup", () => { downAt = -1; if (dragging && moved) art.dataset.justDragged = "1"; dragging = false; });
// A second tap in the same spot soon after the first: start typing there. On a phone it's caught
// as the finger lifts (a tap on a highlighted word never becomes a click: that's how dragging works).
let lastTap = { at: 0, x: 0, y: 0 }, lastTouchEnd = 0;
function doubleTap(x, y, target) {
  const now = Date.now(), near = Math.hypot(x - lastTap.x, y - lastTap.y) < 30;
  if (!(now - lastTap.at < 400 && near)) { lastTap = { at: now, x, y }; return false; }
  lastTap = { at: 0, x: 0, y: 0 };
  if (historyOpen) return true;
  const block = target.closest && target.closest("p, li, h1, h2, h3, h4, blockquote, figcaption, td");
  if (block && block.tagName === "H1" && block === art.firstElementChild) startEditing(true);
  else startEditing(false, { x, y, block });
  return true;
}
art.addEventListener("touchend", (e) => {
  lastTouchEnd = Date.now();
  if (state.editing || dragging && moved || e.touches.length || e.target.closest("a, button, video, iframe, .edit")) return;
  const t = e.changedTouches[0];
  if (doubleTap(t.clientX, t.clientY, e.target)) e.preventDefault();
});
art.addEventListener("click", (e) => {
  if (state.editing || e.target.closest("a, button, video, iframe, .edit")) return;
  if (art.dataset.justDragged) { delete art.dataset.justDragged; return; }
  if (Date.now() - lastTouchEnd > 800 && doubleTap(e.clientX, e.clientY, e.target)) return;
  const i = words.indexOf(e.target);
  const fig = e.target.closest("figure");
  if (i >= 0) {
    const single = anchor === focus && anchor === i;
    clearPick();
    if (!single) { anchor = focus = i; paint(); }
  } else if (fig) {
    const was = fig === pickedFig;
    clearPick();
    if (!was) {
      pickedFig = fig; fig.classList.add("picked");
      const cap = fig.querySelector("figcaption");
      const what = fig.querySelector("video, iframe") ? "the video" : "the image";
      showAbout(cap ? `${what}: “${cap.textContent.trim()}”` : what, "");
      scopeChanged();
    }
  }
});

function pointing() {
  if (state.editing && editorInst) {
    const { line, picked } = ed().cursorText();
    const passage = line.replace(/^(#{1,6}|>|[-*+]|\d+\.)\s+/, "").trim();
    return passage ? { what: "text", text: picked || passage, in_passage: passage } : null;
  }
  if (pickedFig) {
    const cap = pickedFig.querySelector("figcaption");
    return { what: pickedFig.querySelector("video, iframe") ? "video" : "image", src: pickedFig.dataset.src || "", caption: cap ? cap.textContent.trim() : "" };
  }
  const text = pickedText();
  if (!text) return null;
  const block = blockOf(words[Math.min(anchor, focus)]);
  const clean = block.cloneNode(true);
  clean.querySelectorAll("ins.sug, sup, .edit-acts").forEach((el) => el.remove());
  return { what: "text", text, in_passage: clean.textContent.replace(/\s+/g, " ").trim() };
}

// --- editing (the markdown underneath) ---------------------------------------
let saveTimer = null, saving = Promise.resolve();

// --- keeping their place when switching between reading and editing --------------
const squash = (t) => t.replace(/[*_`#>\[\]()!^~-]+/g, "").replace(/\s+/g, " ").trim().toLowerCase();

// Where in the markdown a stretch of rendered text starts (formatting marks ignored).
function indexInSource(src, text) {
  const words = squash(text).split(" ").filter(Boolean).slice(0, 8);
  // Map each character of the squashed source back to its position in the real source.
  let flat = "", map = [], lastSpace = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (/[*_`#>\[\]()!^~-]/.test(c)) continue;
    if (/\s/.test(c)) { if (!lastSpace && flat) { flat += " "; map.push(i); } lastSpace = true; continue; }
    flat += c.toLowerCase(); map.push(i); lastSpace = false;
  }
  for (let n = words.length; n >= 2; n--) {
    const at = flat.indexOf(words.slice(0, n).join(" "));
    if (at >= 0) return map[at];
  }
  return -1;
}

// The first block of the article showing at the top of the screen, and how far down it sits.
// With `whole`, the first block that starts on the screen: the one the user is looking at.
function topBlock(whole = false) {
  const scroller = $("docScroll"), top = scroller.getBoundingClientRect().top;
  const blocks = art.querySelectorAll("p, li, h1, h2, h3, h4, blockquote, figcaption, td");
  for (const b of blocks) {
    const r = b.getBoundingClientRect();
    if ((whole ? r.top >= top - 1 : r.bottom > top + 4) && b.textContent.trim()) return { block: b, offset: r.top - top };
  }
  return null;
}

// The character position at the top of the screen while editing.
function topIndexInEditor() {
  const visibleTop = $("docScroll").scrollTop - $("mdsrc").offsetTop;
  return visibleTop <= 0 ? 0 : ed().posAtHeight(visibleTop);
}

// The editor: markdown underneath, but it reads like the page (see editor/editor.js).
let editorInst = null;
function ed() {
  if (!editorInst) editorInst = TTWEditor.createEditor($("mdsrc"), "", {
    onChange: () => { clearTimeout(saveTimer); saveTimer = setTimeout(save, 900); },
    drawMark: (key) => drawSpot(key),
  });
  return editorInst;
}

// There's no Edit button: a double-tap starts typing right where the user tapped (`tap` is the
// point and the paragraph under it), and lowering the keyboard or tapping away goes back to reading.
function startEditing(focusTitle, tap) {
  if (!state.doc || state.editing) return;
  state.editing = true;
  let place = focusTitle ? null : topBlock(true);
  if (tap && tap.block) place = { block: tap.block, offset: tap.block.getBoundingClientRect().top - $("docScroll").getBoundingClientRect().top };
  clearPick();
  const scroller = $("docScroll");
  // The page stays on screen exactly as the user sees it while the editor lines up underneath;
  // then the two swap in one go, so nothing on screen moves.
  const unfreeze = freezeArticle();
  // A garden note's title is its file name: changing it renames the note (see renameNote).
  $("titleInput").hidden = false;
  $("titleInput").value = state.doc.title;
  holdBars();
  // The bars (with the formatting bar) come back if the user had scrolled them away; they float
  // over the text, so this doesn't move it.
  document.body.classList.remove("tucked");
  // The formatting bar appears under the top bar and pushes the text area down by its height;
  // that difference comes off the paragraph's distance from the top, so the text stays put.
  const topBefore = scroller.getBoundingClientRect().top;
  document.body.classList.add("editing");
  const shift = scroller.getBoundingClientRect().top - topBefore;
  clipFrozen();
  $("mdsrc").hidden = false;
  const e = ed();
  e.reset(state.doc.body);
  e.setMarks(editorMarks());
  if (focusTitle) { unfreeze(); $("titleInput").focus(); $("titleInput").select(); return; }
  // Open the editor at the same place the user was reading, with the cursor there.
  let index = 0;
  if (place && place.block.tagName !== "H1") {
    const found = indexInSource(state.doc.body, place.block.textContent);
    if (found >= 0) index = found;
  }
  // Same distance from the top as the paragraph had (if it started above the screen, just as far above).
  // The editor measures lines as it shows them, so the position is set again each frame until it holds.
  let offset = place ? place.offset - shift : 0;
  const exact = () => { scroller.scrollTop = index ? $("mdsrc").offsetTop + e.topOf(index) - offset : 0; };
  // The cursor goes to the word under their finger.
  const pick = () => { if (tap) { const p = e.posAt(tap.x, tap.y); if (p != null) e.select(p); } };
  // When the keyboard comes up over the cursor, the text moves up once, just enough to show it.
  const aboveKeyboard = () => {
    const vv = window.visualViewport;
    const c = vv && e.view.coordsAtPos(e.view.state.selection.main.head);
    if (!c) return;
    const over = c.bottom - (vv.height - 24);
    if (over > 0) { offset -= over; exact(); }
  };
  // The cursor goes in place before focusing: a phone scrolls to wherever the cursor is when the
  // keyboard opens, and would otherwise jump to the last place the user edited.
  // On a heading the cursor would show its "##" and shift the line; it goes to the text below instead.
  let cursorAt = index;
  const lineStart = state.doc.body.lastIndexOf("\n", index - 1) + 1;
  if (/^#{1,6} /.test(state.doc.body.slice(lineStart, lineStart + 7))) {
    const next = state.doc.body.slice(index).search(/\n\s*\n(?=\S)/);
    if (next >= 0) cursorAt = index + next + state.doc.body.slice(index + next).match(/^\n\s*\n/)[0].length;
  }
  if (index) e.scrollTo(index, Math.max(offset, 0)); else exact();
  e.placeCursor(cursorAt);
  // Line up each frame until the text has sat still on screen for three frames (or enough have
  // passed), then show the editor.
  let last = "", still = 0, frames = 0;
  const settle = () => {
    exact(); pick(); exact();
    const c = e.view.coordsAtPos(Math.min(e.view.state.selection.main.head, e.view.state.doc.length));
    const now = `${scroller.scrollTop}/${e.topOf(index)}/${c ? Math.round(c.top) : ""}`;
    still = now === last ? still + 1 : 0; last = now;
    if (still < 2 && frames++ < 20) { requestAnimationFrame(settle); return; }
    unfreeze();
    holdScroll(() => { exact(); aboveKeyboard(); });
  };
  requestAnimationFrame(settle);
}

// The page as it is on screen, held still (and covering the editor) until `unfreeze`.
function freezeArticle() {
  const r = art.getBoundingClientRect();
  art.style.cssText = `position:fixed;top:${r.top}px;left:${r.left}px;width:${r.width}px;box-sizing:border-box;margin:0;z-index:4;background:var(--paper);pointer-events:none`;
  art.dataset.frozenTop = r.top;
  return () => { art.hidden = true; art.style.cssText = ""; delete art.dataset.frozenTop; };
}
// Only the part inside the text area shows (the formatting bar may have appeared above it).
function clipFrozen() {
  const s = $("docScroll").getBoundingClientRect(), top = +art.dataset.frozenTop;
  art.style.minHeight = `${s.bottom - top}px`;
  art.style.clipPath = `inset(${Math.max(0, s.top - top)}px 0 0 0)`;
  const below = art.getBoundingClientRect().bottom - s.bottom;
  if (below > 0) art.style.clipPath = `inset(${Math.max(0, s.top - top)}px 0 ${below}px 0)`;
}

// Keep the editor in place while the phone's keyboard slides up and the browser tries to
// scroll the cursor into view on its own.
function holdScroll(place) {
  const scroller = $("docScroll");
  const apply = () => { if (window.scrollY) window.scrollTo(0, 0); place(); };
  apply();
  let stop = false;
  const onResize = () => { if (!stop) apply(); };
  if (window.visualViewport) visualViewport.addEventListener("resize", onResize);
  [50, 150, 300, 500, 800].forEach((ms) => setTimeout(() => { if (!stop) apply(); }, ms));
  const release = () => {
    stop = true;
    if (window.visualViewport) visualViewport.removeEventListener("resize", onResize);
    scroller.removeEventListener("touchstart", release);
    $("mdsrc").removeEventListener("keydown", release);
  };
  // Hand scrolling back to them as soon as the user touches or types, and after the keyboard settles.
  scroller.addEventListener("touchstart", release, { passive: true });
  $("mdsrc").addEventListener("keydown", release);
  setTimeout(release, 1000);
}

async function finishEditing() {
  if (!state.editing) return;
  clearTimeout(saveTimer);
  await save();
  state.editing = false;
  $("titleInput").hidden = true;
  $("mdsrc").hidden = true;
  document.body.classList.remove("editing");
  art.hidden = false;
}

// A garden note's title is its file name in their vault, so a new title renames the note (and
// every link to it). Done once the user leaves the title, not on each keystroke.
let renaming = null;
async function renameNote() {
  if (state.side !== "k" || !state.doc) return;
  if (renaming) return renaming;
  const title = $("titleInput").value.trim();
  if (!title || title === state.doc.title) { $("titleInput").value = state.doc.title; return; }
  if (state.doc.public && !confirm(`"${state.doc.title}" is marked public. If you publish your notes somewhere, renaming it may change its web address, so links to the old address could stop working. Rename it?`)) {
    $("titleInput").value = state.doc.title;
    return;
  }
  $("saveStatus").textContent = "Renaming...";
  renaming = api("/api/garden/rename", { json: { id: state.id, title } })
    .then((res) => {
      state.id = res.id; state.doc.title = title; state.title = title; state.doc.modified = res.modified;
      // Its links to itself now use the new title: the editor takes that text, unless the user has changed it.
      if (state.editing && ed().value === state.doc.body && res.body !== state.doc.body) ed().reset(res.body);
      state.doc.body = res.body;
      history.replaceState({}, "", hashFor("k", "doc", "note", res.id));
      rememberScreen(parseHash());
      $("saveStatus").textContent = "Renamed";
    })
    .catch((err) => { $("titleInput").value = state.doc.title; $("saveStatus").textContent = ""; toast(err.message); })
    .finally(() => { renaming = null; });
  return renaming;
}
$("titleInput").addEventListener("blur", () => { if (state.side === "k") renameNote(); });
$("titleInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && state.side === "k") { e.preventDefault(); renameNote().then(() => ed().focus()); }
});

async function save() {
  if (!state.doc) return;
  await renameNote();
  const body = ed().value, title = $("titleInput").value.trim() || "Untitled";
  if (body === state.doc.body && (state.side === "k" || title === state.doc.title)) return;
  $("saveStatus").textContent = "Saving...";
  saving = api("/api/doc", { method: "PUT", json: { side: state.side, kind: state.kind, id: state.id, body, title, base: state.doc.modified } })
    .then((res) => { state.doc.body = body; state.doc.title = title; state.doc.modified = res.modified; $("saveStatus").textContent = "Saved"; })
    .catch((err) => { $("saveStatus").textContent = ""; toast(err.message); });
  return saving;
}

$("titleInput").addEventListener("input", () => { if (state.side === "k") return; clearTimeout(saveTimer); saveTimer = setTimeout(save, 900); });
// The suggestions waiting on this document, as stretches of the text for the editor to underline.
function editorMarks() {
  const body = (state.doc && state.doc.body) || "";
  const { groups, blocks } = suggestionSpots(body);
  editorSpots = Object.fromEntries(groups.map((g) => [g.key, g]));
  editorBlocks = Object.fromEntries(blocks.map((b) => ["block:" + b.id, b]));
  return groups.map((g) => ({ from: g.start, to: g.end, key: g.key }))
    .concat(blocks.map((b) => { const at = body.indexOf(b.find); return { from: at, to: at + b.find.length, key: "block:" + b.id, block: blockKind(b) }; }));
}

// Lowering the keyboard or tapping away ends typing. Not when the focus went to the formatting
// buttons, the title, a suggestion's card, or a file picker the user opened from the bar.
let holdEditingUntil = 0;
function maybeStopEditing() {
  setTimeout(() => {
    if (!state.editing || Date.now() < holdEditingUntil) return;
    const a = document.activeElement;
    if (ed().hasFocus() || $("mdsrc").contains(a) || a === $("titleInput") || $("toolbar").contains(a) || !$("sugPop").hidden) return;
    stopEditing();
  }, 250);
}
$("mdsrc").addEventListener("focusout", maybeStopEditing);
// Tapping a suggestion in the text (its words, its tick or cross) keeps the keyboard up and typing on.
["pointerdown", "mousedown"].forEach((t) => $("mdsrc").addEventListener(t, (e) => { if (e.target.closest(".edit")) e.preventDefault(); }, true));
$("titleInput").addEventListener("blur", maybeStopEditing);

// Back to reading at the same place the user was editing.
let stopping = false;
async function stopEditing() {
  if (!state.editing || stopping) return;
  stopping = true;
  try { await backToReading(); } finally { stopping = false; }
}
async function backToReading() {
  // The first line that starts on the screen, and how far down it sits, so reading keeps the same spot.
  // The line the user was typing in, if it's on screen (suggestions shown in the reading view change
  // how much room the paragraphs above take); otherwise the first line on the screen.
  const top = $("docScroll").scrollTop - $("mdsrc").offsetTop, bottom = top + $("docScroll").clientHeight;
  const cursorLine = ed().lineStartAtCursor();
  const cursorTop = ed().topOf(cursorLine);
  const at = ed().cursorText().line.trim() && cursorTop >= top && cursorTop < bottom - 40 ? cursorLine : ed().firstLineAt(top);
  const snippet = ed().value.slice(at, at + 160).split("\n")[0];
  const lineOffset = $("mdsrc").offsetTop + ed().topOf(at) - $("docScroll").scrollTop;
  // Where that line is on the screen; the formatting bar going away moves the text area up.
  const lineScreenY = $("docScroll").getBoundingClientRect().top + lineOffset;
  holdBars();
  // The editor stays on screen while the text is saved and (only if it changed) drawn again;
  // then the page replaces it already at the same spot, all before the screen updates.
  clearTimeout(saveTimer);
  await save();
  let data = null;
  if (pendingRefresh || drawnAs !== state.doc.title + "\n" + state.doc.body) {
    try { data = await api("/api/doc?" + docQuery()); pendingRefresh = false; } catch { /* the page as last drawn */ }
  }
  if (!state.editing) return;
  state.editing = false;
  $("titleInput").hidden = true;
  $("mdsrc").hidden = true;
  document.body.classList.remove("editing");
  if (data) showDoc(data);
  art.hidden = false;
  // Skip a half-cut first word and any formatting-only lines, then look for the next few words.
  const want = squash(snippet.replace(/^[#>*\-\s]+/, "")).split(" ").filter(Boolean).slice(0, 5).join(" ");
  // Their own words in each paragraph: suggested new words and the buttons left out.
  const own = (b) => { const c = b.cloneNode(true); c.querySelectorAll("ins.sug, sup, .edit-acts").forEach((x) => x.remove()); return squash(c.textContent); };
  const block = want && [...art.querySelectorAll("p, li, h1, h2, h3, h4, blockquote, figcaption, td")].find((b) => own(b).includes(want));
  if (!block) { reportScreen(); return; }
  const scroller = $("docScroll");
  const align = () => { scroller.scrollTop += block.getBoundingClientRect().top - lineScreenY; };
  align();
  // Pictures above it that finish loading afterwards would push it down: keep it in place until the user scrolls.
  const follow = () => align();
  art.addEventListener("load", follow, true);
  const stop = () => art.removeEventListener("load", follow, true);
  scroller.addEventListener("touchstart", stop, { once: true, passive: true });
  scroller.addEventListener("wheel", stop, { once: true, passive: true });
  setTimeout(stop, 2000);
  reportScreen();
}

let uploadKind = "image";
document.querySelectorAll("[data-md]").forEach((b) => {
  // Tapping a button keeps the keyboard up and the cursor where it was.
  b.addEventListener("pointerdown", (e) => e.preventDefault());
  b.addEventListener("mousedown", (e) => e.preventDefault());
  b.addEventListener("click", () => {
    const k = b.dataset.md;
    if (k === "done") { stopEditing(); return; }
    if (k === "link") {
      holdEditingUntil = Date.now() + 120000;
      const url = prompt("Link address");
      holdEditingUntil = 0;
      if (url) ed().format("link", url.trim()); else ed().focus();
      return;
    }
    if (k === "caption") { ed().insertBlock("*Caption*", 1, 8); return; }
    if (k === "image" || k === "video") {
      holdEditingUntil = Date.now() + 120000;
      uploadKind = k;
      $("fileInput").accept = k === "image" ? "image/*" : "video/*";
      $("fileInput").click();
      return;
    }
    ed().format(k);
  });
});
$("fileInput").onchange = async () => {
  const file = $("fileInput").files[0];
  $("fileInput").value = "";
  if (!file) return;
  $("saveStatus").textContent = "Uploading...";
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("/api/media", { method: "POST", body: form }).then((r) => r.json());
    if (!res.url) throw new Error(res.error || "Upload failed.");
    holdEditingUntil = 0;
    const md = `![${uploadKind === "image" ? "" : "video"}](${res.url})`;
    ed().insertBlock(`${md}\n*Caption*`, md.length + 2, md.length + 9);
    $("saveStatus").textContent = "";
  } catch (err) { $("saveStatus").textContent = ""; toast(err.message); }
};

// --- version history -----------------------------------------------------------
const WHO = { you: "Your edits", outside: "The agent's edits", restored: "Restored an earlier version", earlier: "Before any changes here" };
let historyOpen = false, viewingVid = null;

async function openHistory() {
  if (state.editing) await finishEditing();
  clearPick();
  historyOpen = true; viewingVid = null;
  const data = await api(`/api/versions?side=${state.side}&id=${encodeURIComponent(state.id)}`);
  $("historyBar").hidden = false; $("docView").classList.add("history");
  $("historyBackLabel").textContent = "Back to the document";
  $("restoreBtn").hidden = true;
  art.hidden = true;
  const ul = $("historyList");
  ul.hidden = false;
  ul.innerHTML = data.versions.map((v, i) =>
    `<li data-vid="${v.vid}"><span>${new Date(v.at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}${i === 0 ? " (current)" : ""}<br><span class="who">${esc(WHO[v.who] || v.who)} · ${v.words} words</span></span></li>`).join("")
    || `<li class="new">No earlier versions yet.</li>`;
  ul.querySelectorAll("li[data-vid]").forEach((li, i) => (li.onclick = () => viewVersion(li.dataset.vid, i === 0)));
  $("docScroll").scrollTop = 0;
}

async function viewVersion(vid, isCurrent) {
  const data = await api(`/api/version?side=${state.side}&id=${encodeURIComponent(state.id)}&vid=${vid}`);
  viewingVid = vid;
  $("historyList").hidden = true;
  art.hidden = false;
  art.classList.add("old-version");
  art.innerHTML = `<h1>${esc(data.title)}</h1>` + data.html;
  words = [];
  $("historyBackLabel").textContent = "All versions";
  $("restoreBtn").hidden = isCurrent;
  $("docScroll").scrollTop = 0;
}

function closeHistory() {
  if (!historyOpen) return;
  historyOpen = false; viewingVid = null;
  $("historyBar").hidden = true; $("docView").classList.remove("history");
  $("historyList").hidden = true;
  art.classList.remove("old-version");
  art.hidden = false;
}

$("historyBtn").onclick = () => (historyOpen ? (closeHistory(), loadDoc(true)) : openHistory());
$("historyBack").onclick = () => (viewingVid ? openHistory() : (closeHistory(), loadDoc(true)));
$("restoreBtn").onclick = async () => {
  await api("/api/version/restore", { json: { side: state.side, kind: state.kind, id: state.id, vid: viewingVid } });
  closeHistory();
  await loadDoc();
  toast("Restored. The version before it is still in the history.");
};

// --- full screen and tucking bars away on a phone -----------------------------
// On a phone the bars float over the top of the document and slide away over it, so the text
// only ever moves with their finger. The top of what the user can see is the bottom of the bars.
const visibleTop = () => Math.max($("docScroll").getBoundingClientRect().top, $("docBars").getBoundingClientRect().bottom);
$("fullBtn").onclick = () => document.body.classList.add("full");
$("exitFull").onclick = () => document.body.classList.remove("full");
addEventListener("keydown", (e) => { if (e.key === "Escape") document.body.classList.remove("full"); });
let lastY = 0, switchingUntil = 0;
// Switching between reading and editing moves the scroll position on its own; that isn't their
// scrolling, so the bars stay as they were.
function holdBars() { switchingUntil = Date.now() + 1500; }
$("docScroll").addEventListener("scroll", (e) => {
  const y = e.target.scrollTop;
  if (Date.now() < switchingUntil) { lastY = y; return; }
  if (y > lastY + 8 && y > 60) document.body.classList.add("tucked");
  else if (y < lastY - 8 || y < 20) document.body.classList.remove("tucked");
  lastY = y;
});

// --- suggested edits ----------------------------------------------------------
function scopeChanged() { if (suggesting) return; $("sugBtn").textContent = anchor >= 0 || pickedFig ? "Suggest edits here" : "Suggest edits"; }

let suggesting = false;
function setSuggesting(on) {
  suggesting = on;
  $("sugBtn").disabled = on;
  if (on) $("sugBtn").textContent = "Suggesting...";
  else scopeChanged();
}

$("sugBtn").onclick = async () => {
  const ptr = pointing();
  const scope = ptr ? (ptr.what === "text" ? ptr.in_passage : ptr.caption) || null : null;
  try {
    setSuggesting(true);
    await api("/api/suggest", { json: { side: state.side, kind: state.kind, id: state.id, scope } });
    clearPick();
    setSuggesting(true);
  } catch (err) { setSuggesting(false); toast(err.message); }
};

const KIND_LABEL = { chat: "From your chat", ai: "Sounds like AI", concrete: "Make it concrete", landing: "Land it", rhythm: "Rhythm", trim: "Trim" };
// Markdown in an edit's text, as it reads on the page.
// Line markers (headings, lists, quotes) and footnote marks never show on the page as typed.
const mdText = (md) => md.replace(/^[ \t]*(#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+|\d+\.[ \t]+)/gm, "").replace(/\[\^[^\]\s]+\]/g, "").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/(\*\*|__|\*|_|`)/g, "");

// Each suggested edit sits in the text itself: the old words struck through, the new ones after,
// and a tick and a cross to accept or skip right there. Tapping the edit opens its details:
// why, the other options, and the line of theirs it's based on.
const CHECK = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg>';
const PENCIL = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14 6l4 4"/></svg>';
const CROSS = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const chosenOption = {};

// Where each suggestion sits in their text (`body`): suggestions touching the same words become one
// spot with all of them as ideas. Those without words are additions; those whose words aren't
// there any more are strays.
// A chat change that only adds: it keeps the words it's attached to (`find`) and puts new words
// before or after them. {where, sep, added} (`sep` is what goes between), or null.
function additionOf(s, rep = s.replace || "") {
  if (s.source !== "chat" || !s.find || rep.length <= s.find.length) return null;
  if (rep.startsWith(s.find)) {
    const rest = rep.slice(s.find.length), sep = rest.match(/^\s*/)[0];
    return { where: "after", sep, added: rest.slice(sep.length) };
  }
  if (rep.endsWith(s.find)) {
    const rest = rep.slice(0, rep.length - s.find.length), sep = rest.match(/\s*$/)[0];
    return { where: "before", sep, added: rest.slice(0, rest.length - sep.length) };
  }
  return null;
}
// A chat change the size of paragraphs is shown as a block of its own: "replace" (its paragraphs
// crossed out, the new ones under them), or "after"/"before" (new paragraphs, nothing crossed out).
function blockKind(s) {
  if (s.source !== "chat" || !s.find) return null;
  const add = additionOf(s);
  if (add) return add.sep.includes("\n") ? add.where : null;
  return s.find.includes("\n") || (s.replace || "").includes("\n") ? "replace" : null;
}
// What a chat change puts in, without the words it's attached to when it only adds.
function newPart(s, rep = s.replace || "") { const add = additionOf(s, rep); return add ? add.added : rep; }
// Its full text from the new part the user may have changed in the card.
function fullText(s, part) {
  const add = additionOf(s);
  return !add ? part : add.where === "after" ? s.find + add.sep + part : part + add.sep + s.find;
}
// Markdown paragraphs drawn the way the page shows them (headings and paragraphs).
function blockHtml(md) {
  return md.trim().split(/\n\s*\n/).filter(Boolean).map((chunk) => {
    const h = chunk.match(/^(#{1,4})\s+([\s\S]*)$/);
    if (h) return `<h${h[1].length}>${esc(mdText(h[2]))}</h${h[1].length}>`;
    return `<p>${esc(mdText(chunk.replace(/^(>|[-*+]|\d+\.)\s+/gm, ""))).replace(/\n/g, "<br>")}</p>`;
  }).join("");
}

function suggestionSpots(body) {
  const list = [...((state.doc && state.doc.suggestions) || [])].sort((x, y) => (y.at || 0) - (x.at || 0));
  const spots = [], strays = [], additions = [], blocks = [];
  for (const s of list) {
    if (!s.find) { additions.push(s); continue; }
    if (blockKind(s)) { if (body.includes(s.find)) blocks.push(s); else strays.push(s); continue; }
    const at = body.indexOf(s.find);
    if (at < 0) strays.push(s); else spots.push({ s, at, end: at + s.find.length });
  }
  spots.sort((x, y) => x.at - y.at);
  const groups = [];
  for (const p of spots) {
    const last = groups[groups.length - 1];
    if (last && p.at < last.end) { last.items.push(p.s); last.end = Math.max(last.end, p.end); }
    else groups.push({ start: p.at, end: p.end, items: [p.s] });
  }
  for (const g of groups) {
    g.single = g.items.length === 1 && (g.items[0].source === "chat" || piecesFor(g.items).length < 2);
    if (!g.single) g.items.sort((x, y) => (y.at || 0) - (x.at || 0));
    g.union = body.slice(g.start, g.end);
    g.key = g.items.map((x) => x.id).sort().join(",");
  }
  return { groups, strays, additions, blocks };
}

// One spot drawn on its own, the same as in reading, for the editor to show in place of its words.
let editorSpots = {}, editorBlocks = {};
function drawSpot(key) {
  if (key.startsWith("block:")) { const s = editorBlocks[key]; return s ? blockEdit(s, blockKind(s)) : document.createElement("div"); }
  const g = editorSpots[key], host = document.createElement("span");
  if (!g) return host;
  host.textContent = mdText(g.union);
  const range = document.createRange();
  range.selectNodeContents(host);
  if (g.single) placeEdit(g.items[0], range); else placeGroup(g.items, g.union, range);
  return host;
}

function showSuggestions() {
  closePop();
  const body = (state.doc && state.doc.body) || "";
  let { groups, strays, additions, blocks } = suggestionSpots(body);
  // Paragraph-sized ones first: they only mark whole paragraphs, so the others still find their words.
  for (const s of blocks) if (!placeBlock(s, blockKind(s))) strays.push(s);
  for (const s of additions) placeEdit(s, null);
  for (const g of groups) {
    if (g.single) {
      const range = findText(art, mdText(g.items[0].find));
      if (range) placeEdit(g.items[0], range); else strays.push(g.items[0]);
      continue;
    }
    const range = findText(art, mdText(g.union));
    if (range) placeGroup(g.items, g.union, range); else strays.push(...g.items);
  }
  if (reopenFor && !state.editing) {
    const spot = [...art.querySelectorAll(".edit.group")].find((w) => w.dataset.sid.split(",").some((id) => reopenFor.includes(id)));
    reopenFor = null;
    if (spot) setTimeout(() => spot.querySelector(".vtext").click(), 0);
  }
  // Any whose words aren't in the text as it now reads sit under the paragraph most like them;
  // only one that shares no words with any paragraph is listed at the end. Never hidden.
  const unplaced = [];
  for (const s of strays) {
    const near = nearestBlock(art, mdText(s.find || ""));
    if (!near) { unplaced.push(s); continue; }
    let box = near.nextElementSibling;
    if (!box || !box.classList.contains("stray-near")) {
      box = document.createElement("div");
      box.className = "stray stray-near";
      box.innerHTML = `<div class="stray-head">Suggested for an earlier version of this part</div>`;
      near.after(box);
    }
    placeEdit(s, null, box);
  }
  strays = unplaced;
  if (strays.length) {
    const box = document.createElement("div");
    box.className = "stray";
    box.innerHTML = `<div class="stray-head">More suggestions (the words they were for have changed or moved)</div>`;
    const footnotes = art.querySelector(".footnote");
    footnotes ? footnotes.before(box) : art.append(box);
    for (const s of strays) placeEdit(s, null, box);
  }
}

// --- several suggestions for the same words ------------------------------------------
// Every change offered for the spot is its own switch: the chat's changes, each small fix, each
// rewrite. The user turns on the ones the user wants; they're combined in the order the user chose them.
const groupOn = {}, groupText = {};

function piecesFor(items) {
  const pieces = [], seen = new Set();
  const add = (s, label, find, rep) => {
    const k = find + "\u0000" + rep;
    if (!find || find === rep || seen.has(k)) return;
    seen.add(k);
    pieces.push({ sid: s.id, label, find, rep, why: s.why || "", chat: s.source === "chat" });
  };
  for (const s of items) {
    if (s.source === "chat") { add(s, "From your chat", s.find, s.replace || ""); continue; }
    const label = KIND_LABEL[s.kind] || "Edit";
    smallOf(s).forEach((f) => add(s, label + " · small fix", f.from, f.to));
    rewritesOf(s).forEach((r) => add(s, label + " · rewrite", s.find, r));
  }
  return pieces;
}

// The stretch with the switched-on pieces combined word by word, in the order the user switched them
// on: each piece's changed words go in, and where two change the same words the later one wins.
// `applied` is the pieces with words still in; `covered` the ones switched on but wholly replaced.
function composeGroup(union, pieces, on) {
  const lead = union.match(/^\s*/)[0];
  const words = union.slice(lead.length).match(/\S+\s*/g) || [];
  let hunks = [];
  for (const i of on) {
    const p = pieces[i], at = union.indexOf(p.find);
    if (at < 0) continue;
    const variant = union.slice(0, at) + p.rep + union.slice(at + p.find.length);
    const next = wordHunks(words, variant.slice(variant.match(/^\s*/)[0].length).match(/\S+\s*/g) || []).map((h) => ({ ...h, i }));
    const clash = (a, b) => (a.from === a.to && b.from === b.to ? a.from === b.from : a.from < b.to && b.from < a.to) || (a.from === a.to && b.from < a.from && a.from < b.to) || (b.from === b.to && a.from < b.from && b.from < a.to);
    hunks = hunks.filter((h) => !next.some((n) => clash(h, n))).concat(next);
  }
  hunks.sort((a, b) => a.from - b.from || a.to - b.to);
  let text = lead, k = 0;
  for (const h of hunks) { text += words.slice(k, h.from).join("") + h.text; k = h.to; }
  text += words.slice(k).join("");
  // A last word that lost its trailing space or line break to a change keeps the original ending.
  const tail = union.match(/\s*$/)[0];
  if (!text.endsWith(tail)) text = text.trimEnd() + tail;
  const kept = new Set(hunks.map((h) => h.i));
  return { text, applied: on.filter((i) => kept.has(i)), covered: on.filter((i) => !kept.has(i)) };
}
// Where `b` differs from `a`, word by word: [{from, to, text}] replacing a's words from..to.
function wordHunks(a, b) {
  const x = a.map((t) => t.trim()), y = b.map((t) => t.trim()), n = x.length, m = y.length;
  const L = Array.from({ length: n + 1 }, () => new Int16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0, cur = null;
  const flush = () => { if (cur) { cur.to = i; out.push(cur); cur = null; } };
  while (i < n || j < m) {
    if (i < n && j < m && x[i] === y[j]) { flush(); i++; j++; continue; }
    if (!cur) cur = { from: i, to: i, text: "" };
    if (j >= m || (i < n && L[i + 1][j] >= L[i][j + 1])) i++;
    else cur.text += b[j++];
  }
  flush();
  return out;
}

function groupState(key, union, pieces) {
  if (!groupOn[key]) groupOn[key] = pieces.length ? [0] : [];
  return groupText[key] ?? composeGroup(union, pieces, groupOn[key]).text;
}

function placeGroup(items, union, range) {
  const key = items.map((s) => s.id).sort().join(",");
  const pieces = piecesFor(items);
  const wrap = document.createElement("span");
  wrap.className = "edit vedit group";
  wrap.dataset.sid = key;
  range.deleteContents();
  range.insertNode(wrap);
  const text = document.createElement("span");
  text.className = "vtext";
  wrap.append(text);
  showDiff(text, union, groupState(key, union, pieces));
  const more = document.createElement("span");
  more.className = "more";
  more.textContent = pieces.length > 1 ? `${pieces.length} suggestions` : "1 suggestion";
  wrap.append(more);
  const acts = document.createElement("span");
  acts.className = "edit-acts";
  acts.innerHTML = `<button class="edit-btn ok" title="Accept">${CHECK}</button><button class="edit-btn no" title="Skip all">${CROSS}</button>`;
  wrap.append(acts);
  acts.querySelector(".ok").addEventListener("click", (e) => { e.stopPropagation(); acceptGroup(items, union, key, pieces); });
  acts.querySelector(".no").addEventListener("click", (e) => { e.stopPropagation(); skipGroup(items); });
  wrap.addEventListener("click", (e) => { if (!e.target.closest(".edit-btn")) { e.stopPropagation(); openGroupCard(items, union, pieces, wrap, key); } });
}

function openGroupCard(items, union, pieces, wrap, key) {
  const pop = $("sugPop");
  const notes = items.filter((s) => s.source !== "chat" && !smallOf(s).length && !rewritesOf(s).length && s.why);
  const shownWhy = new Set();
  pop.className = "sug-pop sheet";
  pop.innerHTML = `<div class="kind-label">${pieces.length} suggestions</div>`
    + `<div class="sec">Will read</div><textarea class="own" rows="2"></textarea>`
    + `<div class="fixes">${pieces.map((p, i) => {
      const why = p.why && !shownWhy.has(p.sid) ? (shownWhy.add(p.sid), `<span class="iw">${esc(p.why)}</span>`) : "";
      return `<div class="piece-row"><button class="fix piece${p.chat ? " chat" : ""}" data-i="${i}"><span class="box">${CHECK}</span><span class="body"><span class="lbl">${esc(p.label)}</span><span class="txt">${diffHtml(p.find, p.rep)}</span>${why}</span></button><button class="edit-btn no piece-skip" data-i="${i}" title="Skip this one">${CROSS}</button></div>`;
    }).join("")}</div>`
    + notes.map((s) => `<div class="src">Note: ${esc(s.why)}</div>`).join("")
    + `<div class="acts"><button class="btn primary" data-act="accept">Accept selected</button><button class="btn" data-act="skip">Skip all</button></div>`;
  const box = pop.querySelector(".own"), vtext = wrap.querySelector(".vtext");
  const fit = () => { box.style.height = "auto"; box.style.height = box.scrollHeight + 2 + "px"; };
  const draw = () => {
    box.value = groupState(key, union, pieces);
    showDiff(vtext, union, box.value); fit();
    // Switched on but every word of it changed again by one switched on later: dimmed.
    const { covered } = composeGroup(union, pieces, groupOn[key]);
    pop.querySelectorAll(".piece").forEach((b) => {
      const i = +b.dataset.i, on = groupOn[key].includes(i);
      b.classList.toggle("on", on);
      b.classList.toggle("blocked", covered.includes(i));
    });
    const n = groupOn[key].length - covered.length;
    pop.querySelector('[data-act="accept"]').textContent = n > 1 ? `Accept ${n} selected` : "Accept selected";
  };
  pop.querySelectorAll(".piece").forEach((b) => (b.onclick = () => {
    const i = +b.dataset.i, on = groupOn[key];
    // Any can be on together; the one switched on last wins where they change the same words.
    groupOn[key] = on.includes(i) ? on.filter((x) => x !== i) : [...on, i];
    delete groupText[key];
    draw();
  }));
  pop.querySelectorAll(".piece-skip").forEach((b) => (b.onclick = () => skipPiece(pieces[+b.dataset.i], key)));
  box.oninput = () => { groupText[key] = box.value; showDiff(vtext, union, box.value); fit(); };
  box.onfocus = () => { sitSheet(pop, wrap); setTimeout(() => sitSheet(pop, wrap), 350); };
  pop.querySelector('[data-act="accept"]').onclick = () => acceptGroup(items, union, key, pieces);
  pop.querySelector('[data-act="skip"]').onclick = () => skipGroup(items);
  document.querySelectorAll(".edit.open").forEach((x) => x.classList.remove("open"));
  wrap.classList.add("open");
  pop.hidden = false;
  draw();
  sitSheet(pop, wrap);
}

async function acceptGroup(items, union, key, pieces) {
  await saveTyping();
  const text = groupState(key, union, pieces);
  if (text === union) { toast("Nothing selected"); return; }
  const on = groupText[key] === undefined ? composeGroup(union, pieces, groupOn[key] || []).applied : groupOn[key] || [];
  const applied = on.map((i) => ({ sid: pieces[i].sid, find: pieces[i].find, rep: pieces[i].rep }));
  closePop();
  const where = { side: state.side, kind: state.kind, id: state.id };
  try {
    const res = await api("/api/suggestion", { json: { ...where, sid: items[0].id, action: "accept_group", sids: items.map((s) => s.id), applied, text } });
    delete groupText[key]; delete groupOn[key];
    await loadDoc(true);
    toast("Accepted", () => undoSnapshot(where, items[0].id, res.token));
  } catch (err) { toast(err.message); }
}

// Set aside one change from the card; the rest stay. The card stays open on what's left.
async function skipPiece(p, key) {
  await saveTyping();
  const where = { side: state.side, kind: state.kind, id: state.id };
  try {
    const res = await api("/api/suggestion", { json: { ...where, sid: p.sid, action: "skip_piece", find: p.find, rep: p.rep } });
    delete groupText[key]; delete groupOn[key];
    reopenFor = [p.sid];
    closePop();
    await loadDoc(true);
    toast("Skipped", () => undoSnapshot(where, p.sid, res.token));
  } catch (err) { toast(err.message); }
}

async function undoSnapshot(where, sid, token) {
  await saveTyping();
  try {
    await api("/api/suggestion", { json: { ...where, sid, action: "undo_snapshot", token } });
    if (state.id === where.id) await loadDoc(true);
  } catch (err) { toast(err.message); }
}
let reopenFor = null;
function reopenInEditor() {
  if (!reopenFor) return;
  const ids = reopenFor;
  reopenFor = null;
  const spot = [...$("mdsrc").querySelectorAll(".edit.group")].find((w) => w.dataset.sid.split(",").some((id) => ids.includes(id)));
  if (spot) setTimeout(() => spot.querySelector(".vtext").click(), 0);
}
// Before a suggestion changes the file, what the user has typed is saved first.
async function saveTyping() {
  if (!state.editing) return;
  clearTimeout(saveTimer);
  await save();
  await saving;
}

async function skipGroup(items) {
  await saveTyping();
  closePop();
  const where = { side: state.side, kind: state.kind, id: state.id };
  try {
    for (const s of items) await api("/api/suggestion", { json: { ...where, sid: s.id, action: "skip" } });
    await loadDoc(true);
    toast(items.length > 1 ? `Skipped ${items.length} suggestions` : "Skipped", async () => {
      try { await saveTyping(); for (const s of items) await api("/api/suggestion", { json: { ...where, sid: s.id, action: "undo" } }); if (state.id === where.id) await loadDoc(true); }
      catch (err) { toast(err.message); }
    });
  } catch (err) { toast(err.message); }
}

// Put one suggestion in the document: in the text at `range`, or as its own block in `box`.
// A paragraph-sized chat change: the paragraphs it covers crossed out ("replace"), and the new
// ones as a block of their own under them (or above, for "before").
const BLOCKS = "p, li, h1, h2, h3, h4, blockquote, figcaption, td";
function placeBlock(s, kind) {
  const range = findText(art, mdText(s.find));
  if (!range) return false;
  const covered = [...art.querySelectorAll(BLOCKS)].filter((b) => !b.closest(".edit") && range.intersectsNode(b));
  if (!covered.length) return false;
  const top = (b) => { while (b.parentElement && b.parentElement !== art) b = b.parentElement; return b; };
  const wrap = blockEdit(s, kind);
  if (kind === "replace") { covered.forEach((b) => b.classList.add("sug-old")); wrap.dataset.covers = "1"; }
  if (kind === "before") top(covered[0]).before(wrap); else top(covered[covered.length - 1]).after(wrap);
  return true;
}
function blockEdit(s, kind) {
  const wrap = document.createElement("div");
  wrap.className = "edit chat block" + (kind === "replace" ? "" : " add");
  wrap.dataset.sid = s.id;
  const ins = document.createElement("ins");
  ins.className = "sug block";
  ins.innerHTML = blockHtml(newPart(s));
  wrap.append(ins);
  const acts = document.createElement("span");
  acts.className = "edit-acts";
  acts.innerHTML = `<button class="edit-btn ok" title="Accept">${CHECK}</button><button class="edit-btn no" title="Skip">${CROSS}</button>`;
  wrap.append(acts);
  acts.querySelector(".ok").addEventListener("click", (e) => { e.stopPropagation(); decide(s.id, "accept", 0); });
  acts.querySelector(".no").addEventListener("click", (e) => { e.stopPropagation(); decide(s.id, "skip"); });
  wrap.addEventListener("click", (e) => { if (!e.target.closest(".edit-btn")) { e.stopPropagation(); openPop(s, wrap); } });
  return wrap;
}

function placeEdit(s, range, box) {
  const fromChat = s.source === "chat";
  const options = fromChat ? [s.replace || ""] : s.options && s.options.length ? s.options : (s.replace ? [s.replace] : []);
  const flag = !fromChat && options.length === 0;
  const block = !s.find || !range;
  const wrap = document.createElement(block ? "div" : "span");
  wrap.className = "edit" + (flag ? " flag" : "") + (fromChat ? " chat" : "") + (block ? " append" : "");
  wrap.dataset.sid = s.id;
  const put = () => {
    if (range) range.insertNode(wrap);
    else if (box) box.append(wrap);
    else { const footnotes = art.querySelector(".footnote"); footnotes ? footnotes.before(wrap) : art.append(wrap); }
  };
  if (!fromChat && s.find) {
    // A voice edit: the changed words crossed out and the new ones after them, a tick and a
    // cross, and tapping it opens the card. What the card says it will read shows here as it changes.
    wrap.classList.add("vedit");
    if (range) range.deleteContents();
    put();
    const text = document.createElement("span");
    text.className = "vtext";
    wrap.append(text);
    if (cardText[s.id] === undefined) cardText[s.id] = startText(s);
    // A note only the user can act on (no wording offered): their sentence highlighted, until the user types one.
    if (flag && cardText[s.id] === s.find) text.innerHTML = `<mark class="sug">${esc(mdText(s.find))}</mark>`;
    else showDiff(text, s.find, cardText[s.id]);
    const acts = document.createElement("span");
    acts.className = "edit-acts";
    acts.innerHTML = (flag ? "" : `<button class="edit-btn ok" title="Accept">${CHECK}</button>`) + `<button class="edit-btn no" title="${flag ? "Got it" : "Skip"}">${CROSS}</button>`;
    wrap.append(acts);
    acts.querySelector(".ok")?.addEventListener("click", (e) => { e.stopPropagation(); acceptText(s); });
    acts.querySelector(".no").addEventListener("click", (e) => { e.stopPropagation(); decide(s.id, "skip"); });
    wrap.addEventListener("click", (e) => { if (!e.target.closest(".edit-btn")) { e.stopPropagation(); openCard(s, wrap); } });
    return;
  }
  // An addition keeps the words it's attached to as they are and only shows the new ones.
  const addition = range ? additionOf(s, options[chosenOption[s.id] || 0]) : null;
  if (addition) {
    const original = range.extractContents();
    put();
    wrap.append(original);
  } else if (s.find) {
    const old = document.createElement(flag ? "mark" : "del");
    old.className = "sug";
    if (range) old.append(range.extractContents()); else old.textContent = mdText(s.find);
    put();
    wrap.append(old);
  } else put();  // an addition at the end: shown where it would go, below the last paragraph
  if (!flag && options[0]) {
    const ins = document.createElement("ins");
    ins.className = "sug";
    const replacement = options[chosenOption[s.id] || 0];
    ins.textContent = mdText(addition ? (addition.where === "after" ? addition.sep + addition.added : addition.added + addition.sep) : replacement);
    if (addition) ins.style.whiteSpace = "pre-wrap";
    if (addition && addition.where === "before") wrap.prepend(ins); else wrap.append(ins);
  }
  const acts = document.createElement("span");
  acts.className = "edit-acts";
  acts.innerHTML = flag
    ? `<button class="edit-btn no" title="Got it">${CROSS}</button>`
    : `<button class="edit-btn ok" title="Accept">${CHECK}</button><button class="edit-btn no" title="Skip">${CROSS}</button>`;
  wrap.append(acts);
  acts.querySelector(".ok")?.addEventListener("click", (e) => { e.stopPropagation(); decide(s.id, "accept", chosenOption[s.id] || 0); });
  acts.querySelector(".no").addEventListener("click", (e) => { e.stopPropagation(); decide(s.id, "skip"); });
  wrap.addEventListener("click", (e) => { if (!e.target.closest(".edit-btn")) { e.stopPropagation(); openPop(s, wrap); } });
}

function openPop(s, wrap) {
  const pop = $("sugPop");
  pop.className = "sug-pop";
  pop.style.bottom = ""; pop.style.maxHeight = "";
  const options = s.source === "chat" ? [s.replace || ""] : s.options && s.options.length ? s.options : (s.replace ? [s.replace] : []);
  const flag = s.source !== "chat" && options.length === 0;
  const label = KIND_LABEL[s.kind] || "Edit";
  pop.innerHTML = `<div class="kind-label${s.source === "chat" ? " chat" : ""}">${label}</div><div class="why">${esc(s.why || "")}</div>`
    + (options.length > 1 ? `<div class="opts">${options.map((o, i) => `<button class="opt${i === (chosenOption[s.id] || 0) ? " on" : ""}" data-i="${i}">${esc(mdText(o))}</button>`).join("")}</div>` : "")
    + (s.quote ? `<div class="src">Like when you wrote: “${esc(s.quote)}”${s.source_title ? " — " + esc(s.source_title) : ""}</div>` : "")
    + `<div class="acts">${flag ? `<button class="btn" data-act="skip">Got it</button>` : `<button class="btn primary" data-act="accept">Accept</button><button class="btn" data-act="skip">Skip</button>`}<button class="btn icon pencil" data-act="own" title="Change it before accepting">${PENCIL}</button></div>`;
  pop.querySelectorAll(".opt").forEach((b) => (b.onclick = () => {
    chosenOption[s.id] = +b.dataset.i;
    pop.querySelectorAll(".opt").forEach((x) => x.classList.toggle("on", x === b));
    const ins = wrap.querySelector("ins");
    if (ins) ins.textContent = mdText(options[chosenOption[s.id]]);
    place(pop, wrap);
  }));
  pop.querySelector('[data-act="skip"]').onclick = () => decide(s.id, "skip");
  const accept = pop.querySelector('[data-act="accept"]');
  if (accept) accept.onclick = () => decide(s.id, "accept", chosenOption[s.id] || 0);
  // Change the suggestion in their own words first: the chosen option (or, for a note, their
  // original line) becomes a text box, and Accept puts in whatever it says.
  const own = (focus) => {
    const choice = chosenOption[s.id] || 0;
    const box = document.createElement("textarea");
    box.className = "own";
    box.value = flag ? s.find : s.source === "chat" ? newPart(s, options[choice]) : options[choice];
    pop.querySelector(".opts")?.remove();
    pop.querySelector(".why").after(box);
    const fit = () => { box.style.height = "auto"; box.style.height = box.scrollHeight + 2 + "px"; };
    let ins = wrap.querySelector("ins");
    if (!ins) { ins = document.createElement("ins"); ins.className = "sug"; wrap.querySelector(".edit-acts").before(ins); }
    const add = additionOf(s, options[choice]);
    const show = () => {
      if (ins.classList.contains("block")) ins.innerHTML = blockHtml(box.value);
      else ins.textContent = mdText(add ? (add.where === "after" ? add.sep + box.value : box.value + add.sep) : box.value);
    };
    box.oninput = () => { show(); fit(); };
    // Opened this way from the start, it keeps Skip; opened with the pencil, Cancel puts it back.
    pop.querySelector(".acts").innerHTML = `<button class="btn primary" data-act="accept">Accept</button>` + (focus ? `<button class="btn" data-act="cancel">Cancel</button>` : `<button class="btn" data-act="skip">Skip</button>`);
    pop.querySelector('[data-act="accept"]').onclick = () => decide(s.id, "accept", choice, s.source === "chat" ? fullText(s, box.value) : box.value);
    if (focus) pop.querySelector('[data-act="cancel"]').onclick = () => { closePop(); showSuggestionsAgain(); };
    else pop.querySelector('[data-act="skip"]').onclick = () => decide(s.id, "skip");
    fit();
    if (focus) {
      box.focus({ preventScroll: true });
      box.setSelectionRange(box.value.length, box.value.length);
    }
    box.onfocus = () => setTimeout(() => place(pop, wrap), 350);
    place(pop, wrap);
    if (window.visualViewport) visualViewport.addEventListener("resize", () => { if (!pop.hidden) place(pop, wrap); }, { once: true });
  };
  pop.querySelector('[data-act="own"]').onclick = () => own(true);
  document.querySelectorAll(".edit.open").forEach((x) => x.classList.remove("open"));
  wrap.classList.add("open");
  pop.hidden = false;
  place(pop, wrap);
  // A change from the chat opens ready to edit: the user almost always reworks it before accepting.
  if (s.source === "chat") own(false);
}

// What each voice edit will put in, as the user shapes it in the card (kept until the user decides).
const cardText = {};
const smallOf = (s) => s.small || [];
const rewritesOf = (s) => (s.small ? s.rewrites || [] : s.options || []);
function startText(s) {
  const small = smallOf(s);
  if (small.length) return small.reduce((t, f) => t.replace(f.from, f.to), s.find);
  return rewritesOf(s)[0] ?? s.find;
}
function acceptText(s) {
  const text = cardText[s.id] ?? startText(s);
  return text === s.find ? decide(s.id, "skip") : decide(s.id, "accept", 0, text);
}

// Word by word: words that stay are plain, removed ones crossed out, new ones in green.
// Each word carries its trailing space; within a run of changes, removals come before additions.
function wordDiff(a, b) {
  const x = a.match(/\S+\s*/g) || [], y = b.match(/\S+\s*/g) || [];
  const xw = x.map((t) => t.trim()), yw = y.map((t) => t.trim());
  const n = x.length, m = y.length, L = Array.from({ length: n + 1 }, () => new Int16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = xw[i] === yw[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let dels = "", inss = "";
  const flush = () => { if (dels) out.push({ type: "del", text: dels }); if (inss) out.push({ type: "ins", text: inss }); dels = inss = ""; };
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && xw[i] === yw[j]) { flush(); out.push({ type: "same", text: x[i] }); i++; j++; }
    else if (j >= m || (i < n && L[i + 1][j] >= L[i][j + 1])) dels += x[i++];
    else inss += y[j++];
  }
  flush();
  return out;
}
function showDiff(el, from, to) { el.innerHTML = diffHtml(from, to); }
function diffHtml(from, to) {
  return wordDiff(mdText(from), mdText(to)).map((p) => {
    if (p.type === "same") return esc(p.text);
    const core = p.text.trimEnd(), trail = p.text.slice(core.length);
    const tag = p.type === "del" ? "del" : "ins";
    return `<${tag} class="sug">${esc(core)}</${tag}>${esc(trail)}`;
  }).join("");
}

// The card for a voice edit, at the bottom of the screen: why, what the sentence will read
// (editable), the small fixes as switches that can be combined, and the fuller rewrites.
function openCard(s, wrap) {
  const pop = $("sugPop");
  const small = smallOf(s), rewrites = rewritesOf(s);
  // Start from what's showing in the text: all small fixes on, or the first rewrite.
  const on = small.map(() => true);
  let rw = small.length ? -1 : 0;
  const compose = () => (rw >= 0 ? rewrites[rw] : small.reduce((t, f, i) => (on[i] ? t.replace(f.from, f.to) : t), s.find));
  pop.className = "sug-pop sheet";
  pop.innerHTML = `<div class="kind-label">${KIND_LABEL[s.kind] || "Edit"}</div><div class="why">${esc(s.why || "")}</div>`
    + `<div class="sec">Will read</div><textarea class="own" rows="2"></textarea>`
    + (small.length ? `<div class="sec">Small fixes</div><div class="fixes">${small.map((f, i) => `<button class="fix" data-i="${i}"><span class="box">${CHECK}</span><span><del>${esc(mdText(f.from))}</del> <span class="arrow">→</span> <ins>${esc(mdText(f.to)) || "<i>cut</i>"}</ins></span></button>`).join("")}</div>` : "")
    + (rewrites.length ? `<div class="sec">${small.length ? "Or rewrite it" : "Rewrites"}</div><div class="opts">${rewrites.map((r, i) => `<button class="opt" data-r="${i}">${esc(mdText(r))}</button>`).join("")}</div>` : "")
    + (s.quote ? `<div class="src">Like when you wrote: “${esc(s.quote)}”${s.source_title ? " — " + esc(s.source_title) : ""}</div>` : "")
    + `<div class="acts"><button class="btn primary" data-act="accept">Accept</button><button class="btn" data-act="skip">${small.length || rewrites.length ? "Skip" : "Got it"}</button></div>`;
  const box = pop.querySelector(".own");
  const fit = () => { box.style.height = "auto"; box.style.height = box.scrollHeight + 2 + "px"; };
  const vtext = wrap.querySelector(".vtext");
  const show = (t) => { cardText[s.id] = t; showDiff(vtext, s.find, t); };
  const draw = (keep) => {
    if (!keep) box.value = compose();
    fit(); show(box.value);
    pop.querySelectorAll(".fix").forEach((b) => b.classList.toggle("on", rw < 0 && on[+b.dataset.i]));
    pop.querySelectorAll(".opt").forEach((b) => b.classList.toggle("on", rw === +b.dataset.r));
  };
  pop.querySelectorAll(".fix").forEach((b) => (b.onclick = () => { if (rw >= 0) rw = -1; else on[+b.dataset.i] = !on[+b.dataset.i]; draw(); }));
  pop.querySelectorAll(".opt").forEach((b) => (b.onclick = () => { rw = rw === +b.dataset.r ? -1 : +b.dataset.r; draw(); }));
  box.oninput = () => { fit(); show(box.value); };
  box.onfocus = () => { sitSheet(pop, wrap); setTimeout(() => sitSheet(pop, wrap), 350); };
  pop.querySelector('[data-act="accept"]').onclick = () => acceptText(s);
  pop.querySelector('[data-act="skip"]').onclick = () => decide(s.id, "skip");
  document.querySelectorAll(".edit.open").forEach((x) => x.classList.remove("open"));
  wrap.classList.add("open");
  pop.hidden = false;
  // Reopening shows what the user left it at.
  const kept = cardText[s.id];
  draw();
  if (kept !== undefined && kept !== box.value) { box.value = kept; draw(true); pop.querySelectorAll(".fix, .opt").forEach((b) => b.classList.remove("on")); }
  sitSheet(pop, wrap);
}

// Above the phone's keyboard when it's up, and scroll the sentence into view above the card.
// While the user types in the card on a phone, the card takes the whole space above the keyboard
// (the box the user is typing in at its top, the rest scrolling inside the card), so nothing the user is
// working on is under the keyboard.
function sitSheet(pop, wrap) {
  const vv = window.visualViewport;
  const visible = vv ? vv.height : innerHeight;
  const typing = vv && innerHeight - vv.height > 120 && pop.contains(document.activeElement);
  pop.classList.toggle("typing", !!typing);
  pop.style.bottom = (vv ? Math.max(0, innerHeight - vv.height - vv.offsetTop) : 0) + 10 + "px";
  if (typing) {
    pop.style.top = vv.offsetTop + 10 + "px";
    pop.style.maxHeight = "none";
    pop.querySelector(".own").style.maxHeight = Math.round(visible * 0.45) + "px";
    return;
  }
  pop.style.top = "";
  const own = pop.querySelector(".own");
  if (own) own.style.maxHeight = "";
  pop.style.maxHeight = Math.round(visible * 0.62) + "px";
  const cardTop = pop.getBoundingClientRect().top, r = wrap.getBoundingClientRect();
  const scroller = $("docScroll"), barBottom = visibleTop();
  if (r.bottom > cardTop - 12 || r.top < barBottom) scroller.scrollTop += r.top - Math.max(barBottom + 12, cardTop - 12 - r.height);
}
const resitSheet = () => {
  const pop = $("sugPop"), wrap = document.querySelector(".edit.open");
  if (!pop.hidden && pop.classList.contains("sheet") && wrap) sitSheet(pop, wrap);
};
// The keyboard opening resizes the visible area, and a phone also scrolls it to show the box.
if (window.visualViewport) { visualViewport.addEventListener("resize", resitSheet); visualViewport.addEventListener("scroll", resitSheet); }
$("sugPop").addEventListener("focusout", () => setTimeout(resitSheet, 50));

// Under the edit when there's room, else above it; always on screen.
function place(pop, target) {
  const r = target.getBoundingClientRect(), vw = innerWidth, vh = window.visualViewport ? visualViewport.height : innerHeight;
  const w = Math.min(380, vw - 24);
  pop.style.width = w + "px";
  pop.style.left = Math.max(12, Math.min(r.left, vw - w - 12)) + "px";
  const h = pop.offsetHeight;
  const below = r.bottom + 8, above = r.top - h - 8;
  pop.style.top = (below + h < vh - 12 || above < 12 ? Math.min(below, vh - h - 12) : above) + "px";
}

function closePop() {
  const was = !$("sugPop").hidden;
  $("sugPop").hidden = true;
  document.querySelectorAll(".edit.open").forEach((x) => x.classList.remove("open"));
  if (was && pendingRefresh) setTimeout(refreshDoc, 0);
}
// Judged by where the tap landed when it happened: a button in the pop-up may have been redrawn since.
document.addEventListener("click", (e) => { if (!e.composedPath().some((el) => el.id === "sugPop" || el.classList?.contains("edit"))) closePop(); });
$("docScroll").addEventListener("scroll", () => { if (!$("sugPop").classList.contains("sheet")) closePop(); }, { passive: true });

// The top-level paragraph of the page sharing the most words with `text` (null if none shares any).
function nearestBlock(root, text) {
  const wordsOf = (t) => new Set(t.toLowerCase().match(/[\p{L}\p{N}’']+/gu) || []);
  const want = wordsOf(text);
  if (!want.size) return null;
  let best = null, bestScore = 0;
  for (const b of root.querySelectorAll(BLOCKS)) {
    if (b.closest(".edit, .stray, .footnote")) continue;
    let top = b; while (top.parentElement && top.parentElement !== root) top = top.parentElement;
    const have = wordsOf(b.textContent);
    let shared = 0; for (const w of want) if (have.has(w)) shared++;
    const score = shared / want.size + shared / Math.max(have.size, 1) / 100;  // most of its words, then the tighter fit
    if (shared >= Math.min(3, want.size) && score > bestScore) { best = top; bestScore = score; }
  }
  return best;
}

function findText(root, needle) {
  // Compared with runs of spaces and line breaks counted as one space, the way the page shows them.
  needle = (needle || "").replace(/\s+/g, " ").trim();
  if (!needle) return null;
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.parentElement.closest(".edit, sup, .stray") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
  const map = []; let text = "", lastSpace = false;
  while (walk.nextNode()) {
    const node = walk.currentNode, t = node.textContent;
    for (let k = 0; k < t.length; k++) {
      const space = /\s/.test(t[k]);
      if (space && lastSpace) continue;
      text += space ? " " : t[k]; map.push([node, k]); lastSpace = space;
    }
  }
  const at = text.indexOf(needle);
  if (at < 0) return null;
  const [sn, so] = map[at], [en, eo] = map[at + needle.length - 1];
  const range = document.createRange();
  range.setStart(sn, so); range.setEnd(en, eo + 1);
  return range;
}

// Put the edits back as they were (after the user started changing one and cancelled).
function showSuggestionsAgain() { renderArticle(); }

async function decide(sid, action, option = 0, text) {
  await saveTyping();
  closePop();
  try {
    const where = { side: state.side, kind: state.kind, id: state.id };
    await api("/api/suggestion", { json: { ...where, sid, action, option, ...(text === undefined ? {} : { text }) } });
    delete chosenOption[sid];
    delete cardText[sid];
    await loadDoc(true);
    toast(action === "accept" ? "Edit accepted" : "Edit skipped", async () => {
      try { await saveTyping();
        await api("/api/suggestion", { json: { ...where, sid, action: "undo" } });
        if (state.side === where.side && state.id === where.id) await loadDoc(true);
      } catch (err) { toast(err.message); }
    });
  } catch (err) { toast(err.message); }
}

// ---------------------------------------------------------------------------
// The chat (Talk), and what the agent says back
// ---------------------------------------------------------------------------
async function startChat() {
  try {
    const chat = await api("/api/chat");
    if (!chat.chat_id) throw new Error(chat.error || "");
    // The chat app has its own address: ours with the chat's label in place of our own.
    const host = location.host.replace(chat.own_label, chat.chat_label);
    const src = host === location.host ? `${chat.local_url}/${chat.chat_id}` : `${location.protocol}//${host}/${chat.chat_id}`;
    $("chatFrame").src = src;
    $("chatFrame").hidden = false;
    $("talkMsg").hidden = true;
  } catch (err) {
    $("talkMsg").textContent = err.message || "Your writing chat couldn't start. Reload to try again.";
  }
}

// ---------------------------------------------------------------------------
// The mic: tap to talk, tap again to send; while a reply is read aloud, tap stops it.
// ---------------------------------------------------------------------------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const mic = $("mic");
let rec = null, listening = false, finalText = "", interimText = "", voiceOn = localStorage.getItem("ttw-voice") !== "off";
$("voiceToggle").classList.toggle("off", !voiceOn);

function refreshBubble() {
  $("discardRec").hidden = !listening || !$("liveText").textContent;
  $("bubble").hidden = $("about").hidden && !$("liveText").textContent;
}
function showLive() {
  const box = $("liveText");
  // Follow the newest words, unless the user has scrolled up to reread.
  const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  box.innerHTML = finalText || interimText ? `<span class="final">${esc(finalText)}</span> ${esc(interimText)}` : "";
  refreshBubble();
  if (atEnd) box.scrollTop = box.scrollHeight;
}

function startListening() {
  if (!SR) { toast("This browser can't turn speech into text. Use the keyboard's dictation key in the chat instead."); return; }
  primeSpeech();
  finalText = ""; interimText = "";
  listening = true;
  mic.classList.add("rec"); mic.title = "Send";
  keepAwake();
  const begin = () => {
    rec = new SR();
    // A recording the user discarded can still report or end after the user has started the next one: ignored.
    const me = rec;
    rec.lang = navigator.language || "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e) => {
      if (rec !== me) return;
      interimText = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText = (finalText + " " + r[0].transcript).trim();
        else interimText += r[0].transcript;
      }
      showLive();
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        listening = false; stopUi();
        toast(window.top !== window
          ? "The mic can't be used inside a workspace window yet. Talk from your phone, or open Talk to Write in its own browser tab."
          : "The microphone isn't allowed for this page. Allow it in your browser's settings.");
      }
    };
    // Phones stop listening after a pause; keep going until the mic is tapped again.
    rec.onend = () => { if (rec !== me) return; if (listening) { try { begin(); } catch { /* already restarting */ } } else finishSend(); };
    rec.start();
  };
  begin();
}

function stopUi() { mic.classList.remove("rec"); mic.title = "Talk"; releaseAwake(); }

// While the mic is on, keep the screen from dimming and locking: a phone stops a page's
// microphone once the screen goes off. (Pressing the side button still stops it.)
let awake = null;
async function keepAwake() {
  if (!listening || awake || !navigator.wakeLock || document.visibilityState !== "visible") return;
  try {
    awake = await navigator.wakeLock.request("screen");
    awake.addEventListener("release", () => { awake = null; });
    if (!listening) releaseAwake();
  } catch { awake = null; /* low battery mode, or not allowed */ }
}
function releaseAwake() { if (awake) { awake.release().catch(() => {}); awake = null; } }
// The phone drops the hold whenever the page is hidden; take it back on return if still listening.
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") keepAwake(); });

let sendQueued = false;
function stopAndSend() {
  listening = false; stopUi(); sendQueued = true;
  mic.classList.add("sending");
  try { rec.stop(); } catch { finishSend(); }
  setTimeout(() => { if (sendQueued) finishSend(); }, 1500);
}

async function finishSend() {
  if (!sendQueued) return;
  sendQueued = false;
  mic.classList.remove("sending");
  const text = (finalText + " " + interimText).trim();
  finalText = interimText = "";
  showLive();
  if (text) await send(text);
}

// Throw the recording away instead of sending it. Undo sends it after all.
function discardRecording() {
  const text = (finalText + " " + interimText).trim();
  listening = false; sendQueued = false; stopUi();
  try { rec.abort(); } catch { /* already stopped */ }
  rec = null;
  finalText = interimText = "";
  showLive();
  toast("Recording discarded", text ? () => send(text) : undefined);
}
$("discardRec").onclick = discardRecording;

mic.onclick = () => {
  if (mic.classList.contains("speaking")) { stopSpeaking(); return; }
  if (listening) stopAndSend(); else startListening();
};

async function send(text, extra = {}) {
  try {
    await api("/api/say", { json: { text, how: extra.action ? "button" : "spoken", context: { ...screen(), ...extra } } });
    if (!isDesktop() && document.body.dataset.tab !== "talk" && !extra.action) { /* stay where you are: the agent edits what you see */ }
  } catch (err) { toast(err.message); }
}

// What the agent should know about the screen.
function screen() {
  const s = { tab: document.body.dataset.tab, side: state.side === "k" ? "garden" : "canvas", view: state.view };
  if (state.view === "doc" || state.view === "page") s.item = { kind: state.kind, id: state.id, title: state.title };
  if (state.side === "k" && state.view === "list" && $("kSearch").value.trim()) s.search = $("kSearch").value.trim();
  const ptr = state.view === "doc" ? pointing() : null;
  if (ptr) s.pointing = ptr;
  if (!BESIDE && besideOpen() && besideScreen) s.beside = besideScreen;
  return s;
}
let screenTimer;
function reportScreen() {
  clearTimeout(screenTimer);
  screenTimer = setTimeout(() => (BESIDE ? toOuter({ type: "screen", screen: screen() }) : api("/api/screen", { json: screen() }).catch(() => {})), 300);
}

// --- reading replies aloud ---
let speakingCount = 0;
function primeSpeech() {
  // iPhones only let a page speak after a tap; an empty utterance during the tap unlocks it.
  if (primeSpeech.done || !window.speechSynthesis) return;
  primeSpeech.done = true;
  speechSynthesis.speak(new SpeechSynthesisUtterance(""));
}
function speak(text) {
  if (!voiceOn || !window.speechSynthesis || !text.trim()) return;
  const plain = text.replace(/```[\s\S]*?```/g, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/[*_`#>|]/g, "").trim();
  if (!plain) return;
  const u = new SpeechSynthesisUtterance(plain);
  u.rate = 1.05;
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith((navigator.language || "en").slice(0, 2)));
  const nice = voices.find((v) => /premium|enhanced|natural|samantha|ava|allison|google us/i.test(v.name)) || voices[0];
  if (nice) u.voice = nice;
  u.onstart = () => { speakingCount++; if (!listening) { mic.classList.add("speaking"); mic.title = "Stop the reply"; } };
  u.onend = u.onerror = () => { speakingCount = Math.max(0, speakingCount - 1); if (!speakingCount) { mic.classList.remove("speaking"); mic.title = "Talk"; } };
  speechSynthesis.speak(u);
}
function stopSpeaking() { speechSynthesis.cancel(); speakingCount = 0; mic.classList.remove("speaking"); mic.title = "Talk"; }
$("voiceToggle").onclick = () => {
  voiceOn = !voiceOn;
  localStorage.setItem("ttw-voice", voiceOn ? "on" : "off");
  $("voiceToggle").classList.toggle("off", !voiceOn);
  $("voiceToggle").title = voiceOn ? "Replies are read aloud. Tap to mute." : "Replies are muted. Tap to hear them.";
  if (!voiceOn) stopSpeaking(); else primeSpeech();
};

// --- live updates: replies to speak, and documents the agent changed ---
function listen() {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === "hello" && ev.version !== window.PAGE_VERSION && !reloading) { reloading = true; reloadWhenIdle(); }
    if (ev.type === "reply") speak(ev.text);
    if (ev.type === "suggesting" && state.view === "doc" && ev.side === state.side && ev.id === state.id) {
      if (ev.state === "started") setSuggesting(true);
      if (ev.state === "done") {
        setSuggesting(false);
        toast(ev.error ? "Couldn't make edits just now. Try again." : ev.count ? `${ev.count} edit${ev.count === 1 ? "" : "s"} to look at.` : "No edits to suggest here.");
      }
    }
    if (ev.type === "changed") {
      if (state.view === "doc" && ev.side === state.side && ev.id === state.id) refreshDoc();
      else if (state.view === "page" && ev.side === "canvas" && ev.id === state.id) loadPage();
      else if (state.view === "list" && ev.side === state.side) (state.side === "k" ? loadK() : loadCanvas());
      else if (state.view === "trash") loadTrash();
    }
  };
  es.onerror = () => { es.close(); setTimeout(listen, 3000); };
}

// Live updates can be missed (a phone pauses the page, a connection drops quietly), so while a
// document is open the page also checks every few seconds whether it or its edits changed.
// It never reloads under them: while the user is typing, in history, or has an edit open, it waits.
let pendingRefresh = false;
function refreshDoc() {
  if (state.view !== "doc" || !state.doc) return;
  if (state.editing || historyOpen || !$("sugPop").hidden) { pendingRefresh = true; return; }
  pendingRefresh = false;
  loadDoc(true);
}
async function checkStamp() {
  if (document.visibilityState !== "visible" || state.view !== "doc" || !state.doc) return;
  try {
    const { stamp } = await api("/api/stamp?" + docQuery());
    if (state.doc && stamp !== state.doc.stamp) refreshDoc();
  } catch { /* offline for a moment */ }
}
setInterval(checkStamp, 4000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") checkStamp(); });

// A newer version of the app is out: reload as soon as the user isn't typing, talking, or deciding.
function reloadWhenIdle() {
  const busy = state.editing || listening || mic.classList.contains("speaking") || !$("sugPop").hidden;
  if (busy) { setTimeout(reloadWhenIdle, 5000); return; }
  location.reload();
}
// Check for a newer version whenever the user comes back to the page (phones pause pages in the background).
let reloading = false;
async function checkVersion() {
  if (reloading || document.visibilityState !== "visible") return;
  try { const v = await api("/api/page-version"); if (v.version !== window.PAGE_VERSION) { reloading = true; reloadWhenIdle(); } } catch { /* offline */ }
}
document.addEventListener("visibilitychange", checkVersion);

if (!location.hash) history.replaceState({}, "", places.last || "#canvas");
route();
if (!BESIDE) startChat();
listen();

// Settings: the modes and dials that steer how the writing chat talks back.
const SET_MODES = [
  ["off", "Normal", "Nothing in particular: it answers however fits."],
  ["find", "Find what I mean", "Reflects back the idea under what you said, and asks if that's it."],
  ["clarify", "Clarify", "Points at the fuzziest word or claim and asks what you mean."],
  ["expand", "Expand", "Shows the bigger version of the idea."],
  ["challenge", "Challenge", "Gives the strongest objection."],
  ["usable", "Make it usable", "Turns it toward something you could do or decide."],
];
const SET_DIALS = [["affirm", "Affirm", "Challenge"], ["explore", "Explore", "Solve"], ["grounded", "Grounded", "Wild"], ["fast", "Fast", "Deep"], ["asks", "Asks first", "Goes ahead"]];
let talkSettings = null, settingsTimer = 0;
function drawSettings() {
  const s = talkSettings, modes = $("setModes"), dials = $("setDials");
  modes.innerHTML = "";
  for (const [key, label] of SET_MODES) {
    const b = document.createElement("button");
    b.textContent = label; b.classList.toggle("on", s.mode === key);
    b.onclick = () => { s.mode = key; drawSettings(); saveSettings(); };
    modes.appendChild(b);
  }
  const note = document.createElement("div"); note.className = "set-mode-note";
  note.textContent = SET_MODES.find((m) => m[0] === s.mode)[2]; modes.appendChild(note);
  dials.innerHTML = "";
  for (const [key, left, right] of SET_DIALS) {
    const d = document.createElement("div"); d.className = "set-dial";
    d.innerHTML = `<div class="ends"><span>${left}</span><span>${right}</span></div>`;
    const r = document.createElement("input"); r.type = "range"; r.min = 0; r.max = 100; r.value = s.dials[key];
    r.oninput = () => { s.dials[key] = +r.value; saveSettings(); };
    d.appendChild(r); dials.appendChild(d);
  }
}
function saveSettings() {
  $("setStatus").textContent = "";
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(async () => {
    const r = await fetch("/api/talk-settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(talkSettings) });
    $("setStatus").textContent = r.ok ? "Saved" : "Couldn't save";
  }, 300);
}
async function openSettings() {
  talkSettings = await (await fetch("/api/talk-settings")).json();
  drawSettings(); $("setStatus").textContent = "";
  $("settingsPane").hidden = false; $("settingsBtn").classList.add("on");
}
function closeSettings() { $("settingsPane").hidden = true; $("settingsBtn").classList.remove("on"); }
$("settingsBtn").onclick = () => ($("settingsPane").hidden ? openSettings() : closeSettings());
$("settingsClose").onclick = closeSettings;
$("setReset").onclick = () => { talkSettings = { ...talkSettings, mode: "off", dials: Object.fromEntries(SET_DIALS.map((d) => [d[0], 50])) }; drawSettings(); saveSettings(); };

// Who's in the conversation: people the user brings in, whom the writing chat speaks for.
const ROOM_IDEAS = ["Kim Kardashian", "Rick Rubin", "Björk", "Werner Herzog", "Ira Glass", "Stewart Brand", "Miuccia Prada"];
const ROOM_COLORS = ["#C2452D", "#3D6B8C", "#5E7A3A", "#8A5A9E"];
const ROOM_MAX = 4;
function drawRoom() {
  const room = talkSettings?.room || [], box = $("roomStrip");
  box.innerHTML = "";
  const add = (cls, text) => { const e = document.createElement("span"); e.className = cls; e.textContent = text; box.appendChild(e); return e; };
  add("lbl", "In the conversation:"); add("who", "You"); add("who", "Writing partner");
  room.forEach((name, i) => {
    const c = add("chip", name); c.style.background = ROOM_COLORS[i % ROOM_COLORS.length];
    const x = document.createElement("button"); x.textContent = "×"; x.title = `Ask ${name} to leave`;
    x.onclick = () => setRoom(room.filter((n) => n !== name), `${name} left`);
    c.appendChild(x);
  });
  if (room.length < ROOM_MAX) {
    const b = document.createElement("button"); b.className = "add"; b.textContent = "+ Add someone";
    b.onclick = () => { const p = $("roomPicker"); p.hidden = !p.hidden; if (!p.hidden) { drawRoomIdeas(); $("roomName").focus(); } };
    box.appendChild(b);
  }
}
function drawRoomIdeas() {
  const box = $("roomIdeas"); box.innerHTML = "";
  for (const name of ROOM_IDEAS.filter((n) => !(talkSettings.room || []).includes(n))) {
    const b = document.createElement("button"); b.textContent = name; b.onclick = () => joinRoom(name); box.appendChild(b);
  }
}
function joinRoom(name) {
  name = name.trim(); const room = talkSettings.room || [];
  if (!name) return;
  $("roomPicker").hidden = true; $("roomName").value = "";
  if (room.includes(name) || room.length >= ROOM_MAX) return;
  setRoom([...room, name], `${name} joined. They'll speak from your next message.`);
}
async function setRoom(room, said) {
  talkSettings.room = room; drawRoom();
  const r = await fetch("/api/talk-settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(talkSettings) });
  if (r.ok) { talkSettings = await r.json(); drawRoom(); toast(said); } else toast("Couldn't save that");
}
$("roomName").addEventListener("keydown", (e) => {
  if (e.key === "Enter") joinRoom(e.target.value);
  if (e.key === "Escape") $("roomPicker").hidden = true;
});
fetch("/api/talk-settings").then((r) => r.json()).then((s) => { talkSettings = talkSettings || s; drawRoom(); });

// ---------------------------------------------------------------------------
// Side by side (laptop only): a second document, list or page in a pane to the right.
// It stays open, on whatever it last showed, until the user closes it.
// ---------------------------------------------------------------------------
let besideScreen = null;
const besideOpen = () => !$("besidePane").hidden;
function openBeside(hash) {
  if (BESIDE) { toOuter({ type: "open", hash }); return; }
  if (!isDesktop()) { goHash(hash); return; }
  if (besideOpen()) $("besideFrame").contentWindow.postMessage({ ttw: true, type: "go", hash }, location.origin);
  else { $("besideFrame").src = "/?beside=1" + hash; $("besidePane").hidden = false; document.body.classList.add("two"); }
  places.beside = hash; keepPlaces();
}
function closeBeside() {
  $("besidePane").hidden = true; $("besideFrame").src = "about:blank";
  document.body.classList.remove("two");
  besideScreen = null; delete places.beside; keepPlaces();
  reportScreen();
}
$("besideBtn").onclick = () => openBeside(hashFor(state.side, "list"));
$("closeBeside").onclick = () => toOuter({ type: "close" });
addEventListener("message", (e) => {
  const m = e.data;
  if (e.origin !== location.origin || !m || !m.ttw) return;
  if (BESIDE) {
    if (m.type === "go") goHash(m.hash);
    if (m.type === "clear-pick") { clearPick(); reportScreen(); }
    return;
  }
  if (e.source !== $("besideFrame").contentWindow) return;
  if (m.type === "at") { places.beside = m.hash; keepPlaces(); }
  if (m.type === "screen") { besideScreen = m.screen; reportScreen(); }
  if (m.type === "picked") { clearPick(); reportScreen(); }
  if (m.type === "open") openBeside(m.hash);
  if (m.type === "close") closeBeside();
});

// Dragging a row: drop it on a pane to show it there, or (with one pane) on the right edge.
let dragTimer;
const isPlaceDrag = (e) => e.dataTransfer && [...e.dataTransfer.types].includes(DRAG_TYPE);
document.addEventListener("dragover", (e) => {
  if (!isPlaceDrag(e)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
  document.body.classList.add("dragging");
  $("besideDrop").classList.toggle("over", !!e.target.closest("#besideDrop"));
  clearTimeout(dragTimer);
  dragTimer = setTimeout(() => document.body.classList.remove("dragging"), 250);
});
document.addEventListener("drop", (e) => {
  if (!isPlaceDrag(e)) return;
  e.preventDefault();
  document.body.classList.remove("dragging");
  const hash = e.dataTransfer.getData(DRAG_TYPE);
  if (!hash) return;
  if (e.target.closest("#besideDrop")) openBeside(hash);
  else if (e.target.closest("#sidePane")) goHash(hash);
});
if (!BESIDE && places.beside && isDesktop()) openBeside(places.beside);
