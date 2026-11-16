// 页面交互：选择样例、触发处理、重算、错误反馈。所有请求走同源接口。
const state = {
  sample: null,
  busy: false,
  notice: null,
};

function el(id) {
  return document.getElementById(id);
}

function notice(message, isError = false) {
  const box = el("notice");
  if (!box) return;
  box.textContent = message;
  box.classList.toggle("is-error", Boolean(isError));
  box.hidden = false;
}

function readSample() {
  const select = el("sample-select");
  if (select) state.sample = select.value;
  return state.sample;
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = { error: "E_BAD_RESPONSE", message: "响应不是 JSON" };
  }
  if (!response.ok) {
    const code = payload && payload.error ? payload.error : response.status;
    const detail = payload && payload.message ? payload.message : "请求失败";
    throw new Error(code + " " + detail);
  }
  return payload;
}

function lock(button, label) {
  state.busy = true;
  if (!button) return;
  button.dataset.label = button.textContent;
  button.textContent = label;
  button.disabled = true;
}

function unlock(button) {
  state.busy = false;
  if (!button) return;
  button.textContent = button.dataset.label || button.textContent;
  button.disabled = false;
}

async function runFlow(button, url, label) {
  if (state.busy) return;
  const sample = readSample();
  lock(button, label);
  try {
    const payload = await postJSON(url, { sample: sample, mode: "full" });
    const replay = payload.idempotent_replay ? " · 命中已有批次" : "";
    notice("批次 " + payload.batch_id + replay);
    window.setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    notice(String(error.message || error), true);
  } finally {
    unlock(button);
  }
}

async function recompute(button, url, label) {
  if (state.busy) return;
  lock(button, label);
  try {
    await postJSON(url, {});
    notice("已重算");
    window.setTimeout(() => window.location.reload(), 400);
  } catch (error) {
    notice(String(error.message || error), true);
  } finally {
    unlock(button);
  }
}

function bind() {
  const runButton = el("run-button");
  if (runButton) {
    runButton.addEventListener("click", () => runFlow(runButton, "/api/runs", "处理中"));
  }
  const rerun = el("rerun-button");
  if (rerun) {
    rerun.addEventListener("click", () => runFlow(rerun, "/api/runs", "处理中"));
  }
  const importButton = el("import-button");
  if (importButton) {
    importButton.addEventListener("click",
      () => runFlow(importButton, "/api/readings/import", "导入中"));
  }
  const rebuild = el("rebuild-button");
  if (rebuild) {
    rebuild.addEventListener("click",
      () => recompute(rebuild, "/api/intervals/rebuild", "重算中"));
  }
  const allocate = el("allocate-button");
  if (allocate) {
    allocate.addEventListener("click",
      () => recompute(allocate, "/api/months/allocate", "重算中"));
  }
  document.querySelectorAll("[data-print]").forEach((button) => {
    button.addEventListener("click", () => window.print());
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const box = el("notice");
      if (box) box.hidden = true;
    }
  });
  readSample();
}

bind();
