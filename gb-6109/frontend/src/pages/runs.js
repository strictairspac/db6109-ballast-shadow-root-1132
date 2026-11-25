import { api } from "../api/workbench.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { status } from "../components/common/status.js";
import { table } from "../components/common/table.js";
import { poll } from "../hooks/poll.js";
import { el } from "../utils/dom.js";
import { time } from "../utils/format.js";

function reportState(run) {
  return el("div", {class: "stack", id: "run-state"}, status(run.status),
    el("p", {}, run.status === "queued" ? "后台正在处理，页面会自动更新。" : run.failure || run.result?.summary),
    run.status === "done" ? el("a", {class: "button", href: `#evidence/${run.id}`}, "查看业务证据") : null);
}

const QUICK_REPAIRS = [
  ["BR-K7-1125", "模拟双根坏页并续跑修复"],
  ["BR-K7-1126", "模拟修复与换版并发"],
  ["BR-K9-1125", "模拟无同版留档维持隔离"],
];

function quickRepairPanel() {
  const message = el("p", {role: "status", "data-testid": "quick-repair-message"});
  const buttons = QUICK_REPAIRS.map(([code, label]) => el("button", {
    type: "button", class: "quiet secondary", "data-testid": `quick-repair-${code}`,
    onclick: async () => {
      buttons.forEach(button => { button.disabled = true; });
      message.textContent = "正在创建坏页演练批次…";
      try {
        const cases = await api.cases();
        const fixture = cases.find(item => item.code === code);
        if (!fixture) throw new Error("坏页演练样本尚未载入，请确认后端已更新并完成种子载入。");
        const created = await api.createCase({
          title: `${fixture.title} · 快捷复演`,
          description: fixture.description,
          payload: fixture.payload,
        });
        const run = await api.enqueue(created.id, {parameter: 5});
        location.hash = `runs/${run.id}`;
      } catch (error) {
        message.textContent = error.message;
        buttons.forEach(button => { button.disabled = false; });
      }
    },
  }, label));
  return section("坏页损坏演练", el("div", {class: "stack", "data-testid": "quick-repair-panel"},
    el("p", {class: "muted"}, "按默认页池 5 直接建批：共用坏页会同时隔离生效根与封存根配方，并在可见配方根显示仍隔离或已恢复。"),
    el("div", {class: "template-bar"}, buttons),
    message));
}

export async function runsPage(config, route, signal, user) {
  const items = await api.runs();
  const latest = route.id ? await api.run(route.id, signal) : items[0];
  const detail = latest ? reportState(latest) : empty("进入资料页，选用一个业务样本并提交执行。");
  async function refresh() {
    try {
      const next = await api.run(latest.id, signal);
      if (!signal.aborted) {
        detail.replaceChildren(...reportState(next).childNodes);
        const badge = document.querySelector(`[data-run="${next.id}"] .status`);
        if (badge) badge.replaceWith(status(next.status));
      }
      return next.status === "queued";
    } catch (error) {
      if (!signal.aborted) detail.append(el("p", {role: "alert"}, error.message));
      return false;
    }
  }
  if (latest?.status === "queued") poll(refresh, signal);
  return el("div", {class: "page"},
    pageHeading(config.modules[2], config.parameter_label),
    user?.role === "operator" ? quickRepairPanel() : null,
    latest ? section(latest.case_title, detail) : detail,
    section("执行批次", items.length ? table(["资料", "编号", "参数", "状态", "提交时间"],
      items.map(run => [el("a", {"data-run": run.id, href: `#runs/${run.id}`}, run.case_title), run.id,
        run.parameter, el("span", {"data-run": run.id}, status(run.status)), time(run.created_at)]), "执行批次") :
      empty("暂无执行记录。")));
}
