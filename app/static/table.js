// 表格排序：点表头按该列升序/降序，数字列按数值比较，其余按文本。
const collator = new Intl.Collator("zh-Hans-CN", { numeric: true });

function numericValue(text) {
  const cleaned = text.replace(/[,\s]/g, "").replace(/[kWh元%]+$/u, "");
  if (!cleaned) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) ? value : null;
}

function cellText(row, index) {
  const cell = row.children[index];
  return cell ? cell.textContent.trim() : "";
}

function sortTable(table, header) {
  const headers = Array.from(table.tHead.querySelectorAll("th[data-key]"));
  const index = Array.from(table.tHead.rows[0].children).indexOf(header);
  const body = table.tBodies[0];
  const rows = Array.from(body.rows);
  const ascending = header.dataset.order !== "asc";
  headers.forEach((item) => {
    item.classList.remove("is-sorted");
    delete item.dataset.order;
  });
  header.classList.add("is-sorted");
  header.dataset.order = ascending ? "asc" : "desc";
  rows.sort((left, right) => {
    const a = cellText(left, index);
    const b = cellText(right, index);
    const na = numericValue(a);
    const nb = numericValue(b);
    let result;
    if (na !== null && nb !== null) {
      result = na - nb;
    } else {
      result = collator.compare(a, b);
    }
    return ascending ? result : -result;
  });
  rows.forEach((row) => body.appendChild(row));
}

document.querySelectorAll("table[data-sortable]").forEach((table) => {
  table.querySelectorAll("th[data-key]").forEach((header) => {
    header.setAttribute("scope", "col");
    header.addEventListener("click", () => sortTable(table, header));
  });
});
