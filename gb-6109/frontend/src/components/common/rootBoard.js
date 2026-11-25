import { empty, section } from "./panel.js";
import { table } from "./table.js";
import { el } from "../../utils/dom.js";

function statusPill(entry) {
  const label = entry.status_label || (entry.quarantined ? "仍隔离" : entry.recovered ? "已恢复" : "正常");
  return el("span", {
    class: `root-status status-${entry.status}`,
    "data-testid": "root-status",
    "data-status": entry.status,
    "data-quarantined": String(Boolean(entry.quarantined)),
    "data-recovered": String(Boolean(entry.recovered)),
  }, label);
}

function recipeValue(entry) {
  if (!entry.readable) return el("span", {class: "muted"}, "不可读");
  return `${entry.fine_bp} / ${entry.coarse_bp}`;
}

export function rootBoard(board) {
  if (!board || !Array.isArray(board.entries)) return null;

  const search = el("input", {
    type: "search",
    "aria-label": "查询配方隔离状态",
    placeholder: "输入配方号，例如 BR-053",
    "data-testid": "root-search",
  });
  const result = el("div", {"data-testid": "root-table-host"});
  const summary = el("p", {class: "muted", "data-testid": "root-counts"});

  function render() {
    const keyword = search.value.trim().toLowerCase();
    const entries = board.entries.filter(entry => !keyword ||
      String(entry.recipe || "").toLowerCase().includes(keyword) ||
      String(entry.root_label || "").includes(keyword) ||
      String(entry.page_id || "").includes(keyword));
    const quarantined = entries.filter(entry => entry.quarantined).length;
    const recovered = entries.filter(entry => entry.recovered).length;
    summary.textContent = keyword
      ? `查询到 ${entries.length} 个根条目：${quarantined} 个仍隔离，${recovered} 个已恢复。`
      : `生效根与封存根共 ${entries.length} 个条目：${quarantined} 个仍隔离，${recovered} 个已恢复；其余配方可正常查询。`;
    result.replaceChildren(entries.length ? table(
      ["配方", "配方根", "根次", "物理页", "细料/粗料基点", "隔离状态"],
      entries.map(entry => [
        el("strong", {"data-testid": "root-recipe"}, entry.recipe),
        entry.root_label,
        `R${entry.revision}`,
        `P${entry.page_id}`,
        recipeValue(entry),
        statusPill(entry),
      ]),
      "可见配方根隔离与恢复状态",
    ) : empty("没有符合查询条件的配方根条目。"));
  }

  search.addEventListener("input", render);
  render();

  return section("可见配方根：坏页隔离 / 恢复查询",
    el("div", {
      class: "stack root-board",
      "data-testid": "root-board",
      "data-quarantine-board": "true",
      "data-visible-root-board": "true",
    },
      el("p", {}, "共用物理页损坏时，生效根和封存根中的对应配方分别标记；凭已签留档核出同版副本并修复后，状态变为已恢复。"),
      search,
      summary,
      result));
}

const TICKET_LABELS = {
  pending: "待处理", verifying: "核验留档中", verified: "同版留档已核验",
  interrupted: "中断可续跑", restored: "已写回待收尾", completed: "已恢复",
  quarantined: "维持隔离", superseded: "已换版，旧修复停止",
};

export function repairTicketBoard(tickets) {
  if (!Array.isArray(tickets) || !tickets.length) return null;
  return section("坏页修复检查点", table(
    ["修复单号", "物理页", "状态", "续跑检查点", "尝试次数", "说明"],
    tickets.map(ticket => [
      el("code", {"data-testid": "repair-ticket"}, ticket.ticket_id),
      `P${ticket.page_id}`,
      el("span", {
        class: `ticket-state state-${ticket.state}`,
        "data-testid": "repair-state",
        "data-state": ticket.state,
      }, TICKET_LABELS[ticket.state] || ticket.state),
      ticket.checkpoint,
      String(ticket.attempts ?? 1),
      ticket.reason || "—",
    ]),
    "坏页修复中断与续跑检查点",
  ));
}
