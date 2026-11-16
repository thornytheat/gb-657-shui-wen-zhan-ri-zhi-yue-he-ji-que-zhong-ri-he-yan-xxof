// 阶段卡与重试：失败阶段打开发起重跑，成功后回到总览。
const state = { busy: false };

function notice(message, isError = false) {
  const box = document.getElementById("notice");
  if (!box) return;
  box.textContent = message;
  box.classList.toggle("is-error", Boolean(isError));
  box.hidden = false;
}

async function retry(batchId) {
  if (state.busy) return;
  state.busy = true;
  try {
    const response = await fetch("/api/batches/" + encodeURIComponent(batchId) + "/retry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error((payload.error || response.status) + " " + (payload.message || ""));
    }
    notice("批次 " + payload.batch_id + " · " + payload.status);
    window.setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    notice(String(error.message || error), true);
  } finally {
    state.busy = false;
  }
}

document.querySelectorAll(".retry-button").forEach((button) => {
  button.addEventListener("click", () => {
    const batchId = button.dataset.batch;
    if (batchId) retry(batchId);
  });
});

// 阶段卡：失败阶段高亮，其余按完成态显示序号
document.querySelectorAll(".card").forEach((card) => {
  const badge = card.querySelector(".badge");
  if (badge && badge.textContent.trim() === "失败") {
    card.classList.add("card-failed");
  }
});
