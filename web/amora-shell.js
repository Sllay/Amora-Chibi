import { OperatorState } from "./operator-state.mjs";

const $ = (id) => document.getElementById(id);
const operatorState = new OperatorState(globalThis.localStorage);
const TERMINAL = new Set(["succeeded", "failed", "timed_out"]);
let activeJobId = null;
let pollTimer = null;

// The standalone Shell must not reintroduce a dependency on the host terminal.
// Reuse the existing Dev master-key flow so an operator can unlock this page
// directly from the browser when no pairing code can be generated on the host.
import("./dev-master-key.mjs?v=20261103-1")
  .then((module) => module.installDevMasterKey({ OperatorState }))
  .catch(() => {});

function token() {
  return operatorState.get();
}

function setLocked(locked) {
  $("operatorGate").classList.toggle("hidden", !locked);
  $("consolePanel").classList.toggle("hidden", locked);
}

function humanError(error) {
  return String(error?.message || error || "erro desconhecido").replace(/^\d+:\s*/, "");
}

async function api(path, options = {}) {
  const credential = token();
  if (!credential) {
    setLocked(true);
    throw new Error("modo operador bloqueado");
  }
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${credential}`);
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const text = await response.text();
  let payload = text;
  try { payload = text ? JSON.parse(text) : null; } catch (_) {}
  if (!response.ok) {
    if (response.status === 401) {
      operatorState.clear();
      setLocked(true);
    }
    const detail = typeof payload === "object" && payload?.detail ? payload.detail : payload;
    const pretty = typeof detail === "object" ? JSON.stringify(detail) : String(detail || response.statusText);
    const error = new Error(`${response.status}: ${pretty}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function seconds(ms) {
  if (ms === null || ms === undefined) return "—";
  return `${(Number(ms) / 1000).toFixed(2)}s`;
}

function mark(validated) {
  return validated ? "✓" : "✗";
}

function formatSocialAB(result) {
  const data = result?.data;
  const report = data?.report;
  if (!report) return JSON.stringify(result, null, 2);
  const lines = [];
  lines.push("Certification complete.");
  lines.push(`Corpus: ${data.corpus_id} · ${data.case_count} casos`);
  if (data.case_count < 11) lines.push("Diagnóstico parcial: a certificação completa dos 11 casos ainda é necessária.");
  lines.push(`Left : ${report.left_provider} · score médio ${Number(report.left_mean_mechanical_score || 0).toFixed(2)} · latência ${seconds(report.left_mean_latency_ms)}`);
  lines.push(`Right: ${report.right_provider} · score médio ${Number(report.right_mean_mechanical_score || 0).toFixed(2)} · latência ${seconds(report.right_mean_latency_ms)}`);
  lines.push(`Validated: ${report.left_validated_count}/${data.case_count} × ${report.right_validated_count}/${data.case_count}`);
  lines.push("");
  report.cases.forEach((item, index) => {
    lines.push(`[${index + 1}/${report.cases.length}] ${item.probe_id} · ${item.category}`);
    lines.push(`  ${item.left.provider}  ${seconds(item.left.latency_ms)} ${mark(item.left.validated)} score=${Number(item.left.score || 0).toFixed(1)}`);
    if (item.left.text) lines.push(`    ${item.left.text.replace(/\s+/g, " ").trim()}`);
    if (item.left.error_code) lines.push(`    error=${item.left.error_code}`);
    lines.push(`  ${item.right.provider}  ${seconds(item.right.latency_ms)} ${mark(item.right.validated)} score=${Number(item.right.score || 0).toFixed(1)}`);
    if (item.right.text) lines.push(`    ${item.right.text.replace(/\s+/g, " ").trim()}`);
    if (item.right.error_code) lines.push(`    error=${item.right.error_code}`);
    lines.push(`  Δ right-left = ${Number(item.mechanical_score_delta_right_minus_left || 0).toFixed(1)} · ordem=${item.execution_order}`);
    lines.push("");
  });
  lines.push("Human review required.");
  lines.push(`Promotion decision: ${String(data.promotion_decision || report.promotion_decision || "none").toUpperCase()}`);
  return lines.join("\n");
}

function formatJob(job) {
  if (job.status === "queued") return `amora> ${job.command}\n[queued] aguardando execução…`;
  if (job.status === "running") {
    const progress = Array.isArray(job.progress) ? job.progress : [];
    const lines = [`amora> ${job.command}`, `[running] ${progress.length} resposta(s) concluída(s)`];
    for (const item of progress) {
      lines.push(`[${item.probe_id}] ${item.provider} · ${seconds(item.latency_ms)} · ${item.validated ? "✓" : "✗"}`);
      if (item.text) lines.push(`  ${String(item.text).replace(/\\s+/g, " ").trim()}`);
      if (item.error_code) lines.push(`  error=${item.error_code}`);
    }
    lines.push("Aguardando próxima resposta…");
    return lines.join("\\n");
  }
  if (job.status === "timed_out") return `amora> ${job.command}\n[timeout] ${job.error_code || "COMMAND_TIMEOUT"}`;
  if (job.status === "failed") {
    const details = job.error_details && Object.keys(job.error_details).length
      ? `\n${JSON.stringify(job.error_details, null, 2)}`
      : "";
    return `amora> ${job.command}\n[failed] ${job.error_code || "COMMAND_FAILED"} · ${job.error_type || "error"}${details}`;
  }
  if (job.result?.kind === "social_provider_ab") return `amora> ${job.command}\n\n${formatSocialAB(job.result)}`;
  if (job.result?.kind === "command_help") {
    const commands = (job.result.commands || []).map((item) => `  ${item.usage}\n    ${item.description}`).join("\n");
    return `amora> ${job.command}\n\n${commands}`;
  }
  return `amora> ${job.command}\n\n${JSON.stringify(job.result || {}, null, 2)}`;
}

function renderJob(job) {
  activeJobId = String(job.job_id);
  $("resultTitle").textContent = `amora> ${job.command_path}`;
  $("jobStatus").textContent = `${job.status} · ${job.job_id}`;
  $("shellOutput").textContent = formatJob(job);
  $("shellRun").disabled = !TERMINAL.has(job.status);
}

function stopPolling() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function pollJob(jobId) {
  stopPolling();
  try {
    const job = await api(`/v1/operator/commands/${encodeURIComponent(jobId)}`);
    renderJob(job);
    await loadJobs().catch(() => {});
    if (!TERMINAL.has(job.status)) {
      pollTimer = setTimeout(() => pollJob(jobId), 1000);
    } else {
      $("shellRun").disabled = false;
    }
  } catch (error) {
    $("jobStatus").textContent = "erro";
    $("shellOutput").textContent = humanError(error);
    $("shellRun").disabled = false;
  }
}

async function runCommand(command) {
  stopPolling();
  $("shellRun").disabled = true;
  $("jobStatus").textContent = "enviando";
  $("shellOutput").textContent = `amora> ${command}\n[starting]…`;
  try {
    const job = await api("/v1/operator/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    });
    renderJob(job);
    await pollJob(job.job_id);
  } catch (error) {
    $("jobStatus").textContent = "erro";
    $("shellOutput").textContent = `amora> ${command}\n[error] ${humanError(error)}`;
    $("shellRun").disabled = false;
  }
}

async function loadJobs() {
  const jobs = await api("/v1/operator/commands");
  const list = $("jobsList");
  list.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Nenhum comando executado nesta instância.";
    list.appendChild(empty);
    return;
  }
  for (const job of jobs) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "ghost shell-job";
    const title = document.createElement("strong");
    title.textContent = `${job.command} · ${job.status}`;
    const id = document.createElement("span");
    id.textContent = String(job.job_id).slice(0, 8);
    const meta = document.createElement("small");
    meta.textContent = `${job.command_path} · timeout ${job.timeout_seconds}s`;
    row.append(title, id, meta);
    row.addEventListener("click", () => {
      renderJob(job);
      if (!TERMINAL.has(job.status)) pollJob(job.job_id);
    });
    list.appendChild(row);
  }
}

$("operatorPairForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("operatorPairError").textContent = "";
  const code = $("operatorPairCode").value.trim();
  if (!/^\d{6}$/.test(code)) {
    $("operatorPairError").textContent = "Digite o código de 6 dígitos.";
    return;
  }
  try {
    const response = await fetch("/v1/pairing/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, client: "operator" }),
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.detail || "código inválido");
    operatorState.save(payload.token);
    $("operatorPairCode").value = "";
    setLocked(false);
    await loadJobs();
  } catch (error) {
    $("operatorPairError").textContent = humanError(error);
  }
});

$("shellForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const command = $("shellInput").value.trim();
  if (command) runCommand(command);
});

for (const button of document.querySelectorAll(".quick-command")) {
  button.addEventListener("click", () => {
    const command = button.dataset.command || "";
    $("shellInput").value = command;
    if (command) runCommand(command);
  });
}

$("refreshJobs").addEventListener("click", () => loadJobs().catch((error) => {
  $("shellOutput").textContent = humanError(error);
}));

$("lockOperator").addEventListener("click", () => {
  stopPolling();
  operatorState.clear();
  activeJobId = null;
  setLocked(true);
});

if (token()) {
  setLocked(false);
  loadJobs().catch((error) => {
    $("shellOutput").textContent = humanError(error);
  });
} else {
  setLocked(true);
}
