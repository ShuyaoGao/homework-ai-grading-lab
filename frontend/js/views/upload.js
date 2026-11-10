// 上传作业：选学生/作业 → 手机拍照（capture=environment）或从相册选 → 多页排序 → 上传（带进度）
import { api, randomToken, uploadWithRetry } from "../api.js";
import { editLabel, openEditor } from "../editor.js";
import { $, esc, maybeCompress, render, SUBJECTS, toast } from "../ui.js";

export default async function ({ query, user }) {
  const students = await api("/api/students");
  if (!students.length) { render(`<div class="card empty">没有可上传作业的学生。</div>`); return; }
  const assignments = await api("/api/assignments");
  let providers = null;
  if (user.role === "teacher" || user.role === "admin") providers = await api("/api/meta/providers");

  const pre = Number(query.get("student")) || students[0].id;
  const files = [];  // {file, url, edit}
  let token = null;  // 同一批照片重试时复用同一个令牌（服务器据此去重）

  render(`
    <h1>上传作业</h1>
    <div class="card">
      <label for="stu">学生</label>
      <select id="stu">${students.map(s => `<option value="${s.id}" ${s.id === pre ? "selected" : ""}>${esc(s.name)}${s.student_no ? "（" + esc(s.student_no) + "）" : ""}</option>`).join("")}</select>
      <div id="consentBox"></div>
      <label for="asg">作业</label>
      <select id="asg"></select>
      <p id="asgInfo" class="small muted"></p>
    </div>
    <div class="card">
      <h2>拍照</h2>
      <div class="dropzone" id="drop">
        <div class="muted small">把作业平铺在桌面上，光线充足、手机正对纸面，尽量让整张纸都在画面内。多页作业按顺序逐页拍。</div>
        <div class="big-btns">
          <label class="btn primary" style="margin:0;color:var(--primary-ink)">拍照
            <input id="cam" type="file" accept="image/*" capture="environment" hidden>
          </label>
          <label class="btn" style="margin:0;color:var(--text)">从相册选择
            <input id="gal" type="file" accept="image/*" multiple hidden>
          </label>
        </div>
      </div>
      <div class="thumbs" id="thumbs"></div>
      <p class="small muted">点缩略图上的 ✎ 可以裁剪、旋转。拍照后会自动打开调整界面。</p>
      ${providers ? `
      <details style="margin-top:12px"><summary>高级：指定识别/评分模型</summary>
        <label>手写识别</label><select id="ocrp"><option value="">默认（${esc(providers.ocr_default)}）</option>${providers.ocr.filter(p => p.available).map(p => `<option value="${p.name}">${esc(p.label)}</option>`).join("")}</select>
        <label>评分</label><select id="grdp"><option value="">默认（${esc(providers.grader_default)}）</option>${providers.grader.filter(p => p.available).map(p => `<option value="${p.name}">${esc(p.label)}</option>`).join("")}</select>
        <p class="small muted">云端模型只在监护人勾选了“允许云端 AI 处理”时使用，否则自动改用本地模型。</p>
      </details>` : ""}
      <div class="progress" id="prog" hidden><span></span></div>
      <div class="upload-status" id="upStatus" role="status" aria-live="polite"></div>
      <button class="primary block" id="go" disabled>上传并开始批改</button>
      <p class="small muted" style="margin-top:8px">照片仅用于本次作业批改。上传前会去除照片中的位置等元数据。AI 评分仅供参考，以老师复核为准。</p>
    </div>`);

  const stuSel = $("#stu"), asgSel = $("#asg");

  function refreshAssignments() {
    const st = students.find(s => s.id === Number(stuSel.value));
    const list = assignments.filter(a => a.class_id === st.class_id);
    asgSel.innerHTML = list.map(a => `<option value="${a.id}">${esc(a.title)}</option>`).join("") || `<option value="">（该班级暂无作业）</option>`;
    const pa = Number(query.get("assignment"));
    if (pa && list.some(a => a.id === pa)) asgSel.value = String(pa);
    showAsg();
    const box = $("#consentBox");
    if (st.is_minor_under_14 && !st.consents.basic) {
      box.innerHTML = user.role === "parent"
        ? `<div class="notice warn">孩子未满 14 周岁，需要您作为监护人先同意《个人信息处理说明》。<br><a class="btn sm" href="#/privacy">去阅读并同意</a></div>`
        : `<div class="notice danger">该学生的监护人尚未签署同意，暂不能上传（请通知家长在“授权隐私”页面完成授权）。</div>`;
    } else {
      box.innerHTML = st.consents.cloud_ai ? `<p class="small muted">监护人已同意云端 AI 处理。</p>` : `<p class="small muted">监护人未开启云端 AI 处理：将只使用本地模型。</p>`;
    }
    updateBtn();
  }
  function showAsg() {
    const a = assignments.find(x => x.id === Number(asgSel.value));
    $("#asgInfo").innerHTML = a ? `${esc(SUBJECTS[a.subject] || a.subject)} · ${esc(a.rubric_name)} · 满分 ${a.max_score ?? "—"}<br>${esc(a.prompt)}` : "";
  }
  function blocked() {
    const st = students.find(s => s.id === Number(stuSel.value));
    return st.is_minor_under_14 && !st.consents.basic;
  }
  function updateBtn() { $("#go").disabled = !files.length || !asgSel.value || blocked(); }

  function drawThumbs() {
    $("#thumbs").innerHTML = files.map((f, i) => `
      <div class="thumb"><img src="${f.url}" alt="第 ${i + 1} 页" class="${f.edit?.rotate ? "rot" + f.edit.rotate : ""}">
        ${editLabel(f.edit) ? `<span class="edit-tag">${editLabel(f.edit)}</span>` : ""}
        <div class="bar2"><span>第${i + 1}页</span><span>
          <button data-ed="${i}" aria-label="裁剪或旋转">✎</button>
          <button data-up="${i}" aria-label="上移" ${i === 0 ? "disabled" : ""}>↑</button>
          <button data-del="${i}" aria-label="删除">✕</button></span></div></div>`).join("");
    $("#thumbs").querySelectorAll("[data-ed]").forEach(b => b.onclick = async () => {
      const f = files[+b.dataset.ed];
      const e = await openEditor(f.url, f.edit);
      if (e) { f.edit = e; drawThumbs(); }
    });
    $("#thumbs").querySelectorAll("[data-del]").forEach(b => b.onclick = () => { URL.revokeObjectURL(files[+b.dataset.del].url); files.splice(+b.dataset.del, 1); drawThumbs(); });
    $("#thumbs").querySelectorAll("[data-up]").forEach(b => b.onclick = () => { const i = +b.dataset.up; [files[i - 1], files[i]] = [files[i], files[i - 1]]; drawThumbs(); });
    updateBtn();
    token = null;  // 照片有变化 → 视为新的一次上传
  }
  async function addFiles(list) {
    for (const f of list) {
      if (!f.type.startsWith("image/") && !/\.(heic|heif)$/i.test(f.name)) { toast(`${f.name} 不是图片`, true); continue; }
      const c = await maybeCompress(f);
      if (c !== f) toast(`已压缩：${(f.size / 1048576).toFixed(1)} MB → ${(c.size / 1048576).toFixed(1)} MB`);
      files.push({ file: c, url: URL.createObjectURL(c), edit: null });
    }
    drawThumbs();
    // 刚拍完一张：直接打开裁剪/旋转（可以点“完成”跳过）
    if (list.length === 1 && files.length && fromCamera) {
      fromCamera = false;
      const f = files[files.length - 1];
      const e = await openEditor(f.url, null);
      if (e) { f.edit = e; drawThumbs(); }
    }
  }
  let fromCamera = false;
  $("#cam").onchange = e => { fromCamera = true; addFiles(e.target.files); e.target.value = ""; };
  $("#gal").onchange = e => { addFiles(e.target.files); e.target.value = ""; };
  const drop = $("#drop");
  drop.ondragover = e => { e.preventDefault(); };
  drop.ondrop = e => { e.preventDefault(); addFiles(e.dataTransfer.files); };
  stuSel.onchange = refreshAssignments;
  asgSel.onchange = () => { showAsg(); updateBtn(); };
  refreshAssignments();

  $("#go").onclick = async () => {
    token = token || randomToken();
    const makeForm = () => {
      const fd = new FormData();
      fd.append("assignment_id", asgSel.value);
      fd.append("student_id", stuSel.value);
      fd.append("client_token", token);
      files.forEach((f, i) => fd.append("files", f.file, f.file.name || `page${i + 1}.jpg`));
      if (files.some(f => f.edit)) fd.append("edits", JSON.stringify(files.map(f => f.edit)));
      if ($("#ocrp")?.value) fd.append("ocr_provider", $("#ocrp").value);
      if ($("#grdp")?.value) fd.append("grader_provider", $("#grdp").value);
      return fd;
    };
    const btn = $("#go"), prog = $("#prog"), st = $("#upStatus");
    btn.disabled = true; btn.textContent = "上传中…"; prog.hidden = false; st.textContent = "";
    try {
      const sub = await uploadWithRetry("/api/submissions", makeForm, {
        onProgress: p => { prog.firstElementChild.style.width = `${Math.round(p * 100)}%`; },
        onStatus: m => { st.textContent = m; },
      });
      toast("上传成功，正在识别和评分");
      files.forEach(f => URL.revokeObjectURL(f.url));
      location.hash = `#/submission/${sub.id}`;
    } catch (e) {
      toast(e.message, true);
      st.textContent = `上传没有成功：${e.message}。照片还在，网络恢复后点按钮重试即可。`;
      btn.disabled = false; btn.textContent = "重试上传";
    }
  };
  return () => files.forEach(f => URL.revokeObjectURL(f.url));
}
