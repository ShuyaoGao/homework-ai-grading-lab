import { api } from "../api.js";
import { $, esc, fmtDate, render, submissionItem, toast, ROLE_LABEL } from "../ui.js";

export default async function ({ user }) {
  if (user.role === "teacher") return teacherHome(user);
  if (user.role === "parent") return parentHome(user);
  if (user.role === "student") {
    if (!user.student_id) { render(`<div class="card">账号未关联学生档案，请联系老师。</div>`); return; }
    const mod = await import("./student.js");
    return mod.default({ params: [String(user.student_id)], query: new URLSearchParams(), user });
  }
  return adminHome(user);
}

async function teacherHome(user) {
  const [classes, pending, recent] = await Promise.all([
    api("/api/classes"), api("/api/submissions?status=ai_done&limit=500"), api("/api/submissions?limit=8"),
  ]);
  const studentsByClass = await Promise.all(classes.map(c => api(`/api/classes/${c.id}/students`)));
  const urgent = pending.filter(s => s.needs_review).length;
  render(`
    <h1>${esc(user.display_name)}，您好</h1>
    <div class="stats">
      <div class="stat"><div class="v">${classes.length}</div><div class="k">任教班级</div></div>
      <div class="stat"><div class="v">${pending.length}</div><div class="k">待复核</div></div>
      <div class="stat"><div class="v">${urgent}</div><div class="k">AI 建议重点复核</div></div>
      <div class="stat"><div class="v">${studentsByClass.reduce((a, s) => a + s.length, 0)}</div><div class="k">学生</div></div>
    </div>
    <div class="grid two">
      <div>
        ${classes.map((c, i) => `
          <div class="card">
            <div class="spread"><h2>${esc(c.name)}</h2><a class="btn sm primary" href="#/class/${c.id}">班级看板</a></div>
            <p class="muted small">${esc(c.grade)} · ${esc(c.school_year)} · ${c.student_count} 名学生</p>
            <ul class="list">${studentsByClass[i].map(s => `
              <li><a class="item" href="#/student/${s.id}"><div><div class="title">${esc(s.name)}</div>
              <div class="sub">学号 ${esc(s.student_no)}</div></div>
              <div class="row">${s.consents.basic ? '<span class="chip ok">已授权</span>' : '<span class="chip danger">未授权</span>'}${s.consents.cloud_ai ? '<span class="chip info">可用云端</span>' : ""}</div></a></li>`).join("")}
            </ul>
          </div>`).join("") || `<div class="card empty">还没有班级</div>`}
      </div>
      <div>
        <div class="card">
          <div class="spread"><h2>待复核</h2><a href="#/submissions?status=ai_done">全部 ›</a></div>
          <ul class="list">${pending.slice(0, 8).map(s => submissionItem(s)).join("") || '<li class="empty">没有待复核的作业</li>'}</ul>
        </div>
        <div class="card">
          <h2>最近提交</h2>
          <ul class="list">${recent.map(s => submissionItem(s)).join("") || '<li class="empty">暂无</li>'}</ul>
        </div>
      </div>
    </div>`);
}

async function parentHome(user) {
  const [kids, subs] = await Promise.all([api("/api/students"), api("/api/submissions?limit=20")]);
  render(`
    <h1>${esc(user.display_name)}，您好</h1>
    ${kids.length ? "" : `<div class="card empty">账号还没有绑定孩子，请联系班主任。</div>`}
    <div class="grid two">
      ${kids.map(k => `
        <div class="card">
          <div class="spread"><h2>${esc(k.name)}</h2>
            <span>${k.consents.basic ? '<span class="chip ok">已签署同意</span>' : '<span class="chip danger">未签署同意</span>'}</span></div>
          ${k.consents.basic ? "" : `<div class="notice warn">孩子未满 14 周岁，上传作业前需要您先阅读并同意<a href="#/privacy">《个人信息处理说明》</a>。</div>`}
          <div class="row">
            <a class="btn primary" href="#/upload?student=${k.id}">拍照上传作业</a>
            <a class="btn" href="#/student/${k.id}">学习画像</a>
          </div>
        </div>`).join("")}
    </div>
    <div class="card">
      <h2>最近的作业</h2>
      <ul class="list">${subs.map(s => submissionItem(s)).join("") || '<li class="empty">还没有作业记录</li>'}</ul>
    </div>`);
}

async function adminHome(user) {
  const [prov, users, audit] = await Promise.all([api("/api/meta/providers"), api("/api/users"), api("/api/admin/audit?limit=30")]);
  const provRows = (list, def) => list.map(p => `<tr><td>${esc(p.name)}${p.name === def ? ' <span class="chip info">当前默认</span>' : ""}</td>
    <td>${esc(p.label)}</td><td>${p.available ? '<span class="chip ok">可用</span>' : `<span class="chip">不可用</span> <span class="muted small">${esc(p.reason)}</span>`}</td>
    <td>${p.is_cloud ? '<span class="chip warn">云端</span>' : '<span class="chip ok">本地</span>'}</td><td class="mono small">${esc(p.model)}</td></tr>`).join("");
  render(`
    <h1>系统管理</h1>
    <div class="card">
      <h2>模型 provider</h2>
      <p class="small muted">API key 只从服务器环境变量读取，这里只显示是否存在。云端模型：${prov.allow_cloud ? "允许（仍需监护人单独同意）" : "已关闭"}。</p>
      <p class="small">${Object.entries(prov.keys_present).map(([k, v]) => `<span class="chip ${v ? "ok" : ""}">${esc(k)}：${v ? "已设置" : "未设置"}</span>`).join(" ")}</p>
      <h3>手写识别</h3>
      <div class="table-wrap"><table><thead><tr><th>名称</th><th>说明</th><th>状态</th><th>部署</th><th>模型</th></tr></thead><tbody>${provRows(prov.ocr, prov.ocr_default)}</tbody></table></div>
      <h3 style="margin-top:16px">评分</h3>
      <div class="table-wrap"><table><thead><tr><th>名称</th><th>说明</th><th>状态</th><th>部署</th><th>模型</th></tr></thead><tbody>${provRows(prov.grader, prov.grader_default)}</tbody></table></div>
    </div>
    <div class="grid two">
      <div class="card">
        <h2>账号</h2>
        <div class="table-wrap"><table><thead><tr><th>用户名</th><th>姓名</th><th>角色</th><th>状态</th></tr></thead><tbody>
        ${users.map(u => `<tr><td class="mono">${esc(u.username)}</td><td>${esc(u.display_name)}</td><td>${esc(ROLE_LABEL[u.role])}</td><td>${u.is_active ? "启用" : "停用"}</td></tr>`).join("")}
        </tbody></table></div>
        <details style="margin-top:12px"><summary>新建账号</summary>
          <form id="nu">
            <label>用户名</label><input type="text" name="username" required pattern="[A-Za-z0-9_.\\-]{3,64}">
            <label>姓名</label><input type="text" name="display_name" required>
            <label>初始密码（≥8 位）</label><input type="password" name="password" required minlength="8">
            <label>角色</label><select name="role"><option value="teacher">教师</option><option value="parent">家长</option><option value="admin">管理员</option></select>
            <div style="height:10px"></div><button class="primary">创建</button>
          </form>
        </details>
      </div>
      <div class="card">
        <h2>数据保留</h2>
        <p class="small muted">按保留期限清理作业原图（成绩与文字保留）。先预览再执行。</p>
        <div class="row"><button id="dry">预览清理范围</button><button class="danger" id="purge">执行清理</button></div>
        <pre id="purgeOut" class="small mono" style="white-space:pre-wrap"></pre>
        <h2 style="margin-top:16px">审计日志（最近 30 条）</h2>
        <div class="table-wrap"><table><thead><tr><th>时间</th><th>用户</th><th>操作</th><th>对象</th></tr></thead><tbody>
          ${audit.map(a => `<tr><td class="small">${fmtDate(a.created_at, true)}</td><td>${a.user_id ?? "—"}</td><td>${esc(a.action)}</td><td class="small mono">${esc(a.target)}</td></tr>`).join("")}
        </tbody></table></div>
      </div>
    </div>`);
  $("#nu").onsubmit = async e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    try { await api("/api/users", { method: "POST", body: fd }); toast("已创建"); location.reload(); } catch (err) { toast(err.message, true); }
  };
  const purge = async dry => {
    try {
      const r = await api(`/api/admin/retention/purge?dry_run=${dry}`, { method: "POST" });
      $("#purgeOut").textContent = JSON.stringify(r, null, 2);
    } catch (err) { toast(err.message, true); }
  };
  $("#dry").onclick = () => purge(true);
  $("#purge").onclick = () => { if (confirm("确定删除超过保留期限的作业图片？此操作不可恢复。")) purge(false); };
}
