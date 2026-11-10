// 学生画像：成绩趋势、写作能力雷达、知识点掌握度、高频错误、作业列表
import { api } from "../api.js";
import { barChart, lineChart, radarChart, PALETTE } from "../charts.js";
import { $, bar, demoNotice, esc, fmtScore, render, submissionItem, SUBJECTS } from "../ui.js";

export default async function ({ params, user }) {
  const id = Number(params[0]);
  const [p, subs] = await Promise.all([api(`/api/students/${id}/profile`), api(`/api/submissions?student_id=${id}&limit=100`)]);
  const subjects = Object.entries(p.subjects);
  const radarKeys = Object.keys(p.radar);
  render(`
    <div class="spread"><h1>${esc(p.student.name)} 的学习画像</h1>
      ${user.role !== "student" ? `<a class="btn sm" href="#/upload?student=${id}">上传作业</a>` : ""}</div>
    <p class="small muted">${esc(p.data_note)} · 共 ${p.submission_count} 份已评作业</p>
    ${p.demo_count ? demoNotice(`画像中有 ${p.demo_count} 份演示作业，趋势与掌握度仅用于展示功能。`) : ""}
    <div class="card"><h2>概况</h2><ul>${p.summary.map(s => `<li>${esc(s)}</li>`).join("")}</ul></div>
    <div class="stats">${subjects.map(([k, s]) => `<div class="stat"><div class="v">${Math.round(s.avg_ratio * 100)}%</div>
      <div class="k">${esc(s.label)} 平均得分率 · ${s.count} 次 · ${esc(s.trend)}</div></div>`).join("")}</div>
    <div class="grid two">
      <div class="card"><h2>成绩趋势（得分率）</h2><div class="chart-box tall"><canvas id="trend"></canvas></div></div>
      <div class="card"><div class="spread"><h2>写作能力雷达</h2>
        ${radarKeys.length > 1 ? `<select id="rsel" style="max-width:140px">${radarKeys.map(k => `<option value="${k}">${esc(p.radar[k].label)}</option>`).join("")}</select>` : ""}</div>
        ${radarKeys.length ? `<div class="chart-box tall"><canvas id="radar"></canvas></div>` : '<p class="empty">还没有作文/论文类作业</p>'}</div>
    </div>
    <div class="grid two">
      <div class="card"><h2>知识点掌握度</h2>
        <p class="small muted">按时间加权（近期表现权重更大）。≥80% 掌握，60–80% 基本掌握，&lt;60% 薄弱。</p>
        ${p.knowledge.map(k => `<div class="crit"><div class="spread"><span>${esc(k.name)} <span class="chip ${k.level === "薄弱" ? "danger" : k.level === "掌握" ? "ok" : "warn"}">${esc(k.level)}</span></span>
          <span class="score">${Math.round(k.mastery * 100)}%<span class="small muted"> · ${k.n}次</span></span></div>${bar(k.mastery)}</div>`).join("") || '<p class="empty">暂无数据</p>'}
      </div>
      <div class="card"><h2>高频错误类型</h2>
        ${p.errors.length ? `<div class="chart-box"><canvas id="errs"></canvas></div>` : '<p class="empty">暂无错误记录</p>'}
      </div>
    </div>
    <div class="card"><h2>作业记录</h2><ul class="list">${subs.map(s => submissionItem(s, { showStudent: false })).join("") || '<li class="empty">暂无</li>'}</ul></div>`);

  // 趋势：每个学科一条线，按时间顺序的统一横轴
  const labels = p.trend.map(t => `${t.date.slice(5)} ${SUBJECTS[t.subject] || ""}`);
  const ds = subjects.map(([k, s]) => ({ label: s.label, data: p.trend.map(t => (t.subject === k ? Math.round(t.ratio * 100) : null)) }));
  if ($("#trend")) lineChart($("#trend"), labels, ds);
  const drawRadar = key => {
    const r = p.radar[key];
    radarChart($("#radar"), r.dimensions.map(d => d.label), [{ label: r.label, data: r.dimensions.map(d => Math.round(d.value * 100)) }]);
  };
  if (radarKeys.length) drawRadar(radarKeys[0]);
  if ($("#rsel")) $("#rsel").onchange = e => drawRadar(e.target.value);
  if ($("#errs")) barChart($("#errs"), p.errors.map(e => e.label), p.errors.map(e => e.count), { horizontal: true, color: PALETTE[1], suffix: " 次" });
  void fmtScore;
}
