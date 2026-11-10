// 量规：列表、查看 YAML 与渲染给模型的提示词、复制为自定义量规并校验保存
import { api } from "../api.js";
import { $, esc, render, toast } from "../ui.js";

const KIND = { essay_en: "英语作文", essay_zh: "中文作文", short_answer: "简答/数学", paper: "论文", generic: "通用" };

export default async function ({ params, user }) {
  const canEdit = user.role === "teacher" || user.role === "admin";
  if (!params[0]) {
    const list = await api("/api/rubrics");
    render(`
      <h1>评分量规</h1>
      <p class="muted small">量规用 YAML 定义：档次、评分维度（分项满分之和 = 总分）、扣分项、复核策略。内置量规不可修改，可复制后自定义。</p>
      <div class="card"><ul class="list">${list.map(r => `<li><a class="item" href="#/rubrics/${esc(r.key)}"><div>
        <div class="title">${esc(r.name)}</div><div class="sub mono">${esc(r.key)} · v${esc(r.version)}</div></div>
        <div class="row"><span class="chip">${esc(KIND[r.kind] || r.kind)}</span>${r.builtin ? '<span class="chip info">内置</span>' : '<span class="chip ok">自定义</span>'}</div></a></li>`).join("")}</ul></div>`);
    return;
  }
  const key = params[0];
  const d = await api(`/api/rubrics/${encodeURIComponent(key)}`);
  const r = d.rubric;
  render(`
    <div class="spread"><h1>${esc(r.name)}</h1><a class="btn sm" href="#/rubrics">返回列表</a></div>
    <p class="small muted">${esc(r.source)}</p>
    <div class="grid two">
      <div class="card">
        <h2>档次</h2>
        ${r.bands.length ? `<div class="table-wrap"><table><thead><tr><th>档次</th><th>分数</th><th>描述</th></tr></thead><tbody>
        ${r.bands.map(b => `<tr><td>${esc(b.name)}</td><td class="num">${r.band_scale === "ratio" ? `${Math.round(b.min * 100)}–${Math.round(b.max * 100)}%` : `${b.min}–${b.max}`}</td><td class="small">${esc(b.descriptor)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">无</p>'}
        <h2 style="margin-top:12px">评分维度</h2>
        ${r.criteria.length ? r.criteria.map(c => `<p><b>${esc(c.name)}</b>（${c.max} 分）<br><span class="small">${esc(c.descriptor)}</span></p>`).join("") : '<p class="small muted">简答/数学类：每道题就是一个评分维度，在布置作业时填写。</p>'}
        ${r.deductions.length ? `<h2>扣分项</h2><ul class="small">${r.deductions.map(x => `<li>${esc(x.name)}：${esc(x.rule)}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="card">
        <h2>发给评分模型的量规文本</h2>
        <pre class="small" style="white-space:pre-wrap;max-height:420px;overflow:auto">${esc(d.prompt_preview)}</pre>
      </div>
    </div>
    <div class="card">
      <h2>${d.builtin ? "复制为自定义量规" : "编辑量规"}</h2>
      ${canEdit ? `<p class="small muted">${d.builtin ? "已自动把 key 改为 *_custom，修改后先校验再保存。" : ""}</p>
      <textarea id="y" class="code">${esc(d.builtin ? d.yaml_text.replace(/^key:\s*(\S+)/m, "key: $1_custom").replace(/^name:\s*(.+)$/m, "name: $1（自定义）") : d.yaml_text)}</textarea>
      <div class="row" style="margin-top:8px"><button id="val">校验</button><button class="primary" id="save">保存</button></div>
      <div id="out"></div>` : `<pre class="small">${esc(d.yaml_text)}</pre>`}
    </div>`);
  if (!canEdit) return;
  $("#val").onclick = async () => {
    const res = await api("/api/rubrics/validate", { method: "POST", body: { yaml_text: $("#y").value } });
    $("#out").innerHTML = res.ok ? `<div class="notice ok">校验通过：${esc(res.rubric.name)}，满分 ${res.rubric.max_score ?? "按题目"}</div>` : `<div class="notice danger" style="white-space:pre-wrap">${esc(res.error)}</div>`;
  };
  $("#save").onclick = async () => {
    try {
      const res = await api("/api/rubrics", { method: "POST", body: { yaml_text: $("#y").value } });
      toast("已保存");
      location.hash = `#/rubrics/${res.key}`;
    } catch (e) { $("#out").innerHTML = `<div class="notice danger" style="white-space:pre-wrap">${esc(e.message)}</div>`; }
  };
}
