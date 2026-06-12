/* Auto_BI web UI: text-first диалог поверх /api/v1.
   Контракт: start/reply -> TurnResponse; approve -> 202 + SSE /events;
   неудачная правка = 200 c error (сессия живёт) — показываем в чате. */

"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  phase: null,
  building: false,
  built: false,
};

/* ---------- chat rendering ---------- */

function addMessage(kind, text, meta) {
  const wrap = document.createElement("div");
  wrap.className = `msg msg-${kind}`;
  if (meta) {
    const m = document.createElement("div");
    m.className = "msg-meta";
    m.textContent = meta;
    wrap.appendChild(m);
  }
  const body = document.createElement("div");
  body.textContent = text;
  wrap.appendChild(body);
  $("chat").appendChild(wrap);
  $("chat").scrollTop = $("chat").scrollHeight;
  return wrap;
}

function addQuestions(questions) {
  if (!questions.length) return;
  const list = document.createElement("ol");
  list.className = "questions";
  for (const q of questions) {
    const li = document.createElement("li");
    li.textContent = q;
    list.appendChild(li);
  }
  $("chat").appendChild(list);
  $("chat").scrollTop = $("chat").scrollHeight;
}

function setChip(text, mod) {
  const chip = $("session-chip");
  chip.textContent = text;
  chip.className = `chip chip-${mod}`;
}

/* ---------- spec rendering ---------- */

function groupColumns(query) {
  const seen = [];
  for (const col of [...query.dimensions, ...query.series, ...query.rows, ...query.columns]) {
    if (!seen.includes(col)) seen.push(col);
  }
  return seen;
}

function renderSpec(spec, verdicts) {
  $("spec-empty").hidden = true;
  $("spec").hidden = false;
  $("spec-title").textContent = `«${spec.title}»`;
  $("spec-count").textContent = `${spec.charts.length} чартов · ${spec.target_bi}`;

  const filters = $("spec-filters");
  if (spec.filters && spec.filters.length) {
    const cols = spec.filters.map((f) => f.column + (f.default ? ` = ${f.default}` : ""));
    filters.textContent =
      `Фильтры дашборда (${cols.join(", ")}) пока не переносятся в Superset — ` +
      "задайте период фильтром чарта или соберите без них.";
    filters.hidden = false;
  } else {
    filters.hidden = true;
  }

  const charts = $("charts");
  charts.replaceChildren();
  for (const chart of spec.charts) {
    const card = document.createElement("div");
    card.className = "chart-card";

    const row = document.createElement("div");
    row.className = "row1";
    const viz = document.createElement("span");
    viz.className = "chart-viz";
    viz.textContent = chart.viz;
    const title = document.createElement("span");
    title.className = "chart-title";
    title.textContent = chart.title;
    row.append(viz, title);

    const fields = document.createElement("div");
    fields.className = "chart-fields";
    const dims = groupColumns(chart.query).join(", ") || "—";
    const measures = chart.query.measures.map((m) => m.label || m.column).join(", ");
    fields.textContent = `${chart.query.table}: ${dims} × ${measures}`;

    card.append(row, fields);
    charts.appendChild(card);
  }

  const verdictsBox = $("verdicts");
  verdictsBox.replaceChildren();
  for (const v of verdicts || []) {
    const card = document.createElement("div");
    card.className = `verdict verdict-${v.severity}`;

    const head = document.createElement("div");
    head.className = "verdict-head";
    head.innerHTML = "";
    const sev = document.createElement("span");
    sev.className = `sev-${v.severity}`;
    sev.textContent = `${v.severity} · ${v.chart_id}`;
    const vclass = document.createElement("span");
    vclass.className = "vclass";
    vclass.textContent = v.verdict_class;
    head.append(sev, vclass);

    const text = document.createElement("div");
    text.className = "verdict-text";
    text.textContent = v.text;
    card.append(head, text);

    if (v.suggestions && v.suggestions.length) {
      const ul = document.createElement("ul");
      ul.className = "verdict-suggestions";
      for (const s of v.suggestions) {
        const li = document.createElement("li");
        li.textContent = s;
        ul.appendChild(li);
      }
      card.appendChild(ul);
    }
    verdictsBox.appendChild(card);
  }

  $("approve-btn").disabled = false;
  $("approve-btn").textContent = state.built ? "Пересобрать дашборд" : "Собрать дашборд";
}

/* ---------- turn handling ---------- */

function handleTurn(turn) {
  state.sessionId = turn.session_id;
  state.phase = turn.phase;

  if (turn.error) {
    addMessage("error", `Правка не применена: ${turn.error}\nТекущий дашборд без изменений.`, "ошибка");
    return;
  }
  if (turn.phase === "clarify") {
    addMessage("agent", turn.message || "Нужны уточнения:", "агент");
    addQuestions(turn.questions || []);
    setChip("уточнение", "active");
    return;
  }
  if (turn.phase === "approve") {
    addMessage("agent", "Предлагаю вариант — превью справа. «Собрать дашборд» или правка словами.", "агент");
    renderSpec(turn.spec, turn.verdicts);
    setChip("превью", "active");
  }
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function send(text) {
  $("send-btn").disabled = true;
  const thinking = addMessage("agent", "…", "агент думает");
  try {
    const turn = state.sessionId
      ? await api(`/api/v1/sessions/${state.sessionId}/reply`, {
          method: "POST",
          body: JSON.stringify({ text }),
        })
      : await api("/api/v1/sessions", {
          method: "POST",
          body: JSON.stringify({ request: text }),
        });
    thinking.remove();
    handleTurn(turn);
  } catch (err) {
    thinking.remove();
    addMessage("error", String(err.message || err), "ошибка");
  } finally {
    $("send-btn").disabled = false;
  }
}

/* ---------- build ---------- */

function approve() {
  if (!state.sessionId || state.building) return;
  state.building = true;
  $("approve-btn").disabled = true;
  $("build").hidden = false;
  $("build-log").replaceChildren();
  $("build-result").hidden = true;
  setChip("сборка…", "active");

  api(`/api/v1/sessions/${state.sessionId}/approve`, { method: "POST" })
    .then(() => {
      const events = new EventSource(`/api/v1/sessions/${state.sessionId}/events`);
      const logLine = (text) => {
        const li = document.createElement("li");
        li.textContent = text;
        $("build-log").appendChild(li);
      };
      events.addEventListener("log", (e) => logLine(JSON.parse(e.data).text));
      events.addEventListener("done", (e) => {
        events.close();
        state.building = false;
        state.built = true;
        const data = JSON.parse(e.data);
        const result = $("build-result");
        result.className = "build-result";
        result.replaceChildren("Дашборд готов: ");
        const link = document.createElement("a");
        link.href = data.url;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = data.url;
        result.appendChild(link);
        result.hidden = false;
        setChip("построен", "built");
        addMessage("agent", "Готово. Дальше можно дорабатывать правками словами — пересоберу.", "агент");
        $("approve-btn").textContent = "Пересобрать дашборд";
        refreshDcr();
      });
      events.addEventListener("error", (e) => {
        if (!e.data) return; // transport-level noise, EventSource ретраится сам
        events.close();
        state.building = false;
        const result = $("build-result");
        result.className = "build-result failed";
        result.textContent = `Сборка не удалась: ${JSON.parse(e.data).text}`;
        result.hidden = false;
        setChip("ошибка сборки", "failed");
        $("approve-btn").disabled = false;
      });
    })
    .catch((err) => {
      state.building = false;
      $("approve-btn").disabled = false;
      addMessage("error", String(err.message || err), "ошибка");
    });
}

/* ---------- dm change requests ---------- */

async function refreshDcr() {
  let rows;
  try {
    rows = await api("/api/v1/dm-change-requests?status=open");
  } catch {
    return; // store не сконфигурирован — секцию не показываем
  }
  $("dcr").hidden = rows.length === 0;
  $("dcr-count").textContent = rows.length;
  const list = $("dcr-list");
  list.replaceChildren();
  for (const row of rows) {
    const li = document.createElement("li");
    li.className = "dcr-item";
    const sev = document.createElement("span");
    sev.className = `dcr-sev sev-${row.severity}`;
    sev.textContent = row.severity;
    const table = document.createElement("span");
    table.className = "dcr-table";
    table.textContent = ` ${row.table_name} `;
    const rule = document.createElement("span");
    rule.className = "dcr-rule";
    rule.textContent = `— ${row.rule}`;
    li.append(sev, table, rule);
    list.appendChild(li);
  }
}

/* ---------- wiring ---------- */

$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("chat-text").value.trim();
  if (!text) return;
  addMessage("user", text);
  $("chat-text").value = "";
  send(text);
});

$("chat-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
});

$("approve-btn").addEventListener("click", approve);

refreshDcr();
