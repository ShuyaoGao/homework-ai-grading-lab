// GitHub Pages 静态演示的接口模拟层：接管页面发出的 /api/* 请求（fetch 和 XMLHttpRequest），
// 回放 tools/static_demo/record.py 从真实后端录下的数据（种子数据全部为程序合成，评分为离线 mock）。
// 必须在 js/app.js 之前用普通 <script> 加载，并且先加载 mock/data.js。
// 不能保存修改：写操作一律返回“静态演示不能保存”，只有自动演示里的上传和老师改分会回放录好的结果。
(function () {
  "use strict";
  const D = window.EDU_MOCK_DATA;
  if (!D) { console.error("缺少 mock/data.js"); return; }
  window.EDU_STATIC_DEMO = true;

  const here = (document.currentScript && document.currentScript.src) || location.href;
  const IMG_BASE = new URL("img/", here).href;
  const realFetch = window.fetch.bind(window);
  const RealXHR = window.XMLHttpRequest;
  const READONLY = "静态演示版（GitHub Pages）只能浏览，不能保存修改。完整的上传、识别、复核改分请在本机运行完整版（见仓库 README）；“自动演示”里的上传和改分是录制好的回放。";
  const TOKEN_PREFIX = "static-demo-";
  const tour = D.tour;
  const S = { state: "before", polls: 0 };   // 刷新页面即回到初始状态
  const STAGES = ["preprocess", "ocr", "grading"];
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  function isApi(u) {
    return /^\/api\//.test(String(u));   // 前端只用 "/api/..." 这种写法
  }
  function normKey(url) {
    const u = new URL(url, "http://x");
    const q = new URLSearchParams(u.search);
    q.sort();
    const qs = q.toString();
    return u.pathname + (qs ? "?" + qs : "");
  }
  function clone(v) { return v == null ? v : JSON.parse(JSON.stringify(v)); }
  function json(status, body) { return { status, body }; }
  function roleOf(headers) {
    const a = headers.Authorization || headers.authorization || "";
    const m = /^Bearer\s+(.+)$/.exec(a);
    if (!m || m[1].indexOf(TOKEN_PREFIX) !== 0) return null;
    const role = m[1].slice(TOKEN_PREFIX.length);
    return D.users[role] ? role : null;
  }

  // 录制表：先查当前时刻（mid/after）的差异，再查初始状态
  const recordIndex = {};
  Object.keys(D.snapshots).forEach(st => {
    recordIndex[st] = {};
    Object.keys(D.snapshots[st]).forEach(role => {
      const m = {};
      Object.keys(D.snapshots[st][role]).forEach(k => { m[normKey(k)] = D.snapshots[st][role][k]; });
      recordIndex[st][role] = m;
    });
  });
  function lookup(role, key) {
    const order = S.state === "before" ? ["before"] : [S.state, "before"];
    for (const st of order) {
      const hit = recordIndex[st] && recordIndex[st][role] && recordIndex[st][role][key];
      if (hit) return hit;
    }
    return null;
  }

  function listSubmissions(role, query) {
    const rec = lookup(role, normKey("/api/submissions?limit=1000"));
    if (!rec || rec[0] !== 200) return json(rec ? rec[0] : 404, rec ? clone(D.blobs[rec[1]]) : { detail: "没有数据" });
    let list = clone(D.blobs[rec[1]]);
    const st = query.get("status");
    if (st) { const set = st.split(","); list = list.filter(s => set.indexOf(s.status) !== -1); }
    const nr = query.get("needs_review");
    if (nr === "true" || nr === "false") list = list.filter(s => !!s.needs_review === (nr === "true"));
    ["student_id", "assignment_id"].forEach(f => { const v = query.get(f); if (v) list = list.filter(s => String(s[f]) === v); });
    const cid = query.get("class_id");
    if (cid) {   // 简表里没有班级号：用该班学生名单过滤
      const r2 = lookup(role, normKey(`/api/classes/${cid}/students`));
      const ids = r2 && r2[0] === 200 ? D.blobs[r2[1]].map(s => s.id) : [];
      list = list.filter(s => ids.indexOf(s.student_id) !== -1);
    }
    list.sort((a, b) => (a.submitted_at < b.submitted_at ? 1 : a.submitted_at > b.submitted_at ? -1 : 0));
    const lim = Math.min(Number(query.get("limit") || 200), 1000);
    return json(200, list.slice(0, lim));
  }

  async function handle(method, url, headers, body) {
    const u = new URL(url, "http://x");
    const path = u.pathname;
    const key = normKey(url);
    method = (method || "GET").toUpperCase();

    if (path === "/api/health") return json(200, clone(D.health));
    if (path === "/api/auth/login" && method === "POST") {
      const p = body instanceof URLSearchParams ? body : new URLSearchParams(typeof body === "string" ? body : "");
      const acc = D.accounts[p.get("username") || ""];
      if (!acc || acc.password !== p.get("password")) return json(401, { detail: "用户名或密码错误" });
      return json(200, { access_token: TOKEN_PREFIX + acc.role, token_type: "bearer", user: clone(D.users[acc.role]) });
    }
    if (path === "/api/demo/shutdown") return json(200, { ok: true, static: true });

    const role = roleOf(headers);
    if (!role) return json(401, { detail: "未登录或登录已失效" });

    if (method === "GET") {
      if (path === "/api/submissions") return listSubmissions(role, u.searchParams);
      const m = /^\/api\/submissions\/(\d+)$/.exec(path);
      if (m && Number(m[1]) === tour.submission_id && S.state === "mid" && S.polls > 0) {
        // 自动演示第 4 步：先显示几次“处理中”，再给出录好的结果（真实后端是后台排队处理）
        const rec = lookup(role, key);
        const d = rec && rec[0] === 200 ? clone(D.blobs[rec[1]]) : null;
        if (d) {
          const stage = STAGES[STAGES.length - S.polls];
          S.polls -= 1;
          return json(200, Object.assign(d, { status: "processing", stage }));
        }
      }
      const rec = lookup(role, key);
      if (!rec) return json(404, { detail: "静态演示版没有录这项数据" });
      if (typeof rec[1] === "string" && rec[1].indexOf("img:") === 0) return { image: rec[1].slice(4), status: rec[0] };
      return json(rec[0], clone(D.blobs[rec[1]]));
    }

    // 写操作：只回放自动演示的两步
    if (method === "POST" && path === "/api/submissions" && role === "parent" && typeof FormData !== "undefined" && body instanceof FormData) {
      const f = body.get("files");
      if (String(body.get("assignment_id")) === String(tour.assignment_id) && String(body.get("student_id")) === String(tour.student_id)
          && f && f.name === "homework.png") {
        S.state = "mid"; S.polls = STAGES.length;
        return json(202, Object.assign(clone(D.blobs[tour.upload]), { status: "queued", stage: "uploaded" }));
      }
    }
    if (method === "POST" && path === `/api/submissions/${tour.submission_id}/review` && role === "teacher" && S.state !== "before") {
      S.state = "after"; S.polls = 0;
      return json(200, clone(D.blobs[tour.review]));
    }
    return json(403, { detail: READONLY });
  }

  function headersToObj(h) {
    const o = {};
    if (!h) return o;
    if (typeof Headers !== "undefined" && h instanceof Headers) { h.forEach((v, k) => { o[k] = v; }); return o; }
    if (Array.isArray(h)) { h.forEach(([k, v]) => { o[k] = v; }); return o; }
    return Object.assign(o, h);
  }

  async function toResponse(r) {
    if (r.image) {
      const res = await realFetch(IMG_BASE + r.image);
      return new Response(await res.blob(), { status: res.status, headers: { "content-type": res.headers.get("content-type") || "image/jpeg" } });
    }
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { "content-type": "application/json" } });
  }

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || String(input);
    if (!isApi(url)) return realFetch(input, init);
    init = init || {};
    await sleep(40 + Math.random() * 80);   // 模拟一点网络延迟
    const r = await handle(init.method || (input && input.method) || "GET", url, headersToObj(init.headers), init.body);
    return toResponse(r);
  };

  // 上传页用 XHR（为了上传进度）：只模拟 /api/*，其余交给浏览器原生 XHR
  function MockXHR() {
    this._real = null; this._headers = {}; this.readyState = 0; this.status = 0; this.responseText = "";
    this.upload = {}; this.timeout = 0;
  }
  MockXHR.prototype.open = function (method, url) {
    this._method = method; this._url = url;
    if (!isApi(url)) { this._real = new RealXHR(); this._real.open.apply(this._real, arguments); }
    this.readyState = 1;
  };
  MockXHR.prototype.setRequestHeader = function (k, v) { if (this._real) this._real.setRequestHeader(k, v); else this._headers[k] = v; };
  MockXHR.prototype.getResponseHeader = function (k) { return this._real ? this._real.getResponseHeader(k) : (/content-type/i.test(k) ? "application/json" : null); };
  MockXHR.prototype.abort = function () { if (this._real) this._real.abort(); };
  MockXHR.prototype.send = function (body) {
    const self = this;
    if (this._real) {
      const r = this._real;
      ["onload", "onerror", "ontimeout", "onreadystatechange", "onprogress"].forEach(ev => { if (self[ev]) r[ev] = function (e) { self.status = r.status; self.responseText = r.responseText; self.readyState = r.readyState; return self[ev](e); }; });
      if (self.upload.onprogress) r.upload.onprogress = self.upload.onprogress;
      r.timeout = self.timeout;
      return r.send(body);
    }
    (async () => {
      for (let i = 1; i <= 5; i++) { await sleep(80); if (self.upload.onprogress) self.upload.onprogress({ lengthComputable: true, loaded: i, total: 5 }); }
      const r = await handle(self._method, self._url, self._headers, body);
      self.status = r.status; self.readyState = 4;
      self.responseText = JSON.stringify(r.body || {});
      if (self.onreadystatechange) self.onreadystatechange();
      if (self.onload) self.onload();
    })();
  };
  window.XMLHttpRequest = MockXHR;
})();
