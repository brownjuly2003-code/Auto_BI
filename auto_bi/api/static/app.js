/* Auto_BI web UI: text-first и fields-first входы поверх /api/v1 (один пайплайн).
   Контракт: start/reply -> TurnResponse; approve -> 202 + SSE /events;
   неудачная правка = 200 c error (сессия живёт) — показываем в чате.
   Fields-first: панель полей из GET /model/fields, drag&drop в черновые группы,
   POST /sessions {seed}; после старта сессии оба режима продолжаются чатом. */

"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  phase: null,
  building: false,
  built: false,
  mode: "text", // text | fields; фиксируется при старте сессии
  groups: [], // fields-first черновик: [{label, fields: ["dm.t.col"]}]
  activeGroup: 0, // куда падает клик по полю (DnD-фоллбек)
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

function renderSpec(spec, verdicts, notes) {
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

  const notesBox = $("spec-notes");
  notesBox.replaceChildren();
  notesBox.hidden = !(notes && notes.length);
  for (const note of notes || []) {
    const line = document.createElement("div");
    line.className = "spec-note";
    line.textContent = `Анализ раскладки: ${note}`;
    notesBox.appendChild(line);
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
  $("mode-tabs").hidden = true; // вход (текст/поля) зафиксирован стартом сессии

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
    renderSpec(turn.spec, turn.verdicts, turn.notes);
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

/* ---------- fields-first builder ---------- */

const ROLE_SHORT = { time: "T", dimension: "D", measure: "M" };

function setMode(mode) {
  if (state.sessionId) return; // вход фиксируется первой сессией
  state.mode = mode;
  for (const tab of document.querySelectorAll(".mode-tab")) {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  $("builder").hidden = mode !== "fields";
  $("chat-form").hidden = mode === "fields";
  if (mode === "fields" && !$("field-tables").childElementCount) loadFieldPanel();
  if (mode === "fields" && !state.groups.length) {
    state.groups = [{ label: "", fields: [] }];
    renderGroups();
  }
}

async function loadFieldPanel() {
  let tables;
  try {
    tables = await api("/api/v1/model/fields");
  } catch (err) {
    addMessage("error", `Не удалось загрузить поля витрин: ${err.message || err}`, "ошибка");
    return;
  }
  const box = $("field-tables");
  box.replaceChildren();
  for (const table of tables) {
    const block = document.createElement("div");
    block.className = "field-table";
    const head = document.createElement("div");
    head.className = "field-table-name";
    head.textContent = table.table;
    head.title = table.description || "";
    block.appendChild(head);
    for (const col of table.columns) {
      const ref = `${table.table}.${col.name}`;
      const item = document.createElement("button");
      item.type = "button";
      item.className = `field-item role-${col.role}`;
      item.draggable = true;
      item.dataset.ref = ref;
      item.title = `${col.description || col.name} (${col.type})`;

      const role = document.createElement("span");
      role.className = "field-role";
      role.textContent = ROLE_SHORT[col.role] || "?";
      const name = document.createElement("span");
      name.textContent = col.name;
      item.append(role, name);

      item.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", ref);
        e.dataTransfer.effectAllowed = "copy";
      });
      // клик = добавить в активную группу: DnD-фоллбек (тач, клавиатура)
      item.addEventListener("click", () => addFieldToGroup(state.activeGroup, ref));
      block.appendChild(item);
    }
    box.appendChild(block);
  }
}

function addFieldToGroup(index, ref) {
  const group = state.groups[index];
  if (!group || group.fields.includes(ref)) return;
  group.fields.push(ref);
  renderGroups();
}

function renderGroups() {
  const box = $("groups");
  box.replaceChildren();
  state.groups.forEach((group, index) => {
    const card = document.createElement("div");
    card.className = "group-card" + (index === state.activeGroup ? " active" : "");
    card.dataset.index = String(index);

    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      card.classList.add("drop-target");
    });
    card.addEventListener("dragleave", () => card.classList.remove("drop-target"));
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      card.classList.remove("drop-target");
      state.activeGroup = index;
      addFieldToGroup(index, e.dataTransfer.getData("text/plain"));
    });
    card.addEventListener("click", () => {
      if (state.activeGroup !== index) {
        state.activeGroup = index;
        renderGroups();
      }
    });

    const head = document.createElement("div");
    head.className = "group-head";
    const label = document.createElement("input");
    label.type = "text";
    label.className = "group-label";
    label.placeholder = `Группа ${index + 1} — название чарта (необязательно)`;
    label.value = group.label;
    label.addEventListener("input", () => {
      group.label = label.value;
    });
    label.addEventListener("focus", () => {
      state.activeGroup = index;
    });
    head.appendChild(label);
    if (state.groups.length > 1) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "group-remove";
      remove.title = "Удалить группу";
      remove.textContent = "×";
      remove.addEventListener("click", (e) => {
        e.stopPropagation();
        state.groups.splice(index, 1);
        state.activeGroup = Math.min(state.activeGroup, state.groups.length - 1);
        renderGroups();
      });
      head.appendChild(remove);
    }

    const chips = document.createElement("div");
    chips.className = "group-fields";
    if (!group.fields.length) {
      const empty = document.createElement("span");
      empty.className = "group-empty";
      empty.textContent = "перетащите поля сюда";
      chips.appendChild(empty);
    }
    for (const ref of group.fields) {
      const chip = document.createElement("span");
      chip.className = "field-chip";
      chip.textContent = ref;
      const x = document.createElement("button");
      x.type = "button";
      x.className = "chip-remove";
      x.title = "Убрать поле";
      x.textContent = "×";
      x.addEventListener("click", () => {
        group.fields = group.fields.filter((f) => f !== ref);
        renderGroups();
      });
      chip.appendChild(x);
      chips.appendChild(chip);
    }

    card.append(head, chips);
    box.appendChild(card);
  });
}

async function submitSeed() {
  const groups = state.groups
    .filter((g) => g.fields.length)
    .map((g) => ({ label: g.label.trim(), fields: g.fields }));
  if (!groups.length) {
    addMessage("error", "Перетащите хотя бы одно поле в группу.", "ошибка");
    return;
  }
  const comment = $("seed-comment").value.trim();
  const summary = groups
    .map((g, i) => `Группа ${i + 1}${g.label ? ` «${g.label}»` : ""}: ${g.fields.join(", ")}`)
    .join("\n");
  addMessage("user", summary + (comment ? `\n${comment}` : ""));

  $("seed-submit").disabled = true;
  const thinking = addMessage("agent", "…", "агент думает");
  try {
    const turn = await api("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ seed: { groups, comment } }),
    });
    thinking.remove();
    // сессия началась: дальше — обычный чат (уточнения, правки словами)
    $("builder").hidden = true;
    $("chat-form").hidden = false;
    $("mode-tabs").hidden = true;
    handleTurn(turn);
  } catch (err) {
    thinking.remove();
    addMessage("error", String(err.message || err), "ошибка");
  } finally {
    $("seed-submit").disabled = false;
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

for (const tab of document.querySelectorAll(".mode-tab")) {
  tab.addEventListener("click", () => setMode(tab.dataset.mode));
}

$("add-group").addEventListener("click", () => {
  state.groups.push({ label: "", fields: [] });
  state.activeGroup = state.groups.length - 1;
  renderGroups();
});

$("seed-submit").addEventListener("click", submitSeed);

refreshDcr();
