// 本机演示（run.py --demo）的公共小工具：演示账号登录、关闭演示服务。
import { auth, ApiError, STATIC_DEMO } from "./api.js";
import { render } from "./ui.js";

// 与登录页“演示账号”按钮相同（README 中公开的本机演示账号，部署前必须修改或删除）
export const DEMO_ACCOUNTS = {
  teacher: ["teacher_wang", "teacher12345"],
  parent: ["parent_zhang", "parent12345"],
};

let healthCache = null;
export async function demoHealth() {
  if (!healthCache) healthCache = fetch("/api/health").then(r => r.json()).catch(() => ({}));
  return healthCache;
}

// 只拿 token，不改变当前页面的登录身份（自动演示里家长和老师轮流操作）
export async function tokenFor(role) {
  const [username, password] = DEMO_ACCOUNTS[role];
  const res = await fetch("/api/auth/login", { method: "POST", body: new URLSearchParams({ username, password }) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.detail || `演示账号 ${username} 登录失败`);
  return data;
}

// 用指定 token 调接口（不触发 api() 的 401 跳转）
export async function callAs(token, path, { method = "GET", body, form, raw } = {}) {
  const headers = { Authorization: `Bearer ${token}` };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, { method, headers, body: payload }); } catch { throw new ApiError(0, "连不上演示服务，可能已经关闭"); }
  if (raw) { if (!res.ok) throw new ApiError(res.status, `请求失败（${res.status}）`); return res; }
  const data = (res.headers.get("content-type") || "").includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, (data && typeof data.detail === "string") ? data.detail : `请求失败（${res.status}）`);
  return data;
}

// 进入演示时以教师身份自动登录（只在演示服务上生效）
export async function ensureTeacher() {
  if (auth.token && auth.user?.role === "teacher") return false;
  const d = await tokenFor("teacher");
  auth.set(d.access_token, d.user);
  return true;  // 身份变了，需要重画导航
}

export async function shutdownDemo({ ask = true } = {}) {
  if (STATIC_DEMO) return stopStatic(ask);
  if (ask && !window.confirm("关闭演示服务？\n关闭后这个页面就不能再用了，下次双击“启动演示.bat”重新打开。")) return false;
  try {
    const r = await fetch("/api/demo/shutdown", { method: "POST", headers: { "X-Demo-Action": "shutdown" } });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
  } catch (e) {
    window.alert("关闭失败：" + e.message + "\n可以双击“停止演示.bat”，或直接关掉黑色的服务窗口。");
    return false;
  }
  document.getElementById("topnav").innerHTML = "";
  document.getElementById("tabbar").innerHTML = "";
  document.getElementById("usermenu").innerHTML = "";
  render(`<div class="card shutdown-page"><h1>演示服务已关闭</h1>
    <p>后台服务已经退出，没有遗留进程。现在可以关闭这个浏览器标签页。</p>
    <p class="muted small">下次演示：双击项目文件夹里的“启动演示.bat”。</p></div>`);
  return true;
}

// 静态演示（GitHub Pages）：没有后台服务，“退出”只是停止页面、清掉登录状态
function stopStatic(ask) {
  if (ask && !window.confirm("退出演示？页面会停止运行，刷新即可重新开始。")) return false;
  auth.clear();
  document.getElementById("topnav").innerHTML = "";
  document.getElementById("tabbar").innerHTML = "";
  document.getElementById("usermenu").innerHTML = "";
  render(`<div class="card shutdown-page"><h1>已停止运行</h1>
    <p>可以直接关闭这个标签页。网页版不在后台留下任何东西。</p>
    <p class="muted small">想重新开始：<a href="#/demo">回到演示首页</a>。</p></div>`);
  try { window.close(); } catch { /* 不是脚本打开的窗口时关不掉 */ }
  return true;
}
