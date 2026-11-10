import { login, STATIC_DEMO } from "../api.js";
import { $, esc, render, toast } from "../ui.js";

const DEMO = [["teacher_wang", "teacher12345", "教师"], ["parent_zhang", "parent12345", "家长"],
  ["student_zxm", "student12345", "学生"], ["admin", "admin12345", "管理员"]];

export default async function () {
  render(`
  <div class="login">
    <div class="card">
      <h1>登录作业智评</h1>
      <p class="muted small">老师批改减负，家长随手拍照上传，学生看到自己的成长画像。</p>
      <form id="f" autocomplete="on">
        <label for="u">用户名</label>
        <input id="u" name="username" type="text" autocomplete="username" required autocapitalize="off">
        <label for="p">密码</label>
        <input id="p" name="password" type="password" autocomplete="current-password" required>
        <div style="height:12px"></div>
        <button class="primary block" type="submit">登录</button>
      </form>
    </div>
    <div class="card demo-accounts">
      <h3>演示账号</h3>
      <p class="muted small">${STATIC_DEMO ? "网页版静态演示：点下面任一账号即可进入（数据为程序合成）。" : "仅用于本机演示，正式部署前请在管理端修改或删除。"}</p>
      ${DEMO.map(([u, p, r]) => `<button class="sm" data-u="${esc(u)}" data-p="${esc(p)}">${esc(r)}：${esc(u)}</button>`).join("")}
    </div>
    <p class="muted small" style="text-align:center"><a href="#/privacy">隐私说明</a>${STATIC_DEMO ? "" : ` · <a href="/api/docs" target="_blank" rel="noopener">API 文档</a>`}</p>
  </div>`);
  const f = $("#f");
  f.onsubmit = async e => {
    e.preventDefault();
    const btn = f.querySelector("button");
    btn.disabled = true;
    try {
      await login($("#u").value.trim(), $("#p").value);
      location.hash = "#/";
    } catch (err) {
      toast(err.message, true);
    } finally { btn.disabled = false; }
  };
  document.querySelectorAll(".demo-accounts button").forEach(b => {
    b.onclick = () => { $("#u").value = b.dataset.u; $("#p").value = b.dataset.p; f.requestSubmit(); };
  });
}
