// Bear-style markdown editor: the file stays markdown, but it reads like the finished page.
// Marks (#, **, *, >, -, link addresses) are hidden except on the line you're working on.
import { EditorState, EditorSelection, StateField, StateEffect } from "@codemirror/state";
import { EditorView, Decoration, ViewPlugin, WidgetType, keymap, drawSelection } from "@codemirror/view";
import { history, historyKeymap, defaultKeymap, undo, redo, indentWithTab } from "@codemirror/commands";
import { syntaxTree, forceParsing, codeFolding, foldable, foldedRanges, foldEffect, unfoldEffect } from "@codemirror/language";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";

class Bullet extends WidgetType {
  toDOM() { const s = document.createElement("span"); s.className = "md-bullet"; s.textContent = "•"; return s; }
  eq() { return true; }
}
class FootRef extends WidgetType {
  constructor(n) { super(); this.n = n; }
  toDOM() { const s = document.createElement("sup"); s.className = "md-footref"; s.textContent = this.n; return s; }
  eq(o) { return o.n === this.n; }
}

// A fold arrow in the margin of every heading: tap to tuck the section away, tap again to open it.
class FoldArrow extends WidgetType {
  constructor(folded) { super(); this.folded = folded; }
  eq(o) { return o.folded === this.folded; }
  toDOM(view) {
    const b = document.createElement("span");
    b.className = "md-fold" + (this.folded ? " folded" : "");
    b.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
    const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
    b.addEventListener("mousedown", stop);
    b.addEventListener("pointerdown", stop);
    b.addEventListener("click", (e) => { stop(e); toggleFold(view, view.posAtDOM(b)); });
    return b;
  }
  ignoreEvent() { return true; }
}
function foldAt(state, line) {
  let found = null;
  foldedRanges(state).between(line.to, line.to + 1, (from, to) => { if (from === line.to) found = { from, to }; });
  return found;
}
function toggleFold(view, pos) {
  const line = view.state.doc.lineAt(pos);
  const open = foldAt(view.state, line);
  if (open) { view.dispatch({ effects: unfoldEffect.of(open) }); return; }
  const range = foldable(view.state, line.from, line.to);
  if (range) view.dispatch({ effects: foldEffect.of(range) });
}

// Pictures and videos show as themselves; the markdown for one shows only while the cursor is on it.
class Media extends WidgetType {
  constructor(url, video) { super(); this.url = url; this.video = video; }
  eq(o) { return o.url === this.url && o.video === this.video; }
  toDOM() {
    const el = document.createElement(this.video ? "video" : "img");
    el.src = this.url; el.className = "md-media";
    if (this.video) { el.controls = true; el.playsInline = true; el.preload = "metadata"; }
    return el;
  }
}

const hide = Decoration.replace({});
const LINE = { ATXHeading1: "md-h1", ATXHeading2: "md-h2", ATXHeading3: "md-h3", ATXHeading4: "md-h3" };
const MARK = { Emphasis: "md-em", StrongEmphasis: "md-strong", Link: "md-link", InlineCode: "md-code", Strikethrough: "md-strike" };

function activeLines(state) {
  const lines = new Set();
  for (const r of state.selection.ranges) {
    const a = state.doc.lineAt(r.from).number, b = state.doc.lineAt(r.to).number;
    for (let n = a; n <= b; n++) lines.add(n);
  }
  return lines;
}

function build(view) {
  const { state } = view;
  const focused = view.hasFocus;
  const active = focused ? activeLines(state) : new Set();
  const lineOf = (pos) => state.doc.lineAt(pos).number;
  const ranges = [];
  const touches = (from, to) => focused && state.selection.ranges.some((r) => r.from <= to && r.to >= from);
  for (const { from, to } of view.visibleRanges) {
    syntaxTree(state).iterate({
      from, to,
      enter(node) {
        const name = node.name;
        if (LINE[name]) {
          const line = state.doc.lineAt(node.from);
          ranges.push(Decoration.line({ class: LINE[name] }).range(line.from));
          if (foldable(state, line.from, line.to) || foldAt(state, line))
            ranges.push(Decoration.widget({ widget: new FoldArrow(!!foldAt(state, line)), side: -1 }).range(line.from));
        }
        if (name === "Blockquote") {
          for (let p = node.from; p <= node.to;) { const l = state.doc.lineAt(p); ranges.push(Decoration.line({ class: "md-quote" }).range(l.from)); p = l.to + 1; }
        }
        if (MARK[name]) ranges.push(Decoration.mark({ class: MARK[name] }).range(node.from, node.to));
        const onLine = active.has(lineOf(node.from));
        if (name === "HeaderMark" && !onLine) {
          const end = Math.min(node.to + 1, state.doc.lineAt(node.from).to);
          ranges.push(hide.range(node.from, end));
        }
        if (name === "QuoteMark" && !onLine) {
          const end = state.doc.sliceString(node.to, node.to + 1) === " " ? node.to + 1 : node.to;
          ranges.push(hide.range(node.from, end));
        }
        if (name === "ListMark" && !onLine) {
          const t = state.doc.sliceString(node.from, node.to);
          if (t === "-" || t === "*" || t === "+") ranges.push(Decoration.replace({ widget: new Bullet() }).range(node.from, node.to));
        }
        if ((name === "EmphasisMark" || name === "CodeMark" || name === "StrikethroughMark") && node.to > node.from) {
          const parent = node.node.parent;
          if (!parent || !touches(parent.from, parent.to)) ranges.push(hide.range(node.from, node.to));
        }
        if (name === "Image") {
          const raw = state.doc.sliceString(node.from, node.to);
          const m = /^!\[([^\]]*)\]\(([^)\s]+)/.exec(raw);
          if (m) {
            const video = /^video$/i.test(m[1]) || /\.(mp4|mov|webm|m4v)(\?|$)/i.test(m[2]);
            const w = new Media(m[2], video);
            if (touches(node.from, node.to)) ranges.push(Decoration.widget({ widget: w, side: 1 }).range(node.to));
            else ranges.push(Decoration.replace({ widget: w }).range(node.from, node.to));
          }
        }
        if (name === "Link") {
          if (!touches(node.from, node.to)) {
            const c = node.node;
            for (let ch = c.firstChild; ch; ch = ch.nextSibling) {
              if (ch.name === "LinkMark" || ch.name === "URL" || ch.name === "LinkTitle") ranges.push(hide.range(ch.from, ch.to));
            }
            // hide "(...)" leftovers between the marks
          }
        }
      },
    });
    // The empty line between paragraphs takes the reading view's gap, not a whole line
    // (except the line you're on, so the cursor there is full size).
    for (let pos = from; pos <= to;) {
      const line = state.doc.lineAt(pos);
      if (!line.text.trim() && !active.has(line.number)) {
        const prev = line.number > 1 ? state.doc.line(line.number - 1).text : "";
        const next = line.number < state.doc.lines ? state.doc.line(line.number + 1).text : "";
        const before = /^(#{1,3}) /.exec(next);
        const cls = before ? `md-gap-h${before[1].length}` : /^#{1,6} /.test(prev) ? "md-gap-head" : "md-gap";
        ranges.push(Decoration.line({ class: cls }).range(line.from));
      }
      pos = line.to + 1;
    }
    // Footnote markers [^3] read as small superscripts; definitions read as notes.
    const text = state.doc.sliceString(from, to);
    for (const m of text.matchAll(/\[\^([^\]\s]+)\](:?)/g)) {
      const a = from + m.index, b = a + m[0].length;
      if (m[2]) { ranges.push(Decoration.line({ class: "md-footdef" }).range(state.doc.lineAt(a).from)); continue; }
      if (!touches(a, b)) ranges.push(Decoration.replace({ widget: new FootRef(m[1]) }).range(a, b));
    }
  }
  return Decoration.set(ranges, true);
}

const livePreview = ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = build(view); }
  update(u) {
    const folds = u.transactions.some((t) => t.effects.some((e) => e.is(foldEffect) || e.is(unfoldEffect)));
    if (u.docChanged || u.selectionSet || u.viewportChanged || u.focusChanged || folds || syntaxTree(u.startState) !== syntaxTree(u.state)) this.decorations = build(u.view);
  }
}, { decorations: (v) => v.decorations });

// --- formatting commands (toggle, like Bear) ---
function lineCommand(view, prefix) {
  const { state } = view;
  const changes = [];
  const seen = new Set();
  for (const r of state.selection.ranges) {
    for (let p = r.from; p <= r.to;) {
      const line = state.doc.lineAt(p);
      if (!seen.has(line.number)) {
        seen.add(line.number);
        const m = /^(#{1,6} |> |[-*+] |\d+\. )/.exec(line.text);
        const cur = m ? m[0] : "";
        const kind = (s) => s.replace(/#+ /, "# ").replace(/\d+\. /, "1. ").replace(/[*+] /, "- ");
        if (cur === prefix) changes.push({ from: line.from, to: line.from + cur.length, insert: "" });
        else if (cur && kind(cur) === kind(prefix) || (cur.startsWith("#") && prefix.startsWith("#")))
          changes.push({ from: line.from, to: line.from + cur.length, insert: prefix });
        else if (cur) changes.push({ from: line.from, to: line.from + cur.length, insert: prefix });
        else changes.push({ from: line.from, insert: prefix });
      }
      p = line.to + 1;
    }
  }
  view.dispatch({ changes, scrollIntoView: false, userEvent: "input.format" });
  view.focus();
}

function wrapCommand(view, mark, placeholder) {
  const { state } = view;
  const tr = state.changeByRange((r) => {
    const n = mark.length;
    const before = state.doc.sliceString(r.from - n, r.from), after = state.doc.sliceString(r.to, r.to + n);
    if (before === mark && after === mark) {
      return { changes: [{ from: r.from - n, to: r.from }, { from: r.to, to: r.to + n }], range: EditorSelection.range(r.from - n, r.to - n) };
    }
    const sel = state.doc.sliceString(r.from, r.to);
    if (sel.startsWith(mark) && sel.endsWith(mark) && sel.length >= 2 * n) {
      return { changes: { from: r.from, to: r.to, insert: sel.slice(n, -n) }, range: EditorSelection.range(r.from, r.to - 2 * n) };
    }
    const inner = sel || placeholder;
    return { changes: { from: r.from, to: r.to, insert: mark + inner + mark }, range: EditorSelection.range(r.from + n, r.from + n + inner.length) };
  });
  view.dispatch(state.update(tr, { userEvent: "input.format" }));
  view.focus();
}

function linkCommand(view, url) {
  const { state } = view;
  const r = state.selection.main;
  const sel = state.doc.sliceString(r.from, r.to) || "link";
  const insert = `[${sel}](${url || "https://"})`;
  view.dispatch({ changes: { from: r.from, to: r.to, insert }, selection: { anchor: r.from + 1, head: r.from + 1 + sel.length }, userEvent: "input.format" });
  view.focus();
}

function footnoteCommand(view) {
  const { state } = view;
  const text = state.doc.toString();
  const nums = [...text.matchAll(/\[\^(\d+)\]/g)].map((m) => +m[1]);
  const n = (nums.length ? Math.max(...nums) : 0) + 1;
  const pos = state.selection.main.head;
  const tail = text.replace(/\s*$/, "").length;
  const def = `\n\n[^${n}]: `;
  view.dispatch({
    changes: [{ from: pos, insert: `[^${n}]` }, { from: tail, to: text.length, insert: def }],
    selection: { anchor: tail + `[^${n}]`.length + def.length },
    scrollIntoView: true, userEvent: "input.format",
  });
  view.focus();
}

const theme = EditorView.theme({
  "&": { fontFamily: "var(--serif, Georgia, serif)", fontSize: "17px", color: "var(--ink, #2a2420)", backgroundColor: "transparent" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "inherit", lineHeight: "1.45", overflow: "visible" },
  ".cm-content": { padding: "0 0 40vh", caretColor: "var(--accent, #B4532A)" },
  ".cm-line": { padding: "0" },
  ".cm-cursor": { borderLeftColor: "var(--accent, #B4532A)", borderLeftWidth: "2px" },
  ".md-fold": { position: "absolute", transform: "translateX(calc(-100% - 2px))", marginTop: ".35em", color: "#c4b6a6", padding: "1px", cursor: "pointer", lineHeight: "0", userSelect: "none", WebkitUserSelect: "none" },
  ".md-fold svg": { transition: "transform .15s" },
  ".md-fold.folded": { color: "var(--accent, #D26645)" },
  ".md-fold.folded svg": { transform: "rotate(-90deg)" },
  ".md-h1 .md-fold": { marginTop: "9px" },
  ".md-h2 .md-fold": { marginTop: "9px" },
  ".md-folded": { fontFamily: "var(--sans, system-ui)", fontSize: "13px", color: "var(--accent, #D26645)", background: "#F9D9C9", borderRadius: "8px", padding: "0 8px", marginLeft: "6px", cursor: "pointer" },
  ".md-media": { display: "block", maxWidth: "100%", maxHeight: "70vh", margin: "6px auto", borderRadius: "4px" },
  ".md-h1": { fontSize: "26px", fontWeight: "500", lineHeight: "1.2", paddingTop: "0" },
  ".md-h2": { fontSize: "20px", fontWeight: "500", lineHeight: "1.45" },
  ".md-h3": { fontSize: "17px", fontWeight: "600" },
  ".md-gap": { lineHeight: ".7em" },
  ".md-gap-head": { lineHeight: "7px" },
  // the space above a heading, as in the reading view
  ".md-gap-h1": { lineHeight: "14px" },
  ".md-gap-h2": { lineHeight: "24px" },
  ".md-gap-h3": { lineHeight: "19px" },
  ".md-strong": { fontWeight: "600" },
  ".md-em": { fontStyle: "italic" },
  ".md-strike": { textDecoration: "line-through" },
  ".md-link": { color: "var(--accent, #B4532A)", textDecoration: "underline", textDecorationColor: "#B4532A55" },
  ".md-code": { fontFamily: "ui-monospace, Menlo, monospace", fontSize: ".88em", background: "#0000000a", borderRadius: "3px" },
  ".md-quote": { borderLeft: "3px solid #D8C8B0", paddingLeft: "14px !important", color: "#5a4f46" },
  ".md-bullet": { color: "var(--accent, #B4532A)", display: "inline-block", width: ".9em" },
  ".md-footref": { color: "var(--accent, #B4532A)", fontFamily: "var(--sans, system-ui)", fontSize: "11px", padding: "0 1px" },
  ".md-footdef": { fontFamily: "var(--sans, system-ui)", fontSize: "13px", color: "#7a6e64" },
  // markup that's showing because you're on its line: faint, like Bear
  ".cm-formatting, .tok-meta, .tok-processingInstruction": { color: "#b9ab9c" },
});

// Suggestions waiting on the text show in the editor exactly as in reading: the app draws each
// one (crossed-out words, new words, its tick and cross) and the editor puts that in place of the
// words it covers. They follow those words as the user types around them.
class Suggestion extends WidgetType {
  constructor(key, draw) { super(); this.key = key; this.draw = draw; }
  eq(o) { return o.key === this.key; }
  toDOM() { return this.draw(this.key); }
  ignoreEvent() { return true; }
}
const setMarks = StateEffect.define();
// Suggestions in the text, drawn by the app: an inline one replaces its words with how the
// reading view shows them (`{from, to, key}`); a bigger one (`block`) leaves the text editable,
// crosses it out ("replace") or not ("after", "before"), and shows the new paragraphs as their own block.
function marksField(draw) {
  const build = (list, doc) => {
    const atomic = [], other = [];
    for (const m of list) {
      if (m.to > doc.length || m.from < 0) continue;
      const w = new Suggestion(m.key, draw);
      if (!m.block) { if (m.to > m.from) atomic.push(Decoration.replace({ widget: w }).range(m.from, m.to)); continue; }
      if (m.block === "replace" && m.to > m.from) other.push(Decoration.mark({ class: "md-sug-old" }).range(m.from, m.to));
      if (m.block === "before") other.push(Decoration.widget({ widget: w, block: true, side: -1 }).range(doc.lineAt(m.from).from));
      else other.push(Decoration.widget({ widget: w, block: true, side: 1 }).range(doc.lineAt(m.to).to));
    }
    return { atomic: Decoration.set(atomic, true), other: Decoration.set(other, true) };
  };
  return StateField.define({
    create: () => ({ atomic: Decoration.none, other: Decoration.none }),
    update(marks, tr) {
      marks = { atomic: marks.atomic.map(tr.changes), other: marks.other.map(tr.changes) };
      for (const e of tr.effects) if (e.is(setMarks)) marks = build(e.value, tr.state.doc);
      return marks;
    },
    provide: (f) => [
      EditorView.decorations.from(f, (m) => m.atomic),
      EditorView.decorations.from(f, (m) => m.other),
      EditorView.atomicRanges.of((v) => v.state.field(f).atomic),
    ],
  });
}

export function createEditor(parent, doc, { onChange, drawMark } = {}) {
  let quiet = false;
  const extensions = [
    marksField(drawMark || (() => document.createElement("span"))),
    history(),
    drawSelection(),
    EditorView.lineWrapping,
    markdown({ base: markdownLanguage }),
    codeFolding({
      placeholderDOM(view, onclick) {
        const s = document.createElement("span");
        s.className = "md-folded"; s.textContent = "…"; s.title = "Show this section";
        s.onclick = onclick;
        return s;
      },
    }),
    livePreview,
    theme,
    keymap.of([
      { key: "Mod-b", run: (v) => (wrapCommand(v, "**", "bold"), true) },
      { key: "Mod-i", run: (v) => (wrapCommand(v, "*", "italic"), true) },
      { key: "Mod-1", run: (v) => (lineCommand(v, "# "), true) },
      { key: "Mod-2", run: (v) => (lineCommand(v, "## "), true) },
      ...historyKeymap, ...defaultKeymap, indentWithTab,
    ]),
    EditorView.contentAttributes.of({ spellcheck: "true", autocorrect: "on", autocapitalize: "sentences" }),
    EditorView.updateListener.of((u) => { if (u.docChanged && onChange && !quiet) onChange(u.state.doc.toString()); }),
  ];
  const view = new EditorView({ parent, state: EditorState.create({ doc, extensions }) });
  return {
    view,
    get value() { return view.state.doc.toString(); },
    // A fresh document: new text and a clean undo history.
    // The whole document is read at once, so headings far down are already their size when it shows
    // (read bit by bit, they'd grow a frame later and move the text).
    reset(text) { quiet = true; view.setState(EditorState.create({ doc: text, extensions })); forceParsing(view, text.length, 500); quiet = false; },
    // New text from outside (a suggestion the user accepted): only the part that changed is replaced,
    // so the cursor, the scroll and the rest of the page stay where they are.
    replaceAll(text) {
      const old = view.state.doc.toString();
      if (old === text) return;
      let a = 0; while (a < old.length && a < text.length && old[a] === text[a]) a++;
      let b = 0; while (b < old.length - a && b < text.length - a && old[old.length - 1 - b] === text[text.length - 1 - b]) b++;
      quiet = true;
      view.dispatch({ changes: { from: a, to: old.length - b, insert: text.slice(a, text.length - b) }, scrollIntoView: false });
      quiet = false;
    },
    format(kind, arg) {
      if (kind === "h1") lineCommand(view, "# ");
      else if (kind === "h2") lineCommand(view, "## ");
      else if (kind === "quote") lineCommand(view, "> ");
      else if (kind === "list") lineCommand(view, "- ");
      else if (kind === "b") wrapCommand(view, "**", "bold");
      else if (kind === "i") wrapCommand(view, "*", "italic");
      else if (kind === "link") linkCommand(view, arg);
      else if (kind === "footnote") footnoteCommand(view);
      else if (kind === "undo") undo(view);
      else if (kind === "redo") redo(view);
    },
    // A block of its own (an image, a caption) after the paragraph the cursor is in.
    insertBlock(text, selectFrom, selectTo) {
      const st = view.state, pos = st.selection.main.head, line = st.doc.lineAt(pos);
      const at = line.text.trim() ? line.to : line.from;
      const before = at > 0 && line.text.trim() ? "\n\n" : (at > 0 && st.doc.sliceString(at - 2, at) !== "\n\n" ? "\n" : "");
      const insert = before + text + "\n\n";
      const start = at + before.length;
      view.dispatch({
        changes: { from: at, insert },
        selection: selectFrom == null ? { anchor: start + text.length } : { anchor: start + selectFrom, head: start + selectTo },
        userEvent: "input",
      });
      view.focus();
    },
    // Put the cursor at `pos` without letting the browser scroll anywhere.
    placeCursor(pos) {
      view.dispatch({ selection: { anchor: Math.min(pos, view.state.doc.length) } });
      view.contentDOM.focus({ preventScroll: true });
    },
    // How far down the editor a position sits, and which position sits at a given height.
    // Show these stretches as suggestions: [{from, to, key}], each drawn by the app.
    setMarks(marks) { view.dispatch({ effects: setMarks.of(marks) }); },
    // The position under a point on the screen, and moving the cursor there (no scrolling).
    posAt(x, y) { return view.posAtCoords({ x, y }); },
    select(pos) { view.dispatch({ selection: { anchor: Math.min(pos, view.state.doc.length) } }); },
    // The paragraph the cursor is in, and the selected words (if any).
    cursorText() {
      const st = view.state, r = st.selection.main, line = st.doc.lineAt(r.head);
      return { line: line.text, picked: st.doc.sliceString(r.from, r.to) };
    },
    hasFocus() { return view.hasFocus; },
    lineStartAtCursor() { return view.state.doc.lineAt(view.state.selection.main.head).from; },
    // Scroll so `pos` sits `margin` pixels below the top of whatever scrolls the editor.
    scrollTo(pos, margin) {
      view.dispatch({ effects: EditorView.scrollIntoView(Math.min(pos, view.state.doc.length), { y: "start", yMargin: Math.max(0, margin) }) });
    },
    topOf(pos) { return view.lineBlockAt(Math.min(pos, view.state.doc.length)).top; },
    // The first line with text that starts at or below height `y`.
    firstLineAt(y) {
      const len = view.state.doc.length;
      let b = view.lineBlockAtHeight(Math.max(0, y));
      while ((b.top < y - 1 || !view.state.doc.sliceString(b.from, b.to).trim()) && b.to < len) b = view.lineBlockAt(b.to + 1);
      return b.from;
    },
    posAtHeight(y) { return view.lineBlockAtHeight(Math.max(0, y)).from; },
    focus() { view.focus(); },
  };
}
