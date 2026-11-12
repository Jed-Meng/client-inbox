"use strict";
// All email content is put on the page with textContent, never innerHTML.

const $ = (id) => document.getElementById(id);
const view = { clients: [], clientId: null, threadId: null, tab: "threads", draftStamp: null, dirty: false };

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: { "X-Client-Inbox": "1" } };
  if (options.form) {
    init.body = options.form;
  } else if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const resp = await fetch(path, init);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `Request failed (${resp.status})`);
  return data;
}

function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, 3500);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function when(seconds) {
  if (!seconds) return "";
  const date = new Date(seconds * 1000);
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

// ---------- left: clients ----------

async function refreshState() {
  const state = await api("/api/state");
  view.clients = state.clients;
  $("me").textContent = state.me;

  const sync = $("sync");
  sync.className = "pill" + (state.sync.error ? " error" : "");
  if (state.sync.error) sync.textContent = `Gmail problem: ${state.sync.error}`;
  else if (state.sync.total && state.sync.done < state.sync.total) sync.textContent = `Loading mail ${state.sync.done} of ${state.sync.total}`;
  else sync.textContent = state.sync.phase;

  $("voice").textContent = !state.claude_ready ? "Claude Code not found"
    : state.voice_building ? "Learning your voice…"
    : state.voice_error ? `Voice failed: ${state.voice_error}`
    : state.voice_ready ? "Voice learned" : "Voice not learned yet";

  const nav = $("clients");
  nav.replaceChildren();
  if (!state.clients.length) nav.append(el("p", "muted pad", "No mail loaded yet."));
  for (const client of state.clients) {
    const row = el("button", "client" + (client.id === view.clientId ? " on" : ""));
    row.append(el("span", "name", client.name));
    if (client.needs_reply) row.append(el("span", "count", String(client.needs_reply)));
    row.title = `${client.thread_count} threads`;
    row.onclick = () => selectClient(client.id);
    nav.append(row);
  }
  const current = state.clients.find((c) => c.id === view.clientId);
  if (current) $("client-name").textContent = current.name;
}

// ---------- middle: threads and files ----------

async function selectClient(id) {
  view.clientId = id;
  view.threadId = null;
  showDetail(null);
  await refreshState();
  await refreshMiddle();
}

function setTab(tab) {
  view.tab = tab;
  $("tab-threads").classList.toggle("on", tab === "threads");
  $("tab-files").classList.toggle("on", tab === "files");
  refreshMiddle();
}

async function refreshMiddle() {
  const has = view.clientId !== null;
  $("client-bar").hidden = !has;
  $("middle-empty").hidden = has;
  $("threads").hidden = !has || view.tab !== "threads";
  $("files").hidden = !has || view.tab !== "files";
  if (!has) return;
  if (view.tab === "files") return refreshFiles();

  const threads = await api(`/api/clients/${view.clientId}/threads`);
  const list = $("threads");
  list.replaceChildren();
  for (const thread of threads) {
    const row = el("button", "thread" + (thread.id === view.threadId ? " on" : ""));
    const top = el("div", "top");
    if (thread.needs_reply && thread.awaiting_me) top.append(el("span", "dot"));
    top.append(el("span", "subject", thread.subject), el("span", "muted small", when(thread.last_date)));
    const working = thread.status === "summarizing" ? "Summarizing… " : thread.status === "drafting" ? "Drafting… " : "";
    row.append(top, el("div", "line", working + (thread.one_line || thread.snippet || "")));
    row.onclick = () => selectThread(thread.id);
    list.append(row);
  }
}

async function refreshFiles() {
  const files = await api(`/api/clients/${view.clientId}/files`);
  const list = $("file-list");
  list.replaceChildren();
  if (!files.length) list.append(el("li", "muted", "No files yet."));
  for (const file of files) {
    const item = el("li");
    const remove = el("button", "quiet", "Remove");
    remove.onclick = async () => { await api(`/api/files/${file.id}`, { method: "DELETE" }); refreshFiles(); };
    item.append(el("span", "spacer", file.filename), el("span", "muted small", `${file.chars.toLocaleString()} chars`), remove);
    list.append(item);
  }
}

// ---------- right: one thread ----------

async function selectThread(id) {
  view.threadId = id;
  view.draftStamp = null;
  view.dirty = false;
  view.askedSummary = false;
  $("instruction").value = "";
  await refreshThread();
  refreshMiddle();
}

function showDetail(thread) {
  $("detail-empty").hidden = thread !== null;
  $("detail-body").hidden = thread === null;
}

async function refreshThread() {
  if (!view.threadId) return;
  const id = view.threadId;
  const thread = await api(`/api/threads/${id}`);
  if (id !== view.threadId) return;
  showDetail(thread);
  $("subject").textContent = thread.subject;
  $("gmail-link").href = thread.gmail_url;

  const busy = thread.queued || thread.status === "summarizing" || thread.status === "drafting";
  const canReply = Boolean(thread.last_inbound_id);

  // Older mail is summarized the first time it is opened.
  if (!thread.summary && !busy && canReply && thread.status !== "error" && !view.askedSummary) {
    view.askedSummary = true;
    api(`/api/threads/${id}/summarize`, { method: "POST" }).then(refreshThread).catch((e) => toast(e.message));
  }
  $("summary").textContent = thread.summary
    || (thread.status === "error" ? `Could not summarize: ${thread.error}` : canReply ? "Summarizing…" : "Only your own messages so far.");
  const flag = $("reply-flag");
  flag.textContent = thread.needs_reply === null ? "" : !thread.awaiting_me ? "You replied last"
    : thread.needs_reply ? "Needs a reply" : "No reply needed";

  const draft = thread.draft;
  const box = $("draft");
  if (draft && draft.updated_at !== view.draftStamp && !(view.dirty && document.activeElement === box)) {
    box.value = draft.body;
    view.draftStamp = draft.updated_at;
    view.dirty = false;
  } else if (!draft && !view.dirty) {
    box.value = "";
  }
  $("draft-status").textContent = thread.status === "drafting" ? "writing…" : thread.queued ? "queued…" : "";
  $("regen-btn").textContent = draft ? "Regenerate" : "Draft a reply";
  $("regen-btn").disabled = busy || !canReply;
  $("copy-btn").disabled = !box.value.trim();
  $("gmail-btn").disabled = !box.value.trim() || !canReply;
  $("draft-note").textContent = thread.status === "error" ? `Last attempt failed: ${thread.error}`
    : draft && draft.gmail_draft_id ? "A copy of this draft is in Gmail. Review and send it from there."
    : draft && draft.for_message_id !== thread.last_inbound_id ? "A newer message arrived after this draft was written."
    : "";

  const sources = $("sources");
  sources.replaceChildren();
  const kinds = { attachment: "Attachment", drive: "Drive", upload: "Uploaded file", past_reply: "Your earlier reply" };
  for (const source of (draft ? draft.sources : [])) {
    const item = el("li", source.used ? "used" : "");
    item.append(document.createTextNode(`${source.used ? "Used" : "Checked"} · ${kinds[source.kind] || source.kind}: `));
    if (source.url && source.url.startsWith("https://")) {
      const link = el("a", "", source.title);
      link.href = source.url; link.target = "_blank"; link.rel = "noopener";
      item.append(link);
    } else {
      item.append(document.createTextNode(source.title));
    }
    sources.append(item);
  }

  // Rebuild the message list only when it changed, so a text selection survives the refresh.
  const stamp = `${id}:${thread.messages.length}`;
  if (stamp === view.messageStamp) return;
  view.messageStamp = stamp;
  const list = $("messages");
  list.replaceChildren();
  for (const msg of [...thread.messages].reverse()) {
    const card = el("div", "msg" + (msg.is_me ? " me" : ""));
    const files = msg.attachments.length ? ` · ${msg.attachments.join(", ")}` : "";
    card.append(
      el("div", "meta", `${msg.is_me ? "You" : `${msg.from_name} <${msg.from_email}>`} · ${new Date(msg.date * 1000).toLocaleString()}${files}`),
      el("pre", "", msg.body || "(no text)"),
    );
    list.append(card);
  }
}

async function saveDraft() {
  if (!view.threadId || !view.dirty) return;
  await api(`/api/threads/${view.threadId}/draft`, { method: "PUT", body: { body: $("draft").value } });
  view.dirty = false;
}

// ---------- dialogs ----------

function pick({ title, options, allowNew, senderOption }) {
  return new Promise((resolve) => {
    const dialog = $("picker");
    const select = $("picker-select");
    $("picker-title").textContent = title;
    select.replaceChildren();
    for (const option of options) {
      const node = el("option", "", option.name);
      node.value = String(option.id);
      select.append(node);
    }
    if (allowNew) {
      const node = el("option", "", "New client…");
      node.value = "new";
      select.append(node);
    }
    const sync = () => { $("picker-new").hidden = select.value !== "new"; };
    select.onchange = sync;
    $("picker-new").value = "";
    $("picker-sender").checked = false;
    $("picker-sender-row").hidden = !senderOption;
    sync();
    dialog.onclose = () => {
      if (dialog.returnValue !== "ok") return resolve(null);
      resolve({
        id: select.value === "new" ? null : Number(select.value),
        newName: $("picker-new").value.trim(),
        applyToSender: $("picker-sender").checked,
      });
    };
    dialog.showModal();
  });
}

// ---------- wiring ----------

function guard(fn) {
  return async (...args) => {
    try { await fn(...args); } catch (error) { toast(error.message); }
  };
}

$("tab-threads").onclick = () => setTab("threads");
$("tab-files").onclick = () => setTab("files");

$("rename-btn").onclick = guard(async () => {
  const current = view.clients.find((c) => c.id === view.clientId);
  const name = prompt("Client name", current ? current.name : "");
  if (!name || !name.trim()) return;
  await api(`/api/clients/${view.clientId}/rename`, { method: "POST", body: { name } });
  await refreshState();
});

$("merge-btn").onclick = guard(async () => {
  const others = view.clients.filter((c) => c.id !== view.clientId);
  if (!others.length) return toast("There is no other client to merge into.");
  const choice = await pick({ title: "Merge this client into", options: others });
  if (!choice || choice.id === null) return;
  await api(`/api/clients/${view.clientId}/merge`, { method: "POST", body: { into: choice.id } });
  await selectClient(choice.id);
});

$("move-btn").onclick = guard(async () => {
  const choice = await pick({
    title: "Move this thread to", options: view.clients.filter((c) => c.id !== view.clientId),
    allowNew: true, senderOption: true,
  });
  if (!choice) return;
  const moved = await api(`/api/threads/${view.threadId}/move`, {
    method: "POST",
    body: { client_id: choice.id, new_name: choice.newName, apply_to_sender: choice.applyToSender },
  });
  view.clientId = moved.client_id;
  await refreshState();
  await refreshMiddle();
});

$("file-input").onchange = guard(async (event) => {
  for (const file of event.target.files) {
    const form = new FormData();
    form.append("file", file);
    try {
      await api(`/api/clients/${view.clientId}/files`, { method: "POST", form });
    } catch (error) {
      toast(`${file.name}: ${error.message}`);
    }
  }
  event.target.value = "";
  await refreshFiles();
  await refreshState();
});

$("draft").oninput = () => {
  view.dirty = true;
  $("copy-btn").disabled = $("gmail-btn").disabled = !$("draft").value.trim();
};
$("draft").onblur = guard(saveDraft);

$("copy-btn").onclick = guard(async () => {
  await navigator.clipboard.writeText($("draft").value);
  toast("Draft copied. Paste it into your reply.");
});

$("gmail-btn").onclick = guard(async () => {
  $("gmail-btn").disabled = true;
  try {
    await api(`/api/threads/${view.threadId}/gmail-draft`, { method: "POST", body: { body: $("draft").value } });
    view.dirty = false;
    toast("Draft created in Gmail. Nothing was sent.");
  } finally {
    await refreshThread();
  }
});

$("regen-btn").onclick = guard(async () => {
  await saveDraft();
  await api(`/api/threads/${view.threadId}/draft`, { method: "POST", body: { instruction: $("instruction").value } });
  view.dirty = false;
  await refreshThread();
});

$("voice-btn").onclick = guard(async () => {
  await api("/api/voice/rebuild", { method: "POST" });
  await refreshState();
});

async function tick() {
  try {
    await refreshState();
    if (view.clientId !== null && view.tab === "threads") await refreshMiddle();
    await refreshThread();
  } catch (error) {
    $("sync").textContent = "App not reachable. Is it still running?";
    $("sync").className = "pill error";
  }
}

tick();
setInterval(tick, 5000);
