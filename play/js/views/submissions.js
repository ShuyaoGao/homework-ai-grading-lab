// 提交列表 / 教师复核队列
import { api } from "../api.js";
import { $, esc, render, submissionItem } from "../ui.js";

export default async function ({ query, user }) {
  const status = query.get("status") || "";
  const needs = query.get("needs_review") || "";
  const classId = query.get("class_id") || "";
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (needs) qs.set("needs_review", needs);
  if (classId) qs.set("class_id", classId);
  qs.set("limit", "300");
  const list = await api(`/api/submissions?${qs}`);
  const isStaff = user.role === "teacher" || user.role === "admin";
  const title = status === "ai_done" ? "待复核" : "作业记录";
  render(`
    <div class="spread"><h1>${title}</h1><span class="muted small">共 ${list.length} 份</span></div>
    ${isStaff ? `<div class="card"><div class="row">
      <select id="st" style="max-width:220px"><option value="">全部状态</option>
        <option value="ai_done" ${status === "ai_done" ? "selected" : ""}>待复核</option>
        <option value="reviewed" ${status === "reviewed" ? "selected" : ""}>已复核</option>
        <option value="failed" ${status === "failed" ? "selected" : ""}>处理失败</option>
        <option value="queued,processing" ${status === "queued,processing" ? "selected" : ""}>处理中</option></select>
      <label class="check" style="margin:0"><input type="checkbox" id="nr" ${needs === "true" ? "checked" : ""}> 只看 AI 建议复核的</label>
    </div>${status === "ai_done" ? `<p class="small muted" style="margin:8px 0 0">建议按列表顺序逐份复核；标“建议复核”的是置信度低、贴近档次边界或识别质量差的作业。</p>` : ""}</div>` : ""}
    <div class="card"><ul class="list">${list.map(s => submissionItem(s, { showStudent: user.role !== "student" })).join("") || '<li class="empty">暂无记录</li>'}</ul></div>`);
  if (isStaff) {
    const go = () => {
      const q = new URLSearchParams();
      if ($("#st").value) q.set("status", $("#st").value);
      if ($("#nr").checked) q.set("needs_review", "true");
      location.hash = `#/submissions?${q}`;
    };
    $("#st").onchange = go;
    $("#nr").onchange = go;
  }
  void esc;
}
