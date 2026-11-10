// 应用入口：hash 路由、按角色的导航、登录态。
import { api, auth, clearImageCache, STATIC_DEMO } from "./api.js";
import { destroyAll } from "./charts.js";
import { esc, render, errorBox, ROLE_LABEL } from "./ui.js";
import { shutdownDemo } from "./demo_common.js";

const NAV = {
  teacher: [["#/", "⌂", "首页"], ["#/submissions?status=ai_done", "✓", "待复核"], ["#/upload", "＋", "上传"], ["#/assignments", "▤", "作业"], ["#/rubrics", "☰", "量规"]],
  parent: [["#/", "⌂", "孩子"], ["#/upload", "＋", "拍照上传"], ["#/submissions", "▤", "作业记录"], ["#/privacy", "⚿", "授权隐私"]],
  student: [["#/", "⌂", "我的画像"], ["#/submissions", "▤", "我的作业"], ["#/upload", "＋", "上传"]],
  admin: [["#/", "⌂", "系统"], ["#/submissions", "▤", "提交"], ["#/rubrics", "☰", "量规"], ["#/privacy", "⚿", "隐私"]],
};

const ROUTES = [
  [/^\/login$/, () => import("./views/login.js")],
  [/^\/$/, () => import("./views/home.js")],
  [/^\/upload$/, () => import("./views/upload.js")],
  [/^\/submission\/(\d+)$/, () => import("./views/submission.js")],
  [/^\/submissions$/, () => import("./views/submissions.js")],
  [/^\/student\/(\d+)$/, () => import("./views/student.js")],
  [/^\/class\/(\d+)$/, () => import("./views/classboard.js")],
  [/^\/assignments$/, () => import("./views/assignments.js")],
  [/^\/rubrics$/, () => import("./views/rubrics.js")],
  [/^\/rubrics\/([\w\-]+)$/, () => import("./views/rubrics.js")],
  [/^\/privacy$/, () => import("./views/privacy.js")],
  [/^\/demo$/, () => import("./views/demo.js")],
  [/^\/demo\/auto$/, () => import("./views/demo_auto.js")],
];

// 健康检查只做一次：provider 是否为 mock、是否本机演示服务（run.py --demo）
const healthP = fetch("/api/health").then(r => r.json()).catch(() => ({}));

let cleanup = null;

function parseHash() {
  const h = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = h.split("?");
  return { path, query: new URLSearchParams(qs || "") };
}

function drawChrome() {
  const u = auth.user;
  const top = document.getElementById("topnav");
  const tab = document.getElementById("tabbar");
  const menu = document.getElementById("usermenu");
  if (!u) { top.innerHTML = tab.innerHTML = menu.innerHTML = ""; return; }
  const items = NAV[u.role] || [];
  const cur = location.hash || "#/";
  const isActive = href => (href === "#/" ? cur === "#/" || cur === "" : cur.startsWith(href.split("?")[0]) && (href.includes("?") ? cur === href : true));
  top.innerHTML = items.map(([h, , t]) => `<a href="${h}" class="${isActive(h) ? "active" : ""}">${esc(t)}</a>`).join("");
  tab.innerHTML = items.map(([h, i, t]) => `<a href="${h}" class="${isActive(h) ? "active" : ""}"><span class="ico" aria-hidden="true">${i}</span>${esc(t)}</a>`).join("");
  menu.innerHTML = `<span>${esc(u.display_name)}<span class="chip" style="margin-left:6px">${esc(ROLE_LABEL[u.role])}</span></span><button class="sm" id="logout">退出</button>`;
  document.getElementById("logout").onclick = () => { auth.clear(); clearImageCache(); location.hash = "#/login"; };
  healthP.then(h => {
    if (!h.demo || document.getElementById("demo-stop")) return;
    // 本机演示：导航里加“演示”入口，右上角加“关闭演示”（关掉后台服务，不留进程）
    const isDemo = (location.hash || "").startsWith("#/demo");
    top.insertAdjacentHTML("beforeend", `<a href="#/demo" class="${isDemo ? "active" : ""}">演示</a>`);
    menu.insertAdjacentHTML("afterbegin", STATIC_DEMO ? `<button class="sm danger" id="demo-stop" title="停止静态演示">退出演示</button>`
      : `<button class="sm danger" id="demo-stop" title="关闭本机演示服务">关闭演示</button>`);
    document.getElementById("demo-stop").onclick = () => shutdownDemo();
  });
}

async function route() {
  const { path, query } = parseHash();
  if (!auth.token && path !== "/login" && path !== "/privacy" && !path.startsWith("/demo")) { location.hash = "#/login"; return; }
  if (typeof cleanup === "function") { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  destroyAll();
  drawChrome();
  const hit = ROUTES.find(([re]) => re.test(path));
  if (!hit) { render(`<div class="card"><h1>页面不存在</h1><a class="btn" href="#/">返回首页</a></div>`); return; }
  const params = path.match(hit[0]).slice(1);
  render(`<div class="loading">加载中…</div>`);
  try {
    const mod = await hit[1]();
    cleanup = await mod.default({ params, query, user: auth.user });
  } catch (e) {
    console.error(e);
    if (e.status !== 401) render(errorBox(e));
  }
  document.getElementById("app").focus({ preventScroll: true });
}

// 路由串行执行：上一个页面的异步渲染结束后才开始下一个，避免两个页面同时往 #app 里写导致互相覆盖。
// 连续多次跳转时只渲染最后一个。
let chain = Promise.resolve();
let pending = 0;
function schedule() {
  pending += 1;
  chain = chain.then(async () => {
    pending -= 1;
    if (pending > 0) return;  // 后面还有更新的跳转，跳过这一次
    await route();
  });
}

// 演示模式提示：识别或评分仍是离线 mock 时，顶部横幅明确告诉看的人“这不是真实模型输出”
async function markDemoMode() {
  try {
    const h = await healthP;
    const mock = [h.ocr === "mock" ? "识别" : "", h.grader === "mock" ? "评分" : ""].filter(Boolean);
    if (mock.length) {
      const b = document.querySelector(".ai-banner");
      b.classList.add("demo");
      b.innerHTML = STATIC_DEMO
        ? `<b>静态演示 · 合成数据</b>：网页版回放录制好的演示数据（学生、作业照片均为程序合成），${mock.join("与")}为离线规则模拟（mock），非真实模型输出 · 不能保存修改 · AI 评分仅供参考，以教师复核结果为准`
        : `<b>演示数据，非真实模型输出</b>：${mock.join("与")}为离线规则模拟（mock） · AI 评分仅供参考，以教师复核结果为准`;
      document.body.classList.add("demo-mode");
      if (STATIC_DEMO) document.body.classList.add("static-demo");
    }
  } catch { /* 健康检查失败不影响使用 */ }
}
markDemoMode();

window.addEventListener("hashchange", schedule);
window.addEventListener("DOMContentLoaded", async () => {
  if (auth.token) {
    try { const me = await api("/api/auth/me"); auth.set(auth.token, me); } catch { /* 401 时 api() 已跳转登录 */ }
  }
  schedule();
});

if ("serviceWorker" in navigator && location.protocol !== "file:" && !STATIC_DEMO) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* 非 HTTPS 局域网访问时可能不可用，忽略 */ });
}
