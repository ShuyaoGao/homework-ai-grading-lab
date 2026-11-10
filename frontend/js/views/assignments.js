// 作业列表与布置作业（教师）
import { api } from "../api.js";
import { $, esc, fmtDate, render, SUBJECTS, toast } from "../ui.js";

const REF_TEMPLATES = {
  essay_en: { key_points: [["party", "festival"], ["friday", "time"], ["hall", "place"]], target_words: [80, 100] },
  essay_zh: { keywords: ["关键词1", "关键词2"], min_chars: 600 },
  short_answer: { questions: [{ id: 1, question: "解方程 2x+3=11", answer: "x=4", points: 3, knowledge_point: "一元一次方程", steps: ["2x=8"] }] },
  paper: { min_chars: 1200 },
  generic: {},
};

export default async function ({ user }) {
  const [list, classes, rubrics] = await Promise.all([api("/api/assignments"), api("/api/classes"), api("/api/rubrics")]);
  const isTeacher = user.role === "teacher" || user.role === "admin";
  render(`
    <h1>作业</h1>
    ${isTeacher ? `
    <details class="card" ${list.length ? "" : "open"}><summary><b>布置新作业</b></summary>
      <form id="f">
        <label>班级</label><select name="class_id">${classes.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>
        <label>标题</label><input type="text" name="title" required maxlength="128" placeholder="如：英语书面表达：My Weekend">
        <label>学科</label><select name="subject">${Object.entries(SUBJECTS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
        <label>评分量规</label><select name="rubric_key" id="rk">${rubrics.map(r => `<option value="${esc(r.key)}" data-kind="${esc(r.kind)}">${esc(r.name)}${r.builtin ? "" : "（自定义）"}</option>`).join("")}</select>
        <label>题目 / 写作要求（会提供给评分模型）</label><textarea name="prompt" style="min-height:90px"></textarea>
        <label>参考答案 / 要点（JSON，按量规类型自动给出模板）</label><textarea name="reference" id="ref" class="code" style="min-height:160px"></textarea>
        <p class="small muted">英语作文：key_points 每项是一组同义关键词（命中任一即覆盖该要点）；语文：min_chars 字数要求；简答/数学：questions 逐题填写 answer、points、knowledge_point、steps（给分要点）。</p>
        <label>截止日期</label><input type="date" name="due_date">
        <div style="height:10px"></div><button class="primary">布置</button>
      </form>
    </details>` : ""}
    <div class="card"><ul class="list">${list.map(a => `<li><div class="item"><div>
      <div class="title">${esc(a.title)}</div>
      <div class="sub">${esc(SUBJECTS[a.subject] || a.subject)} · ${esc(a.rubric_name)} · 满分 ${a.max_score ?? "—"} · 布置于 ${fmtDate(a.created_at)}</div></div>
      <div class="row"><a class="btn sm" href="#/upload?assignment=${a.id}">上传</a>${isTeacher ? `<a class="btn sm" href="#/class/${a.class_id}">看板</a>` : ""}</div></div></li>`).join("") || '<li class="empty">暂无作业</li>'}</ul></div>`);
  if (!isTeacher) return;
  const setTpl = () => {
    const kind = $("#rk").selectedOptions[0]?.dataset.kind || "generic";
    $("#ref").value = JSON.stringify(REF_TEMPLATES[kind] || {}, null, 2);
  };
  $("#rk").onchange = setTpl;
  setTpl();
  $("#f").onsubmit = async e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    let reference;
    try { reference = JSON.parse(fd.reference || "{}"); } catch { toast("参考答案不是合法的 JSON", true); return; }
    const body = { class_id: Number(fd.class_id), title: fd.title, subject: fd.subject, rubric_key: fd.rubric_key, prompt: fd.prompt, reference,
      due_date: fd.due_date ? `${fd.due_date}T23:59:00` : null };
    try { await api("/api/assignments", { method: "POST", body }); toast("已布置"); window.dispatchEvent(new HashChangeEvent("hashchange")); }
    catch (err) { toast(err.message, true); }
  };
}
