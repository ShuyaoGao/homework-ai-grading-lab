// 批改结果页：原图/处理后图片 ↔ 识别文字（错误高亮）、分项分、评语；教师可在此复核、修正识别文字、重新评分。
import { api, imageURL } from "../api.js";
import { $, $$, bar, demoNotice, esc, fmtDate, fmtScore, render, statusChip, SUBJECTS, toast } from "../ui.js";

const SEV = { minor: "轻微", major: "较重", critical: "严重" };
let errorTypes = null;

export default async function ({ params, user }) {
  const id = Number(params[0]);
  let timer = null;
  let stopped = false;

  async function load() {
    const d = await api(`/api/submissions/${id}`);
    if (stopped) return;  // 已离开本页（轮询中途跳转）
    if (d.status === "queued" || d.status === "processing") {
      renderProgress(d);
      if (!stopped) timer = setTimeout(load, 1500);
      return;
    }
    await renderResult(d, user);
  }
  await load();
  return () => { stopped = true; clearTimeout(timer); };
}

function renderProgress(d) {
  const order = ["uploaded", "preprocess", "ocr", "grading", "done"];
  const names = { uploaded: "已上传", preprocess: "图像预处理", ocr: "手写识别", grading: "按量规评分", done: "完成" };
  const cur = Math.max(0, order.indexOf(d.stage || "uploaded"));
  render(`
    <div class="card">
      <h1>${esc(d.assignment_title)}</h1>
      <p class="muted">${esc(d.student_name)} · 提交于 ${fmtDate(d.submitted_at, true)}</p>
      <div class="steps">${order.map((s, i) => `<span class="s ${i < cur ? "done" : i === cur ? "now" : ""}">${names[s]}</span>`).join("")}</div>
      <p class="muted small">正在处理，页面会自动刷新…</p>
    </div>`);
}

function highlight(text, errors) {
  const spans = errors.map((e, i) => ({ ...e, i })).filter(e => e.start != null && e.end != null && e.end > e.start)
    .sort((a, b) => a.start - b.start);
  let out = "", pos = 0;
  for (const e of spans) {
    if (e.start < pos) continue;  // 重叠的只高亮第一个
    out += esc(text.slice(pos, e.start));
    out += `<mark class="err ${esc(e.severity)}" data-i="${e.i}" title="${esc(e.message)}">${esc(text.slice(e.start, e.end))}</mark>`;
    pos = e.end;
  }
  return out + esc(text.slice(pos));
}

async function renderResult(d, user) {
  const r = d.final_result;
  const ai = d.ai_result;
  const isStaff = user.role === "teacher" || user.role === "admin";
  const reviewed = d.status === "reviewed";
  const errs = r?.errors || [];
  const typeLabel = t => (errorTypes?.error_types?.[t]?.label) || t;
  if (!errorTypes) { try { errorTypes = await api("/api/meta/error-types"); } catch { errorTypes = { error_types: {} }; } }

  const pages = d.pages || [];
  const warnings = [...(d.ocr_meta?.warnings || []), ...(d.ocr_meta?.notes || []), ...(r?.warnings || [])];
  render(`
    <div class="spread">
      <div><h1 style="margin-bottom:2px">${esc(d.assignment_title)}</h1>
        <div class="muted small">${esc(d.student_name)} · ${esc(SUBJECTS[d.subject] || d.subject)} · 提交于 ${fmtDate(d.submitted_at, true)}</div></div>
      <div class="row">${statusChip(d.status)}</div>
    </div>
    ${d.is_demo || (d.grader_provider || "").startsWith("mock") || (d.ocr_provider || "").startsWith("mock")
      ? demoNotice(d.is_demo && d.status === "reviewed" ? "本份的“教师复核”也是程序模拟的。" : "") : ""}
    ${d.status === "failed" ? `<div class="notice danger">${esc(d.error)}</div>` : ""}
    ${d.error && d.status !== "failed" ? `<div class="notice warn">${esc(d.error)}</div>` : ""}
    ${r ? `
    <div class="card">
      <div class="spread">
        <div class="scorebox"><span class="big">${fmtScore(r.total_score)}</span><span class="of">/ ${fmtScore(r.max_score)} 分</span>
          ${r.band ? `<span class="chip info">${esc(r.band)}</span>` : ""}</div>
        <div class="small muted">${reviewed ? "教师已复核" : "AI 初评，待老师复核"}${isStaff && ai && reviewed && ai.total_score !== r.total_score ? ` · AI 原分 ${fmtScore(ai.total_score)}` : ""}</div>
      </div>
      <div class="notice warn small">${esc(d.disclaimer)}</div>
      ${isStaff && r.needs_review && r.review_reasons?.length ? `<div class="notice danger small"><b>建议人工复核：</b><ul>${r.review_reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
      ${r.comment ? `<h3>评语</h3><p>${esc(r.comment)}</p>` : ""}
      ${r.strengths?.length && !r.comment.includes(r.strengths[0]) ? `<p class="small"><b>优点：</b>${r.strengths.map(esc).join("；")}</p>` : ""}
      ${r.suggestions?.length && !r.comment.includes(r.suggestions[0]) ? `<p class="small"><b>建议：</b>${r.suggestions.map(esc).join("；")}</p>` : ""}
      ${isStaff ? `<p class="small muted">识别：${esc(d.ocr_provider || "—")}${d.ocr_confidence != null ? `（置信度 ${d.ocr_confidence.toFixed(2)}）` : ""} · 评分：${esc(d.grader_provider || "—")} · 模型自评置信度 ${ai ? ai.confidence.toFixed(2) : "—"}</p>` : ""}
    </div>` : ""}
    <div class="grid result">
      <div>
        <div class="card">
          <div class="spread"><h2>作业图片</h2>
            <div class="seg" role="group" aria-label="图片版本"><button data-kind="processed" class="on">处理后</button><button data-kind="original">原图</button></div></div>
          ${pages.length > 1 ? `<div class="row small" style="margin:6px 0">${pages.map(p => `<button class="sm pg" data-p="${p.page_no}">第${p.page_no}页</button>`).join("")}${d.has_stitched ? '<button class="sm pg" data-p="stitched">拼接长图</button>' : ""}</div>` : ""}
          <div class="viewer"><img id="pageImg" alt="作业图片"></div>
          ${isStaff && d.preprocess_meta?.pages ? `<details class="small" style="margin-top:8px"><summary>预处理信息</summary>${d.preprocess_meta.pages.map((m, i) =>
            `<div>第${i + 1}页：${m.paper_detected ? "检测到纸张并透视矫正" : "未检测到纸张边缘"}；纠偏 ${m.deskew_angle}°；清晰度 ${m.blur_score}</div>`).join("")}</details>` : ""}
        </div>
      </div>
      <div>
        <div class="card">
          <div class="spread"><h2>识别文字</h2>${d.ocr_text_edited ? '<span class="chip info">教师已修正</span>' : ""}</div>
          ${warnings.length ? `<div class="notice warn small"><ul>${warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
          <div class="ocr-text" id="ocrText">${highlight(d.ocr_text || "", errs)}</div>
          <p class="small muted">高亮处为批注，点击查看说明。${isStaff && d.can_review ? "选中一段文字可快速添加批注。" : ""}</p>
        </div>
        ${r ? `
        <div class="card">
          <h2>分项得分</h2>
          ${r.criteria.map(c => `<div class="crit"><div class="spread"><span><b>${esc(c.name || c.id)}</b>${c.level ? ` <span class="chip">${esc(c.level)}</span>` : ""}</span>
            <span class="score">${fmtScore(c.score)} / ${fmtScore(c.max_score)}</span></div>${bar(c.score / c.max_score)}
            ${c.comment ? `<div class="small muted" style="margin-top:4px">${esc(c.comment)}</div>` : ""}</div>`).join("")}
          ${r.deductions?.length ? `<h3 style="margin-top:12px">扣分项</h3><ul class="small">${r.deductions.map(x => `<li>${esc(x.name || x.id)}：−${fmtScore(x.points)}（${esc(x.reason)}）</li>`).join("")}</ul>` : ""}
        </div>
        <div class="card">
          <h2>批注（${errs.length}）</h2>
          <ul class="list errlist">${errs.map((e, i) => `<li data-ei="${i}"><div class="spread"><span><span class="chip ${e.severity === "minor" ? "" : "danger"}">${esc(typeLabel(e.type))}</span>
            ${e.quote ? ` <span class="q">${esc(e.quote)}</span>` : ""}${e.suggestion ? ` → <b>${esc(e.suggestion)}</b>` : ""}</span>
            <span class="small muted">${esc(SEV[e.severity] || "")}${e.source === "teacher" ? " · 教师" : ""}</span></div>
            <div class="small">${esc(e.message)}</div></li>`).join("") || '<li class="empty">没有批注</li>'}</ul>
        </div>` : ""}
      </div>
    </div>
    ${d.can_review ? `<div id="reviewArea"></div>` : ""}`);

  // 图片查看
  let kind = "processed", page = pages[0]?.page_no || 1;
  async function showImg() {
    const img = $("#pageImg");
    if (!img || !pages.length) return;
    const path = page === "stitched" ? `/api/submissions/${d.id}/stitched` : `/api/submissions/${d.id}/pages/${page}/image?kind=${kind}`;
    try { img.src = await imageURL(path); } catch (e) { img.alt = "图片加载失败：" + e.message; }
  }
  $$(".seg button").forEach(b => b.onclick = () => { kind = b.dataset.kind; $$(".seg button").forEach(x => x.classList.toggle("on", x === b)); showImg(); });
  $$(".pg").forEach(b => b.onclick = () => { page = b.dataset.p === "stitched" ? "stitched" : Number(b.dataset.p); showImg(); });
  showImg();

  // 批注 ↔ 文本联动
  $$("#ocrText mark").forEach(m => m.onclick = () => {
    const li = document.querySelector(`[data-ei="${m.dataset.i}"]`);
    if (li) { li.scrollIntoView({ behavior: "smooth", block: "center" }); li.style.background = "var(--primary-soft)"; setTimeout(() => (li.style.background = ""), 1200); }
    toast(m.title);
  });
  $$("[data-ei]").forEach(li => li.onclick = () => {
    const m = document.querySelector(`#ocrText mark[data-i="${li.dataset.ei}"]`);
    if (m) { m.scrollIntoView({ behavior: "smooth", block: "center" }); m.classList.add("flash"); setTimeout(() => m.classList.remove("flash"), 1200); }
  });

  if (d.can_review) await renderReview(d, r, user);
}

async function renderReview(d, r, user) {
  const [rub, history, prov] = await Promise.all([
    api(`/api/rubrics/${encodeURIComponent(d.rubric_key)}`),
    api(`/api/submissions/${d.id}/reviews`),
    api("/api/meta/providers"),
  ]);
  const types = Object.entries(errorTypes.error_types);
  let errors = (r?.errors || []).map(e => ({ ...e, keep: true }));
  const dedDefs = rub.rubric.deductions || [];
  const area = $("#reviewArea");
  area.innerHTML = `
    <div class="grid two">
      <div class="card" id="rv">
        <h2>教师复核</h2>
        ${r ? `
        <p class="small muted">修改后保存，系统保留每次修改记录，用于评估 AI 与教师的一致性。</p>
        <div id="crits">${r.criteria.map(c => `<div class="review-crit"><label style="margin:0" for="c_${esc(c.id)}">${esc(c.name || c.id)}（满分 ${fmtScore(c.max_score)}）
          ${d.ai_result ? `<span class="small muted">AI：${fmtScore(d.ai_result.criteria.find(x => x.id === c.id)?.score)}</span>` : ""}</label>
          <input type="number" id="c_${esc(c.id)}" data-id="${esc(c.id)}" min="0" max="${c.max_score}" step="0.5" value="${c.score}" inputmode="decimal"></div>`).join("")}</div>
        ${dedDefs.length ? `<h3 style="margin-top:12px">扣分项</h3>${dedDefs.map(dd => {
          const cur = (r.deductions || []).find(x => x.id === dd.id);
          return `<div class="review-crit"><label style="margin:0" for="d_${esc(dd.id)}">${esc(dd.name)}<span class="small muted">（${esc(dd.rule)}${dd.max != null ? `，最多 ${dd.max}` : ""}）</span></label>
            <input type="number" id="d_${esc(dd.id)}" data-id="${esc(dd.id)}" min="0" ${dd.max != null ? `max="${dd.max}"` : ""} step="0.5" value="${cur ? cur.points : 0}" inputmode="decimal"></div>`;
        }).join("")}` : ""}
        <p class="spread" style="margin-top:10px"><b>复核后总分</b><span class="score" id="newTotal"></span></p>
        <h3>批注</h3>
        <ul class="list small" id="errEdit"></ul>
        <details><summary>添加批注</summary>
          <label>原文片段（可在识别文字中选中后自动填入）</label><input type="text" id="nq">
          <label>类型</label><select id="nt">${types.map(([k, v]) => `<option value="${esc(k)}">${esc(v.label)}</option>`).join("")}</select>
          <label>说明</label><input type="text" id="nm">
          <label>修改建议</label><input type="text" id="ns">
          <label>程度</label><select id="nv"><option value="minor">轻微</option><option value="major">较重</option><option value="critical">严重</option></select>
          <div style="height:8px"></div><button id="addErr" class="sm">加入批注</button>
        </details>
        <label for="cm">评语（家长和学生可见）</label><textarea id="cm">${esc(r.comment || "")}</textarea>
        <label for="note">修改理由（内部备注，可选）</label><input type="text" id="note">
        <div style="height:12px"></div>
        <button class="primary block" id="save">${d.status === "reviewed" ? "保存修改" : "确认并发布"}</button>` : `<div class="notice warn">还没有评分结果，可先重新识别/评分。</div>`}
      </div>
      <div>
        <div class="card">
          <h2>修正识别文字</h2>
          <p class="small muted">手写识别有误时在这里改正，保存后用当前评分模型重新评分（已复核的分数不会被覆盖）。</p>
          <textarea id="ocrEdit" style="min-height:180px">${esc(d.ocr_text || "")}</textarea>
          <div style="height:8px"></div><button id="saveOcr">保存文字并重新评分</button>
        </div>
        <div class="card">
          <h2>重新识别 / 评分</h2>
          <label>手写识别</label><select id="rOcr"><option value="">默认（${esc(prov.ocr_default)}）</option>${prov.ocr.filter(p => p.available).map(p => `<option value="${p.name}">${esc(p.label)}</option>`).join("")}</select>
          <label>评分</label><select id="rGr"><option value="">默认（${esc(prov.grader_default)}）</option>${prov.grader.filter(p => p.available).map(p => `<option value="${p.name}">${esc(p.label)}</option>`).join("")}</select>
          <label class="check"><input type="checkbox" id="rOnly"> 只重新评分（不重新识别）</label>
          <button id="retry">重新处理</button>
        </div>
        <div class="card">
          <h2>复核记录（${history.length}）</h2>
          <ul class="list small">${history.map(h => `<li class="item" style="display:block"><div class="spread"><b>${esc(h.reviewer_name)}</b><span class="muted">${fmtDate(h.created_at, true)}</span></div>
            <div>AI ${fmtScore(h.ai_total)} → 教师 ${fmtScore(h.teacher_total)} / ${fmtScore(h.max_score)}（${esc(h.ai_band || "—")} → ${esc(h.teacher_band)}）</div>
            ${h.note ? `<div class="muted">${esc(h.note)}</div>` : ""}</li>`).join("") || '<li class="empty">暂无</li>'}</ul>
        </div>
      </div>
    </div>`;
  if (!r) { bindRetry(d); return; }

  const drawErr = () => {
    $("#errEdit").innerHTML = errors.map((e, i) => `<li class="item"><label class="check" style="margin:0"><input type="checkbox" data-k="${i}" ${e.keep ? "checked" : ""}>
      <span><span class="chip">${esc(errorTypes.error_types[e.type]?.label || e.type)}</span> ${e.quote ? `<span class="q">${esc(e.quote)}</span>` : ""} ${esc(e.message)}</span></label></li>`).join("") || '<li class="empty">无</li>';
    $$("#errEdit [data-k]").forEach(c => c.onchange = () => { errors[+c.dataset.k].keep = c.checked; });
  };
  drawErr();
  const total = () => {
    const s = $$("#crits input").reduce((a, i) => a + (Number(i.value) || 0), 0);
    const dd = dedDefs.reduce((a, x) => a + (Number($(`#d_${CSS.escape(x.id)}`)?.value) || 0), 0);
    const t = Math.max(0, Math.min(r.max_score, s - dd));
    $("#newTotal").textContent = `${fmtScore(t)} / ${fmtScore(r.max_score)}`;
  };
  $$("#rv input[type=number]").forEach(i => i.oninput = total);
  total();

  document.getElementById("ocrText").addEventListener("mouseup", () => {
    const s = String(window.getSelection() || "").trim();
    if (s && s.length < 80) { $("#nq").value = s; $("#rv details").open = true; }
  });
  $("#addErr").onclick = () => {
    if (!$("#nm").value.trim()) { toast("请填写说明", true); return; }
    errors.push({ type: $("#nt").value, quote: $("#nq").value.trim(), message: $("#nm").value.trim(), suggestion: $("#ns").value.trim(),
      severity: $("#nv").value, source: "teacher", keep: true, start: null, end: null });
    ["#nq", "#nm", "#ns"].forEach(s => ($(s).value = ""));
    drawErr();
  };
  $("#save").onclick = async () => {
    const criteria = $$("#crits input").map(i => ({ id: i.dataset.id, score: Number(i.value) || 0 }));
    for (const i of $$("#crits input")) if (Number(i.value) > Number(i.max)) { toast(`分数不能超过满分 ${i.max}`, true); return; }
    const deductions = dedDefs.map(x => ({ id: x.id, name: x.name, points: Number($(`#d_${CSS.escape(x.id)}`).value) || 0, reason: "教师复核" }))
      .filter(x => x.points > 0).map(x => ({ ...x, reason: (r.deductions || []).find(y => y.id === x.id)?.reason || "教师复核" }));
    const errs = errors.filter(e => e.keep).map(({ keep, ...e }) => e);
    try {
      await api(`/api/submissions/${d.id}/review`, { method: "POST", body: { criteria, deductions, errors: errs, comment: $("#cm").value, note: $("#note").value } });
      toast("已保存复核结果");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (e) { toast(e.message, true); }
  };
  $("#saveOcr").onclick = async () => {
    try {
      await api(`/api/submissions/${d.id}/ocr-text`, { method: "PUT", body: { text: $("#ocrEdit").value, regrade: true } });
      toast("已保存并重新评分");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (e) { toast(e.message, true); }
  };
  bindRetry(d);
  void user;
}

function bindRetry(d) {
  $("#retry").onclick = async () => {
    try {
      await api(`/api/submissions/${d.id}/retry`, { method: "POST", body: { ocr_provider: $("#rOcr").value || null, grader_provider: $("#rGr").value || null, regrade_only: $("#rOnly").checked } });
      toast("已提交重新处理");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (e) { toast(e.message, true); }
  };
}
