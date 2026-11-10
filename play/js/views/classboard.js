// 班级看板：分数分布、档次、各维度均分、共性问题、需要关注的学生、AI 与教师一致性
import { api } from "../api.js";
import { barChart, PALETTE } from "../charts.js";
import { $, bar, demoNotice, esc, fmtScore, pct, render, SUBJECTS } from "../ui.js";

export default async function ({ params }) {
  const id = Number(params[0]);
  const [d, students, classes] = await Promise.all([api(`/api/classes/${id}/dashboard`), api(`/api/classes/${id}/students`), api("/api/classes")]);
  const cls = classes.find(c => c.id === id) || { name: `班级 ${id}` };
  const asgs = d.assignments.filter(a => a.graded > 0);
  const agree = Object.entries(d.agreement || {});
  render(`
    <div class="spread"><h1>${esc(cls.name)} · 班级看板</h1><a class="btn sm" href="#/assignments">布置作业</a></div>
    ${d.demo_count ? demoNotice(`本班 ${d.demo_count} 份提交为演示数据，“教师复核”与一致性数字也是模拟的。`) : ""}
    <div class="stats">
      <div class="stat"><div class="v">${d.student_count}</div><div class="k">学生</div></div>
      <div class="stat"><div class="v">${d.submission_count}</div><div class="k">提交</div></div>
      <div class="stat"><div class="v">${d.pending_review}</div><div class="k"><a href="#/submissions?status=ai_done">待复核 ›</a></div></div>
      <div class="stat"><div class="v">${d.status.reviewed || 0}</div><div class="k">已复核</div></div>
    </div>
    <div class="card">
      <div class="spread"><h2>单次作业分析</h2>
        <select id="asel" style="max-width:320px">${asgs.map(a => `<option value="${a.id}">${esc(a.title)}</option>`).join("")}</select></div>
      <div id="adetail">${asgs.length ? "" : '<p class="empty">还没有已评分的作业</p>'}</div>
    </div>
    <div class="grid two">
      <div class="card"><h2>共性问题</h2>
        ${d.common_errors.length ? `<div class="chart-box"><canvas id="cerr"></canvas></div>
        <div class="table-wrap"><table><thead><tr><th>错误类型</th><th class="num">次数</th><th class="num">涉及学生</th></tr></thead><tbody>
        ${d.common_errors.map(e => `<tr><td>${esc(e.label)}</td><td class="num">${e.count}</td><td class="num">${e.students}（${pct(e.student_ratio)}）</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty">暂无</p>'}
        ${d.common_quotes.length ? `<h3 style="margin-top:12px">多人重复出现的错误</h3><ul class="small">${d.common_quotes.map(q => `<li><span class="chip">${esc(q.label)}</span> <span class="mono">${esc(q.quote)}</span> × ${q.count}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="card"><h2>需要关注的学生</h2>
        <ul class="list">${d.attention.map(a => `<li><a class="item" href="#/student/${a.student_id}"><span class="title">${esc(a.name)}</span><span class="small muted">${esc(a.reason)}</span></a></li>`).join("") || '<li class="empty">暂无</li>'}</ul>
        <h2 style="margin-top:16px">学生画像</h2>
        <div class="row">${students.map(s => `<a class="btn sm" href="#/student/${s.id}">${esc(s.name)}</a>`).join("")}</div>
      </div>
    </div>
    <div class="card"><h2>AI 初评与教师复核的一致性</h2>
      <p class="small muted">每份作业取最新一次复核。QWK（二次加权 Kappa）≥0.7 通常认为一致性较好；“±1 档”指档次相差不超过一档的比例；偏差为正表示 AI 比教师给分宽松。当前评分模型为离线规则（mock）时，这些数字只反映规则基线。</p>
      ${agree.length ? `<div class="table-wrap"><table><thead><tr><th>量规</th><th class="num">样本</th><th class="num">QWK</th><th class="num">Pearson</th><th class="num">MAE</th><th class="num">档次一致</th><th class="num">±1 档</th><th class="num">偏差</th><th class="num">教师改分率</th></tr></thead><tbody>
      ${agree.map(([k, a]) => `<tr><td>${esc(a.rubric_name || k)}<div class="small muted">满分 ${fmtScore(a.max_score)}</div></td><td class="num">${a.n}</td><td class="num">${a.qwk ?? "—"}</td><td class="num">${a.pearson ?? "—"}</td>
        <td class="num">${a.mae ?? "—"}</td><td class="num">${pct(a.band_exact)}</td><td class="num">${pct(a.within1_band)}</td><td class="num">${a.bias > 0 ? "+" : ""}${a.bias}</td><td class="num">${pct(a.changed_ratio)}</td></tr>
        ${a.blocked_reason ? `<tr><td colspan="9" class="small muted">${esc(a.blocked_reason)}</td></tr>` : ""}`).join("")}
      </tbody></table></div>` : '<p class="empty">还没有复核记录</p>'}
    </div>`);

  const drawAsg = aid => {
    const a = asgs.find(x => x.id === Number(aid));
    if (!a) return;
    $("#adetail").innerHTML = `
      <p class="small muted">${esc(SUBJECTS[a.subject] || a.subject)} · 已评 ${a.graded}/${a.submitted} · 已复核 ${a.reviewed} · 满分 ${fmtScore(a.max_score)}</p>
      <div class="stats">
        <div class="stat"><div class="v">${fmtScore(a.mean)}</div><div class="k">平均分</div></div>
        <div class="stat"><div class="v">${fmtScore(a.median)}</div><div class="k">中位数</div></div>
        <div class="stat"><div class="v">${fmtScore(a.std)}</div><div class="k">标准差</div></div>
        <div class="stat"><div class="v">${fmtScore(a.min)}–${fmtScore(a.max)}</div><div class="k">最低–最高</div></div>
      </div>
      <div class="grid two">
        <div><h3>分数分布</h3><div class="chart-box"><canvas id="hist"></canvas></div></div>
        <div><h3>各维度平均得分率</h3>${a.criteria_avg.map(c => `<div class="crit"><div class="spread"><span>${esc(c.name)}</span><span class="score">${pct(c.ratio)}</span></div>${bar(c.ratio)}</div>`).join("")}
          <h3 style="margin-top:12px">档次分布</h3><div class="row">${Object.entries(a.bands).map(([b, n]) => `<span class="chip info">${esc(b || "未定档")}：${n}</span>`).join("")}</div></div>
      </div>`;
    barChart($("#hist"), a.histogram.map(h => `${fmtScore(h.from)}–${fmtScore(h.to)}`), a.histogram.map(h => h.count), { label: "人数", suffix: " 人" });
  };
  if (asgs.length) {
    $("#asel").value = String(asgs[asgs.length - 1].id);
    drawAsg($("#asel").value);
    $("#asel").onchange = e => drawAsg(e.target.value);
  }
  if ($("#cerr")) barChart($("#cerr"), d.common_errors.map(e => e.label), d.common_errors.map(e => e.count), { horizontal: true, color: PALETTE[1], suffix: " 次" });
}
