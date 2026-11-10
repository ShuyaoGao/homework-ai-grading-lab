// 通用 UI 小工具
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

let toastTimer;
export function toast(msg, isError = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, isError ? 5000 : 2500);
}

export function fmtScore(v, digits = 1) {
  if (v == null || Number.isNaN(v)) return "—";
  const n = Number(v);
  return Number.isInteger(n) ? String(n) : n.toFixed(digits).replace(/\.0$/, "");
}
export const pct = v => (v == null ? "—" : `${Math.round(v * 100)}%`);
export function fmtDate(s, withTime = false) {
  if (!s) return "—";
  const d = new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
  const p = n => String(n).padStart(2, "0");
  const base = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return withTime ? `${base} ${p(d.getHours())}:${p(d.getMinutes())}` : base;
}

export const SUBJECTS = { english: "英语", chinese: "语文", math: "数学", paper: "论文", other: "其他" };
export const STATUS = {
  queued: ["排队中", "chip"], processing: ["识别评分中", "chip info"], ai_done: ["AI 已评 · 待复核", "chip warn"],
  reviewed: ["教师已复核", "chip ok"], failed: ["处理失败", "chip danger"],
};
export const statusChip = s => { const [t, c] = STATUS[s] || [s, "chip"]; return `<span class="${c}">${esc(t)}</span>`; };
export const ROLE_LABEL = { admin: "管理员", teacher: "教师", parent: "家长", student: "学生" };

export function barClass(ratio) { return ratio < 0.6 ? "bar lv-low" : ratio < 0.8 ? "bar lv-mid" : "bar"; }
export function bar(ratio) {
  const r = Math.max(0, Math.min(1, ratio || 0));
  return `<div class="${barClass(r)}" role="progressbar" aria-valuenow="${Math.round(r * 100)}" aria-valuemin="0" aria-valuemax="100"><span style="width:${(r * 100).toFixed(1)}%"></span></div>`;
}

export function render(html) {
  const app = document.getElementById("app");
  app.innerHTML = html;
  return app;
}

export const DEMO_NOTE = "演示数据，非真实模型输出：作业图片是程序合成的仿手写图；“识别”读取的是图片里内嵌的标准文本，评分是离线规则模拟（mock）。";
export function demoNotice(extra = "") {
  return `<div class="notice demo" role="note"><b>演示数据</b> · ${esc(DEMO_NOTE.replace(/^演示数据，/, ""))}${extra ? " " + esc(extra) : ""}</div>`;
}

export function errorBox(e) {
  return `<div class="card"><div class="notice danger">${esc(e.message || e)}</div><a class="btn" href="#/">返回首页</a></div>`;
}

export function submissionItem(s, { showStudent = true } = {}) {
  const score = s.final_total != null ? `${fmtScore(s.final_total)}<span class="muted small"> / ${fmtScore(s.max_score)}</span>` : "";
  return `<li><a class="item" href="#/submission/${s.id}">
    <div><div class="title">${esc(s.assignment_title)}</div>
      <div class="sub">${showStudent ? esc(s.student_name) + " · " : ""}${esc(SUBJECTS[s.subject] || s.subject)} · ${fmtDate(s.submitted_at, true)}</div>
      <div class="row" style="margin-top:4px">${statusChip(s.status)}${s.is_demo ? '<span class="chip demo">演示数据</span>' : ""}${s.band ? `<span class="chip">${esc(s.band)}</span>` : ""}${s.needs_review && s.status === "ai_done" ? '<span class="chip danger">建议复核</span>' : ""}</div>
    </div>
    <div class="score">${score}</div></a></li>`;
}

// 客户端压缩：大 JPEG 照片缩到长边 2400px（同时去掉 EXIF/GPS），PNG 原样上传
export async function maybeCompress(file, maxSide = 2400, maxBytes = 1.5 * 1024 * 1024) {
  // JPEG/WebP/HEIC 超过 1.5 MB 就压；PNG 只有超过 8 MB 才压（演示图 PNG 里嵌了标准文本，压缩会丢失）
  const lossy = /^image\/(jpeg|webp|heic|heif)$/i.test(file.type);
  if (lossy ? file.size < maxBytes : !(file.type === "image/png" && file.size > 8 * 1024 * 1024)) return file;
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const w = Math.round(bmp.width * scale), h = Math.round(bmp.height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    canvas.getContext("2d").drawImage(bmp, 0, 0, w, h);
    const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", 0.9));
    if (!blob) return file;
    return new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
  } catch {
    return file;  // 浏览器不支持（如 HEIC）就原样上传，由服务器处理
  }
}
