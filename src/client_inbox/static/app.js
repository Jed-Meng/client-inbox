"use strict";
// All email content is put on the page with textContent, never innerHTML.

const $ = (id) => document.getElementById(id);
const view = {
  clients: [], clientId: null, threadId: null, tab: "threads", detailTab: "email",
  draftStamp: null, dirty: false, demo: false,
};

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

  view.demo = state.demo;
  $("demo-badge").hidden = !state.demo;
  $("demo-btn").hidden = !state.demo;
  $("demo-btn").disabled = !state.demo_waiting;
  $("demo-btn").textContent = state.demo_waiting ? `Deliver a sample email (${state.demo_waiting} left)` : "No more sample emails";
  $("gmail-link").hidden = state.demo;

  const nav = $("clients");
  nav.replaceChildren();
  view.library = state.library;
  const shelf = el("button", "client library" + (view.clientId === 0 ? " on" : ""));
  shelf.append(el("span", "name", "Reference library"), el("span", "muted small", String(state.library.count)));
  shelf.title = "Documents Claude searches for every client";
  shelf.onclick = () => selectClient(0);
  nav.append(shelf);
  if (view.clientId === 0) $("client-name").textContent = "Reference library";
  if (!state.clients.length) nav.append(el("p", "muted pad", "No mail loaded yet."));
  for (const client of state.clients) {
    const row = el("button", "client" + (client.id === view.clientId ? " on" : ""));
    row.append(el("span", "name", client.name));
    if (client.high_risk) row.append(el("span", "count risk", "!"));
    if (client.needs_reply) row.append(el("span", "count", String(client.needs_reply)));
    row.title = `${client.thread_count} threads`;
    row.onclick = () => selectClient(client.id);
    nav.append(row);
  }
  const current = state.clients.find((c) => c.id === view.clientId);
  if (current) $("client-name").textContent = current.name;
  $("client-digest").textContent = current ? current.digest : "";
}

// ---------- middle: threads and files ----------

async function selectClient(id) {
  if ((view.clientId === 0 && id !== 0) || view.tab === "profile") view.tab = "threads";
  view.profileLoaded = null;
  view.clientId = id;
  view.threadId = null;
  showDetail(null);
  await refreshState();
  await refreshMiddle();
}

function setTab(tab) {
  view.tab = tab;
  view.profileLoaded = null;
  refreshMiddle();
}

async function refreshMiddle() {
  const has = view.clientId !== null;
  const isLibrary = view.clientId === 0;
  if (isLibrary) view.tab = "files";
  for (const id of ["tab-threads", "tab-profile", "rename-btn", "merge-btn"]) $(id).hidden = isLibrary;
  for (const tab of ["threads", "files", "profile"]) $(`tab-${tab}`).classList.toggle("on", view.tab === tab);
  $("profile").hidden = !has || view.tab !== "profile";
  $("client-bar").hidden = !has;
  $("middle-empty").hidden = has;
  $("threads").hidden = !has || view.tab !== "threads";
  $("files").hidden = !has || view.tab !== "files";
  if (!has) return;
  if (view.tab === "profile") return refreshProfile();
  if (view.tab === "files") return refreshFiles();

  const threads = await api(`/api/clients/${view.clientId}/threads`);
  const list = $("threads");
  list.replaceChildren();
  for (const thread of threads) {
    const row = el("button", "thread" + (thread.id === view.threadId ? " on" : ""));
    const top = el("div", "top");
    if (thread.needs_reply && thread.awaiting_me) top.append(el("span", "dot"));
    top.append(el("span", "subject", thread.subject));
    if (thread.high_risk) top.append(el("span", "tag-risk", "High risk"));
    top.append(el("span", "muted small", when(thread.last_date)));
    const working = { summarizing: "Summarizing… ", reading: "Reading files… ", drafting: "Drafting… " }[thread.status] || "";
    row.append(top, el("div", "line", working + (thread.one_line || thread.snippet || "")));
    row.onclick = () => selectThread(thread.id);
    list.append(row);
  }
}

async function refreshProfile() {
  // Load once per visit to the tab, so typing is never overwritten by the 5-second refresh.
  if (view.profileLoaded === view.clientId) return;
  const data = await api(`/api/clients/${view.clientId}/profile`);
  view.profileLoaded = view.clientId;
  renderProfile(data);
}

function renderProfile(data) {
  const form = $("profile-form");
  form.replaceChildren();
  for (const field of data.fields) {
    const label = el("label", "", field.label);
    if (field.by === "auto") label.append(el("span", "auto", "from email"));
    const input = el(field.key === "notes" ? "textarea" : "input");
    if (field.key !== "notes") input.type = "text";
    input.name = field.key;
    input.value = field.value || "";
    input.placeholder = field.hint || "";
    label.append(input);
    form.append(label);
  }
  const contacts = $("profile-contacts");
  contacts.replaceChildren();
  if (!data.contacts.length) contacts.append(el("li", "muted", "Nobody yet."));
  for (const person of data.contacts) contacts.append(el("li", "", `${person.name} <${person.email}>`));
  $("profile-saved").textContent = "";
}

async function refreshFiles() {
  const isLibrary = view.clientId === 0;
  $("library-tools").hidden = !isLibrary;
  $("files-hint").textContent = isLibrary
    ? "Shared reference documents. Claude searches these when drafting for any client, and links the ones it used. You can also add your own files below."
    : "Reference files for this client (PDF, Word, Excel, text). Claude searches them when drafting.";
  if (isLibrary && view.library) {
    const lib = view.library;
    $("pack-btn").disabled = lib.running;
    $("pack-btn").textContent = lib.running ? `Downloading ${lib.done} of ${lib.total}…`
      : lib.count ? "Refresh HR starter pack" : "Load HR starter pack (California + federal)";
    $("pack-status").textContent = lib.running ? "Fetching each document from the agency's own website."
      : lib.finished_at ? `Finished: ${lib.total - lib.failed.length} of ${lib.total} documents loaded.` : "";
    const failed = $("pack-failed");
    failed.replaceChildren();
    for (const item of lib.failed) failed.append(el("li", "", `Not loaded: ${item.title} (${item.reason})`));
  }
  const files = await api(`/api/clients/${view.clientId}/files`);
  const list = $("file-list");
  list.replaceChildren();
  if (!files.length) list.append(el("li", "muted", "No files yet."));
  for (const file of files) {
    const item = el("li");
    const doc = el("span", "doc");
    if (file.source_url && file.source_url.startsWith("https://")) {
      const link = el("a", "", file.filename);
      link.href = file.source_url; link.target = "_blank"; link.rel = "noopener noreferrer";
      doc.append(link);
    } else {
      doc.append(document.createTextNode(file.filename));
    }
    const facts = [file.publisher, file.fetched_at ? `downloaded ${when(file.fetched_at)}` : null,
      `${Math.round(file.chars / 1000).toLocaleString()}k characters`].filter(Boolean);
    doc.append(el("span", "sub", facts.join(" · ")));
    const remove = el("button", "quiet", "Remove");
    remove.onclick = async () => { await api(`/api/files/${file.id}`, { method: "DELETE" }); refreshFiles(); };
    item.append(doc, remove);
    list.append(item);
  }
}

// ---------- right: one thread ----------

async function selectThread(id) {
  view.threadId = id;
  view.draftStamp = null;
  view.dirty = false;
  view.askedSummary = false;
  view.filesStamp = null;
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

  const busy = thread.queued || ["summarizing", "reading", "drafting"].includes(thread.status);
  renderFiles(thread, busy);
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

  const risk = thread.risk || {};
  $("risk-flag").hidden = !risk.active;
  $("risk-box").hidden = !risk.high;
  $("risk-reasons").textContent = risk.active
    ? `Flagged because: ${risk.reasons.join("; ") || "sensitive employment matter"}. The draft is a holding reply; consider employment counsel before advising.`
    : "You cleared the high-risk flag on this email.";
  $("risk-btn").textContent = risk.active ? "Not high risk: write a normal reply" : "Flag as high risk again";
  $("risk-btn").disabled = busy;
  view.riskActive = Boolean(risk.active);

  const draft = thread.draft;
  const box = $("draft");
  if (draft && draft.updated_at !== view.draftStamp && !(view.dirty && document.activeElement === box)) {
    box.value = draft.body;
    view.draftStamp = draft.updated_at;
    view.dirty = false;
  } else if (!draft && !view.dirty) {
    box.value = "";
  }
  $("draft-status").textContent = thread.status === "drafting" ? "writing…"
    : thread.status === "reading" ? "reading files and links…" : thread.queued ? "queued…" : "";
  $("regen-btn").textContent = draft ? "Regenerate" : "Draft a reply";
  $("regen-btn").disabled = busy || !canReply;
  $("copy-btn").disabled = !box.value.trim();
  $("gmail-btn").disabled = !box.value.trim() || !canReply;
  $("draft-note").textContent = thread.status === "error" ? `Last attempt failed: ${thread.error}`
    : draft && draft.gmail_draft_id ? (view.demo ? "Demo: this is where the Gmail draft would be confirmed."
      : "A copy of this draft is in Gmail. Review and send it from there.")
    : draft && draft.for_message_id !== thread.last_inbound_id ? "A newer message arrived after this draft was written."
    : "";

  const sources = $("sources");
  sources.replaceChildren();
  const kinds = { attachment: "Attachment", link: "Link", drive: "Drive", upload: "Uploaded file", library: "Library", past_reply: "Your earlier reply" };
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

function setDetailTab(tab) {
  view.detailTab = tab;
  $("dtab-email").classList.toggle("on", tab === "email");
  $("dtab-files").classList.toggle("on", tab === "files");
  $("messages").hidden = tab !== "email";
  $("thread-files").hidden = tab !== "files";
}

function fileSize(bytes) {
  if (!bytes) return "";
  return bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function renderFiles(thread, busy) {
  const files = thread.files || [];
  $("dtab-files").textContent = `Files (${files.length})`;
  const unread = files.some((f) => f.status === "pending" || f.status === "error");
  $("read-files-btn").hidden = !unread || view.detailTab !== "files";
  $("read-files-btn").disabled = busy;

  // Rebuild only when something changed, so an opened "extracted text" panel stays open.
  const stamp = thread.id + JSON.stringify(files.map((f) => [f.id, f.status, f.summary, f.name]));
  if (stamp === view.filesStamp) return;
  view.filesStamp = stamp;

  const box = $("thread-files");
  box.replaceChildren();
  if (!files.length) box.append(el("p", "muted pad", "This email has no attachments or links."));
  const methods = { ocr: "Read with OCR", text: "Text read directly", web: "Web page", drive: "Google Drive file" };
  for (const file of files) {
    const card = el("div", "file");
    const contentUrl = `/api/thread-files/${file.id}/content`;
    if (file.viewable && (file.mime || "").startsWith("image/")) {
      const thumb = el("img", "thumb");
      thumb.src = contentUrl; thumb.alt = "";
      card.append(thumb);
    }
    const body = el("div", "body");
    const href = file.kind === "link" ? file.url : file.viewable ? contentUrl : null;
    if (href && (href.startsWith("https://") || href.startsWith("http://") || href.startsWith("/api/"))) {
      const link = el("a", "name", file.name || file.url);
      link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
      body.append(link);
    } else {
      body.append(el("span", "name", file.name || "file"));
    }
    const tags = el("div", "tags");
    tags.append(el("span", "", file.kind === "link" ? "Link" : "Attachment"));
    if (file.size) tags.append(el("span", "", fileSize(file.size)));
    if (file.method) tags.append(el("span", file.method === "ocr" ? "ocr" : "", methods[file.method] || file.method));
    body.append(tags);
    const status = {
      pending: file.kind === "link" ? "Not opened yet." : "Not read yet.",
      reading: "Reading…",
      skipped: `Skipped. ${file.note || ""}`,
      error: `Could not read: ${file.note || "unknown problem"}`,
    }[file.status];
    body.append(el("p", status ? "muted" : "", status || file.summary || ""));
    if (file.kind === "link") body.append(el("p", "muted small", file.url));
    if (file.status === "ready" && file.text) {
      const details = el("details");
      details.append(el("summary", "", file.method === "ocr" ? "Show transcribed text" : "Show extracted text"), el("pre", "", file.text));
      body.append(details);
    }
    card.append(body);
    box.append(card);
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

$("dtab-email").onclick = () => { setDetailTab("email"); refreshThread(); };
$("dtab-files").onclick = () => { setDetailTab("files"); refreshThread(); };
$("read-files-btn").onclick = guard(async () => {
  await api(`/api/threads/${view.threadId}/read-files`, { method: "POST" });
  await refreshThread();
});
$("demo-btn").onclick = guard(async () => {
  const sent = await api("/api/demo/deliver", { method: "POST" });
  toast(`New email: ${sent.subject}`);
  await tick();
});
$("tab-profile").onclick = () => setTab("profile");
$("profile-save").onclick = guard(async (event) => {
  event.preventDefault();
  const fields = {};
  for (const input of $("profile-form").elements) if (input.name) fields[input.name] = input.value;
  renderProfile(await api(`/api/clients/${view.clientId}/profile`, { method: "PUT", body: { fields } }));
  $("profile-saved").textContent = "Saved. New drafts will use it.";
  await refreshState();
});
$("risk-btn").onclick = guard(async () => {
  await api(`/api/threads/${view.threadId}/risk`, { method: "POST", body: { dismissed: view.riskActive } });
  await refreshThread();
  await refreshMiddle();
});
$("pack-btn").onclick = guard(async () => {
  await api("/api/library/load-hr-pack", { method: "POST" });
  await refreshState();
  await refreshFiles();
});
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
    toast(view.demo ? "Demo: with a real mailbox this draft would now be in Gmail. Nothing was sent."
      : "Draft created in Gmail. Nothing was sent.");
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
    if (view.clientId !== null && (view.tab === "threads" || (view.clientId === 0 && view.library.running))) await refreshMiddle();
    else if (view.clientId === 0) $("pack-btn").textContent = view.library.count ? "Refresh HR starter pack" : $("pack-btn").textContent;
    await refreshThread();
  } catch (error) {
    $("sync").textContent = "App not reachable. Is it still running?";
    $("sync").className = "pill error";
  }
}

// A link such as /?thread=<id>&tab=files opens straight onto that thread.
async function start() {
  await tick();
  const params = new URLSearchParams(location.search);
  const threadId = params.get("thread");
  if (params.get("client") !== null) await selectClient(Number(params.get("client")));
  if (params.get("ctab")) setTab(params.get("ctab"));
  if (threadId) {
    try {
      const thread = await api(`/api/threads/${encodeURIComponent(threadId)}`);
      view.clientId = thread.client_id;
      await refreshState();
      if (params.get("tab") === "files") setDetailTab("files");
      await selectThread(threadId);
    } catch (error) {
      toast(error.message);
    }
  }
  setInterval(tick, 5000);
}

start();
