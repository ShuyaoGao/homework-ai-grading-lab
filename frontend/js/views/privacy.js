// 隐私说明 + 监护人同意管理（家长）
import { api, auth } from "../api.js";
import { $$, esc, fmtDate, render, toast } from "../ui.js";

const POLICY = `
<h2>个人信息处理说明（摘要）</h2>
<p class="small muted">版本 v1 · 完整版见项目 docs/PRIVACY.md。本系统面向中小学，用户多为不满 14 周岁的未成年人，适用《个人信息保护法》关于敏感个人信息的规定。</p>
<ol>
  <li><b>我们处理什么：</b>学生姓名（可用化名）、学号、出生年份（只用于判断是否不满 14 周岁），作业照片、识别出的文字、分数和评语。不收集身份证号、住址、电话、人脸等信息。</li>
  <li><b>为什么处理：</b>只用于作业批改、教师复核和学情分析（学生画像、班级看板）。不用于广告，不对学生做公开排名。</li>
  <li><b>监护人同意：</b>不满 14 周岁学生的作业，须由监护人先在本页同意后才能上传。同意可随时撤回，撤回后不再处理新的作业。</li>
  <li><b>云端 AI 是单独选项：</b>默认只用学校服务器上的本地模型。只有监护人另外勾选“允许云端 AI 处理”，作业图片和文字才会发送给第三方大模型服务（可能位于境外）。发送内容不含系统中的学生姓名和学号，照片已删除位置等元数据；作业纸上如写有姓名，建议拍照时遮挡。</li>
  <li><b>保存多久：</b>作业照片默认保存 1 年后自动清理（管理员可调整），成绩与评语保留到学生毕业或监护人要求删除。</li>
  <li><b>您的权利：</b>可查阅、导出孩子的全部数据，可要求更正和删除。访问孩子数据的操作都会记录审计日志。</li>
  <li><b>AI 评分仅供参考：</b>所有分数以老师复核为准，不会仅凭 AI 结果作出影响孩子的决定。</li>
</ol>`;

export default async function () {
  const user = auth.user;
  if (!user || user.role !== "parent") {
    render(`<div class="card">${POLICY}</div>${user ? "" : '<a class="btn" href="#/login">返回登录</a>'}`);
    return;
  }
  const kids = await api("/api/students");
  const consents = await Promise.all(kids.map(k => api(`/api/students/${k.id}/consents`)));
  render(`
    <div class="card">${POLICY}</div>
    ${kids.map((k, i) => `
      <div class="card">
        <h2>${esc(k.name)}</h2>
        <label class="check"><input type="checkbox" data-s="${k.id}" data-scope="basic" ${k.consents.basic ? "checked" : ""}>
          <span>我是 ${esc(k.name)} 的监护人，已阅读上述说明，同意本系统处理孩子的作业照片与成绩，用于批改和学情分析。</span></label>
        <label class="check"><input type="checkbox" data-s="${k.id}" data-scope="cloud_ai" ${k.consents.cloud_ai ? "checked" : ""} ${k.consents.basic ? "" : "disabled"}>
          <span>（单独同意，可不选）允许把作业图片和文字发送给第三方云端大模型进行识别和评分（可能涉及数据出境）。不选择时只使用本地模型。</span></label>
        <div class="row"><button class="sm" data-export="${k.id}">导出孩子的全部数据</button></div>
        <details class="small" style="margin-top:8px"><summary>同意记录</summary><ul>
          ${consents[i].map(c => `<li>${c.scope === "basic" ? "基本处理" : "云端 AI"}：${fmtDate(c.granted_at, true)} 同意${c.revoked_at ? `，${fmtDate(c.revoked_at, true)} 撤回` : ""}</li>`).join("") || "<li>暂无</li>"}
        </ul></details>
      </div>`).join("")}`);
  $$("input[data-scope]").forEach(cb => cb.onchange = async () => {
    const scope = cb.dataset.scope;
    if (!cb.checked && !confirm(scope === "basic" ? "撤回后将不能再上传该孩子的作业（已有记录保留，可另行申请删除）。确定撤回？" : "撤回后将只使用本地模型。确定撤回？")) { cb.checked = true; return; }
    try {
      await api(`/api/students/${cb.dataset.s}/consents`, { method: "POST", body: { scope, granted: cb.checked } });
      toast(cb.checked ? "已记录您的同意" : "已撤回");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (e) { toast(e.message, true); cb.checked = !cb.checked; }
  });
  $$("[data-export]").forEach(b => b.onclick = async () => {
    try {
      const data = await api(`/api/students/${b.dataset.export}/export`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: `student_${b.dataset.export}_export.json` });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) { toast(e.message, true); }
  });
}
