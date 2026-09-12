"use strict";
const $ = (id) => document.getElementById(id);
let data = null, polling = true, initialized = false, busy = false, lastSnapshot = "", lastRead = null;
const views = {};

function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function text(id, value) { $(id).textContent = value; }
function strings(value) { return Array.isArray(value) ? value.filter(x => typeof x === "string") : []; }
function options(select, items) {
  const signature = JSON.stringify(items);
  if (select.dataset.options === signature) return;
  const old = select.value;
  select.replaceChildren(...items.map(([value, label]) => {
    const option = node("option", label); option.value = value; return option;
  }));
  if (items.some(([value]) => value === old)) select.value = old;
  select.dataset.options = signature;
}
function showDialog(title, build) {
  text("dialog-title", title); $("dialog-body").replaceChildren(); build($("dialog-body")); $("details").showModal();
}
function block(parent, title, value) {
  parent.append(node("h3", title), node("pre", typeof value === "string" ? value : JSON.stringify(value, null, 2)));
}
function link(parent, label, url) {
  if (!url) return;
  const a = node("a", label); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; parent.append(a);
}
function badge(el, outcome, label) { el.className = "badge " + outcome; el.textContent = label; }

function createRun(id) {
  const el = $(id);
  el.innerHTML = `<div class="run-head"><span class="run-label"></span><div class="run-state"><select class="state" aria-label="${id} starting state"></select><span class="badge"></span></div></div>
    <div class="media"><video controls playsinline preload="metadata" hidden></video><img hidden alt=""><div class="media-empty"><span class="empty-icon">↗</span><strong></strong><span class="empty-description"></span></div><span class="media-note" hidden></span></div>
    <div class="media-tools"><select class="mode" aria-label="${id} camera"><option value="video">External video</option><option value="agentview">External stills</option><option value="robot0_eye_in_hand">Wrist stills</option></select><input class="frame" aria-label="${id} still frame" type="range" min="0" max="0" value="0" hidden><span class="frame-label">Independent playback</span></div>
    <div class="metrics"><div class="metric"><strong class="success-count"></strong><span>LIBERO successes / completed</span></div><div class="metric"><strong class="step-count"></strong><span>Physics steps · batch total</span></div><div class="metric"><strong class="request-count"></strong><span>API requests · executor total</span></div></div>
    <div class="run-foot"><span class="run-info"></span><button class="trace">Inspect run ↗</button></div>`;
  const get = cls => el.querySelector(cls);
  const view = {id, el, get, batch: null, selected: null, mediaKey: "", lastFrame: null};
  get(".state").addEventListener("change", () => { view.lastFrame = null; renderRun(view, view.batch); });
  get(".mode").addEventListener("change", () => { view.lastFrame = null; renderMedia(view); });
  get(".frame").addEventListener("input", () => { view.lastFrame = Number(get(".frame").value); renderMedia(view); });
  get("video").addEventListener("error", () => {
    if (get(".mode").value === "video" && view.selected?.stills.length) {
      get(".mode").value = "agentview"; renderMedia(view);
      get(".frame-label").textContent += " · video unavailable";
    }
  });
  get(".trace").addEventListener("click", () => showRun(view));
  views[id] = view;
}
function renderRun(view, batch) {
  view.batch = batch;
  const get = view.get, episodes = batch?.episodes || [];
  get(".run-label").textContent = batch?.label || "Candidate";
  options(get(".state"), episodes.length ? episodes.map(e => [String(e.state), "State " + e.state]) : [["", "No episode"]]);
  get(".state").disabled = !episodes.length;
  view.selected = episodes.find(e => String(e.state) === get(".state").value) || null;
  const outcome = view.selected?.outcome || "pending";
  badge(get(".badge"), outcome, ({success:"SUCCESS",failure:"TASK FAILED",error:"POLICY ERROR",pending:"AWAITING RESULT"})[outcome]);
  const metrics = [[".success-count", batch?.successes == null ? null : batch.successes + " / " + batch.completed],
                   [".step-count", batch?.steps], [".request-count", batch?.requests]];
  for (const [cls, value] of metrics) {
    get(cls).textContent = value == null ? "not measured" : value.toLocaleString();
    get(cls).classList.toggle("missing", value == null);
  }
  const ep = view.selected;
  get(".run-info").textContent = ep ? [ep.model || "Model not recorded", ep.termination || "Outcome pending",
      batch.expected ? batch.completed + "/" + batch.expected + " episodes complete" : ""].filter(Boolean).join(" · ") : "A candidate appears when retest artifacts arrive.";
  renderMedia(view);
}
function renderMedia(view) {
  const get = view.get, ep = view.selected, mode = get(".mode").value;
  const video = get("video"), img = get("img"), empty = get(".media-empty"), note = get(".media-note");
  const key = (ep?.path || "") + "|" + mode;
  if (key !== view.mediaKey) { video.pause(); view.mediaKey = key; view.lastFrame = null; }
  let hasMedia = false;
  if (mode === "video" && ep?.video) {
    if (video.getAttribute("src") !== ep.video) {
      video.src = ep.video;
      const poster = ep.stills.find(s => s.camera === "agentview");
      if (poster) video.poster = poster.url;
    }
    hasMedia = true;
  }
  video.hidden = !(mode === "video" && hasMedia);
  if (video.hidden) video.pause();
  const frames = (ep?.stills || []).filter(s => s.camera === (mode === "video" ? "agentview" : mode));
  const stillMode = mode !== "video" || !ep?.video;
  get(".frame").hidden = !stillMode || !frames.length;
  if (stillMode && frames.length) {
    get(".frame").max = String(frames.length - 1);
    const index = Math.min(view.lastFrame ?? frames.length - 1, frames.length - 1);
    get(".frame").value = String(index);
    const frame = frames[index];
    if (img.getAttribute("src") !== frame.url) img.src = frame.url;
    img.alt = (view.batch?.label || view.id) + ", state " + ep.state + ", " + frame.camera + ", physics step " + frame.step;
    hasMedia = true;
    get(".frame-label").textContent = "Step " + frame.step + " · " + (index + 1) + "/" + frames.length;
    note.textContent = frame.camera === "agentview" ? "EXTERNAL CAMERA · STILL" : "WRIST CAMERA · STILL";
  } else {
    get(".frame-label").textContent = "Independent playback";
    note.textContent = "EXTERNAL CAMERA";
  }
  img.hidden = !(stillMode && frames.length);
  empty.hidden = hasMedia;
  note.hidden = !hasMedia;
  get(".media-empty strong").textContent = view.id === "candidate" ? "The next attempt belongs here." : "Waiting for camera evidence.";
  get(".empty-description").textContent = view.id === "candidate" ? "No candidate footage is recorded for this view. New artifacts appear automatically while updates are live." : "The viewer will show a video or recorded stills as soon as they are available.";
}
function showRun(view) {
  showDialog((view.batch?.label || "Candidate") + " · recorded evidence", body => {
    if (!view.selected) { body.append(node("p", "No episode artifacts are available yet.")); return; }
    const ep = view.selected;
    body.append(node("p", "Metrics on the main screen are batch totals. Below is the selected episode. A reached waypoint is not a LIBERO success."));
    block(body, "Selected episode · state " + ep.state, ep.result);
    const links = node("p"); link(links, "Original result.json", ep.result_url); body.append(links);
    block(body, "Batch location and declared protocol", {path:view.batch.path, manifest:view.batch.manifest});
    body.append(node("h3", "Motion evidence · last " + ep.controls.length + " of " + ep.control_records + " waypoint records"));
    if (ep.controls.length) {
      const table = node("table"), row = node("tr");
      ["Physics steps","Position error (m)","Rotation error (rad)","Waypoint reached","Stop"].forEach(t => row.append(node("th",t)));
      table.append(row);
      for (const c of ep.controls) {
        const tr = node("tr");
        const number = n => typeof n === "number" ? n.toPrecision(4) : "not measured";
        [String(c.start_step ?? "?") + "–" + String(c.end_step ?? "?"),number(c.position_error),number(c.rotation_error),
         typeof c.reached === "boolean" ? String(c.reached) : "not measured",c.stop_reason ?? "not measured"].forEach(t => tr.append(node("td",t)));
        table.append(tr);
      }
      body.append(table);
      block(body, "Latest requested and achieved pose", ep.controls[ep.controls.length - 1]);
    } else body.append(node("p", "No motion measurements recorded."));
    const source = node("p"); link(source, "Original control.jsonl", ep.control_url); body.append(source);
    const requests = node("p"); link(requests, "Original requests.jsonl", ep.requests_url); body.append(requests);
  });
}
function render() {
  text("run-name", data.run_name);
  text("task", data.task);
  text("run-status", data.status.replaceAll("_", " "));
  $("fixture").hidden = !data.fixture;
  options($("iteration"), [["","Latest available"], ...data.iterations.map(i => [i, i.replace("iteration-","Iteration ")])]);
  for (const el of document.querySelectorAll("[data-stage]")) {
    el.classList.toggle("active", Number(el.dataset.stage) === data.stage);
    el.classList.toggle("past", Number(el.dataset.stage) < data.stage);
  }
  renderRun(views.baseline, data.baseline);
  renderRun(views.candidate, data.candidate);
  text("skill-context", data.skills.tested ? "Diff: the tested skill → the proposed revision. Acceptance is a separate decision." :
       data.skills.no_skill ? "The recorded baseline has no prior skill. A revision will appear when the author exports one." : "No tested skill snapshot is available yet.");
  const diff = $("diff"); diff.replaceChildren();
  if (data.skills.diff) for (const line of data.skills.diff.split("\n")) diff.append(node("span", line + "\n", line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@") ? "meta" : line.startsWith("+") ? "add" : line.startsWith("-") ? "remove" : ""));
  else diff.append(node("span", data.skills.proposed ? "The proposal has no textual changes from the tested guide." : "No skill revision recorded.\nThis screen will show the actual Markdown change, not an illustrative result.", "empty-copy"));
  text("diagnosis", data.diagnosis.diagnosis || "No diagnosis artifact has been recorded for this view. A failed task establishes the outcome; its cause still needs evidence.");
  text("prediction", data.diagnosis.prediction || "Not recorded.");
  const uncertainties = strings(data.diagnosis.uncertainty);
  text("uncertainty", uncertainties.length ? "Uncertainty · " + uncertainties[0] + (uncertainties.length > 1 ? " (+" + (uncertainties.length - 1) + " more)" : "") : "Uncertainty: not recorded.");
  const titles = {keep:"Candidate retained",reject:"Candidate rejected",inconclusive:"Comparison inconclusive",deferred:"Skill revision deferred",pending:"Selection pending",not_available:"No learning gain established"};
  text("decision-title", titles[data.decision]);
  const counts = data.comparison;
  let detail = strings(counts.reasons).join(" · ");
  if (!detail) detail = ({keep:"The evaluator recorded a strict development success-count gain.",reject:"The evaluator retained the incumbent.",inconclusive:"The recorded comparison cannot establish a valid gain.",deferred:"The author or coordinator deferred revision. No candidate is accepted.",pending:"Waiting for the evaluator’s recorded comparison.",not_available:"This is a standalone attempt. No baseline/candidate learning comparison is recorded."})[data.decision];
  if (Number.isInteger(counts.attempts_per_condition) && Number.isInteger(counts.baseline_successes) && Number.isInteger(counts.candidate_successes)) detail = "Recorded comparison: " + counts.baseline_successes + "/" + counts.attempts_per_condition + " → " + counts.candidate_successes + "/" + counts.attempts_per_condition + ". " + detail;
  text("decision-detail", detail);
  badge($("decision-badge"), data.decision, data.decision === "not_available" ? "NOT MEASURED" : data.decision.toUpperCase());
  $("warnings-box").hidden = !data.warnings.length;
  text("warnings-title", data.warnings.length + " artifact notice" + (data.warnings.length === 1 ? "" : "s"));
  $("warnings").replaceChildren(...data.warnings.map(w => node("li",w)));
}
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const iteration = $("iteration").value;
    const response = await fetch("/api/snapshot" + (iteration ? "?iteration=" + encodeURIComponent(iteration) : ""), {cache:"no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const next = await response.json();
    if (!initialized) { polling = !next.replay; initialized = true; }
    data = next; const signature = JSON.stringify(next);
    if (signature !== lastSnapshot) { render(); lastSnapshot = signature; }
    lastRead = new Date();
    text("updated", "Read " + lastRead.toLocaleTimeString() + " · read-only");
    $("connection-error").hidden = true;
    connection();
  } catch (error) {
    text("connection", "Disconnected");
    text("connection-error", "Connection lost. Showing the last successfully read evidence. Refresh or restart the viewer server.");
    $("connection-error").hidden = false;
  } finally { busy = false; }
}
function connection() {
  text("connection", polling ? "Live artifacts · 3s" : data?.replay ? "Saved-run replay" : "Updates paused");
  text("poll-toggle", polling ? "Pause updates" : "Resume updates");
}
createRun("baseline"); createRun("candidate");
$("poll-toggle").addEventListener("click", () => { polling = !polling; connection(); if (polling) refresh(); });
$("refresh").addEventListener("click", refresh);
$("iteration").addEventListener("change", () => { lastSnapshot = ""; refresh(); });
$("close-dialog").addEventListener("click", () => $("details").close());
$("details").addEventListener("click", e => { if (e.target === $("details")) $("details").close(); });
$("expand-skill").addEventListener("click", () => {
  if (!data) return;
  showDialog("Tested skill & proposed revision", body => {
    body.append(node("p", "The tested skill may be a previously rejected candidate. It is not necessarily the accepted incumbent shown in the comparison."));
    block(body, "Tested skill · " + data.skills.tested_source, data.skills.tested || (data.skills.no_skill ? "Recorded baseline: no prior skill." : "Snapshot not available."));
    block(body, "Proposed revision · not accepted unless the evaluator says keep", data.skills.proposed || "No revision recorded.");
    block(body, "Accepted comparison baseline snapshot", data.baseline.skills.length ? data.baseline.skills : "No snapshot available.");
    if (data.skills.diff) block(body, "Text diff", data.skills.diff);
  });
});
$("expand-diagnosis").addEventListener("click", () => {
  if (!data) return;
  showDialog("Diagnosis & cited evidence", body => {
    block(body, "Diagnosis", data.diagnosis.diagnosis || "Not recorded.");
    block(body, "Predicted behavior", data.diagnosis.prediction || "Not recorded.");
    block(body, "Cited evidence", strings(data.diagnosis.evidence));
    block(body, "Uncertainty and alternatives", strings(data.diagnosis.uncertainty));
    block(body, "Missing evidence", data.evidence.missing_evidence || []);
    body.append(node("h3", "Indexed source artifacts"));
    const ul = node("ul");
    for (const item of data.evidence_links) {
      const li = node("li"); if (item.url) link(li, item.name, item.url); else li.textContent = item.name + " · unavailable"; ul.append(li);
    }
    if (!data.evidence_links.length) ul.append(node("li", "No evidence index recorded."));
    body.append(ul);
    block(body, "Source episode", data.evidence.source_episode || "Not recorded.");
  });
});
$("expand-comparison").addEventListener("click", () => {
  if (!data) return;
  showDialog("Recorded selection decision", body => {
    body.append(node("p", "The viewer displays the evaluator’s decision without recomputing promotion or modifying skills. A development result does not establish transfer to held-out tasks."));
    block(body, "comparison.json", Object.keys(data.comparison).length ? data.comparison : "No comparison artifact recorded.");
  });
});
refresh();
setInterval(() => { if (polling && !document.hidden) refresh(); }, 3000);
