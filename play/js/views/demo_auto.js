// 自动演示：家长上传一张示例作业照片 → 预处理 → 识别 → AI 初评 → 教师复核改分 → 家长看到结果 → 画像更新。
// 全部调用真实的业务接口（和手机端、教师端用的是同一套），每步停留几秒并配说明，无人值守也能看懂。
// 地址参数：start=1 进入即开始；speed=fast 每步只停 0.6 秒（自动化测试用）；loop=1 结束后 20 秒重新开始。
import { api, randomToken, STATIC_DEMO } from "../api.js";
import { callAs, demoHealth, ensureTeacher, tokenFor } from "../demo_common.js";
import { bar, demoNotice, esc, fmtScore, pct, render, SUBJECTS } from "../ui.js";

const STAGE = { uploaded: "已上传", preprocess: "图像预处理", ocr: "手写识别", grading: "量规评分", done: "完成" };

const STEPS = [
  {
    title: "家长登录", who: "家长 · 手机",
    explain: "家长在手机上登录，只能看到自己的孩子。孩子不满 14 周岁，监护人签了“基本同意”才允许上传作业；没有勾选“云端 AI”的孩子，作业不会发给境外或云端模型。",
    tech: "普通程序：账号登录（JWT 令牌）+ 按角色过滤数据 + 合规闸门。不涉及 AI。",
  },
  {
    title: "拍照", who: "家长 · 手机",
    explain: "家长对着作业本拍一张照片。真手机上拍完会自动弹出裁剪框，可以拖四个角去掉桌面、左右旋转。这里用程序合成的“仿手写照片”代替真实拍照。",
    tech: "普通程序：调用手机后置相机；JPEG 超过 1.5 MB 先在手机上压缩。不涉及 AI。",
  },
  {
    title: "上传", who: "家长 · 手机",
    explain: "照片上传到学校自己的服务器。服务器会去掉照片里的 EXIF 信息（包括 GPS 位置）。网络不好时自动重试，同一批照片不会重复提交。",
    tech: "普通程序：FastAPI 上传接口 + 幂等令牌（client_token），后台排队处理。",
  },
  {
    title: "图像预处理", who: "服务器",
    explain: "先把照片“摆正、擦干净”：找到作业纸的四个角做透视矫正，把倾斜的字行转正，去掉手机投下的阴影、提高对比度，方便后面识别。",
    tech: "传统图像算法（OpenCV，不是 AI）：Canny 边缘 + 轮廓找纸张四边形 → 透视变换；投影法估计倾斜角；形态学估计背景光照后相除去阴影，再做 CLAHE 对比度增强。",
  },
  {
    title: "手写识别", who: "服务器",
    explain: "把图片里的手写字变成文字。真实部署时由视觉大模型或 OCR 来识别。演示里的 mock 直接读出图片里内嵌的标准文本，所以这里的识别结果“完全正确”，真实手写的准确率还没有测过。",
    tech: "AI：视觉大模型（Claude / 通义千问 VL / 服务器本地 Qwen-VL）或 PaddleOCR。当前为离线 mock，非真实模型输出。",
  },
  {
    title: "AI 初评", who: "服务器",
    explain: "按评分量规逐项打分：每个维度给分并说明理由，在原文里标出错误，给出评语和置信度。服务器会重新核算分数（越界截断、总分重算、定档），拿不准的标“建议人工复核”。",
    tech: "AI：大模型按 YAML 量规输出结构化 JSON（当前为离线规则 mock）；分数核算和复核判断是普通程序。",
  },
  {
    title: "教师复核改分", who: "老师 · 电脑",
    explain: "老师对照原图和识别文字逐项确认，觉得不合适的就改，并写下修改理由。AI 的原始分永久保留，每次修改都追加一条记录，用来统计 AI 和老师的一致性。",
    tech: "普通程序：复核记录只追加不覆盖。一致性指标（QWK 等）在班级看板里查看。",
  },
  {
    title: "家长看到结果", who: "家长 · 手机",
    explain: "老师确认后，家长端的状态变成“教师已复核”，看到的是老师给的分数和评语。家长看不到 AI 原始分，也看不到老师的内部备注。",
    tech: "普通程序：按角色裁剪返回内容。",
  },
  {
    title: "画像更新", who: "家长 / 老师",
    explain: "学生画像用每份作业的最终分数（老师复核过的以老师为准）重新计算：成绩趋势、写作能力雷达、知识点掌握度（近期表现权重更大）、高频错误。",
    tech: "普通统计（得分率、加权平均、计数），不是 AI。",
  },
];

const sleep = ms => new Promise(r => setTimeout(r, ms));

export default async function ({ query }) {
  const h = await demoHealth();
  if (!h.demo) {
    render(`<div class="card"><h1>演示模式没有开启</h1><p>自动演示只在本机演示服务上可用：双击 <b>启动演示.bat</b>。</p></div>`);
    return;
  }
  if (await ensureTeacher()) { window.dispatchEvent(new HashChangeEvent("hashchange")); return; }

  const fast = query.get("speed") === "fast";
  const loop = query.get("loop") === "1";
  const DWELL = fast ? 600 : 7000;
  let stopped = false;
  let paused = false;
  let skip = false;
  let running = false;
  const urls = [];

  render(`
    <div class="spread"><h1>自动演示：一份作业从拍照到画像</h1><a class="btn sm" href="#/demo">返回演示首页</a></div>
    ${demoNotice("本页的“老师改分”也由程序自动完成。")}
    <div class="ad-layout">
      <div class="card">
        <ol class="ad-steps" id="ad-steps">${STEPS.map((s, i) => `<li data-i="${i}"><span class="n">${i + 1}</span><span>${esc(s.title)}<br><span class="small muted">${esc(s.who)}</span></span></li>`).join("")}</ol>
        <div class="row" style="margin-top:12px">
          <button class="primary sm" id="ad-start">开始演示</button>
          <button class="sm" id="ad-pause" disabled>暂停</button>
          <button class="sm" id="ad-next" disabled>下一步</button>
        </div>
        <p class="small muted" style="margin-top:8px">每步自动停留约 ${Math.round(DWELL / 1000) || 1} 秒；可以随时暂停。</p>
      </div>
      <div class="card" id="ad-panel" aria-live="polite">
        <h2>准备好了</h2>
        <p>点“开始演示”，系统会自动完成一遍完整流程：家长拍照上传一篇作文，服务器识别、评分，老师复核改分，家长看到结果，画像随之更新。</p>
        <p class="muted small">${STATIC_DEMO ? "整个过程约 1 分钟。网页版回放的是本机完整版录下的同一流程（合成数据），不会真的上传或保存，刷新页面即恢复初始状态。" : "整个过程约 1 分钟，会在演示数据库里新增一份标注为演示的作业（下次启动演示时自动恢复初始数据）。"}</p>
      </div>
    </div>`);
  const $ = id => document.getElementById(id);
  const panel = $("ad-panel");
  const btnStart = $("ad-start"), btnPause = $("ad-pause"), btnNext = $("ad-next");

  const mark = (i, cls) => {
    document.querySelectorAll("#ad-steps li").forEach(li => {
      const k = Number(li.dataset.i);
      if (k === i) li.className = cls;
      else if (cls === "now" && k > i) li.className = "";
    });
  };
  const showStep = (i, body, state = "") => {
    const s = STEPS[i];
    panel.innerHTML = `<div class="spread"><h2>第 ${i + 1} 步 / 共 ${STEPS.length} 步：${esc(s.title)}</h2><span class="chip">${esc(s.who)}</span></div>
      <div class="ad-explain"><div>${esc(s.explain)}</div><div class="tech">${esc(s.tech)}</div></div>
      <div id="ad-body">${body}</div>${state}<div class="ad-timer"><span id="ad-bar"></span></div>`;
  };
  const setBody = html => { const b = $("ad-body"); if (b) b.innerHTML = html; };
  const blobURL = async (token, path) => {
    const res = await callAs(token, path, { raw: true });
    const u = URL.createObjectURL(await res.blob());
    urls.push(u);
    return u;
  };

  // 停留：可暂停、可跳过，进度条显示剩余时间
  async function dwell() {
    let t = 0;
    while (t < DWELL) {
      if (stopped) return;
      if (skip) { skip = false; return; }
      if (!paused) t += 100;
      const b = $("ad-bar");
      if (b) b.style.width = `${Math.min(100, (t / DWELL) * 100)}%`;
      await sleep(100);
    }
  }

  async function run() {
    if (running) return;
    running = true;
    btnStart.disabled = true; btnPause.disabled = false; btnNext.disabled = false;
    document.querySelectorAll("#ad-steps li").forEach(li => { li.className = ""; });
    const ctx = {};
    const steps = [
      // 1 家长登录
      async () => {
        ctx.sc = await api("/api/demo/scenario");
        ctx.teacher = (await tokenFor("teacher")).access_token;
        const p = await tokenFor("parent");
        ctx.parent = p.access_token;
        const kids = await callAs(ctx.parent, "/api/students");
        ctx.kid = kids.find(k => k.id === ctx.sc.student_id) || kids[0];
        ctx.prof0 = await callAs(ctx.parent, `/api/students/${ctx.kid.id}/profile`);
        const cs = ctx.kid.consents || {};
        const subj = ctx.prof0.subjects[ctx.sc.subject];
        return `<div class="stats">
          <div class="stat"><div class="v">${esc(p.user.display_name)}</div><div class="k">已登录（家长）</div></div>
          <div class="stat"><div class="v">${esc(ctx.kid.name)}</div><div class="k">绑定的孩子</div></div>
          <div class="stat"><div class="v">${cs.basic ? "✓ 已签" : "✗ 未签"}</div><div class="k">基本同意（允许上传）</div></div>
          <div class="stat"><div class="v">${cs.cloud_ai ? "✓ 已勾选" : "✗ 未勾选"}</div><div class="k">云端 AI 同意</div></div></div>
          <p>画像基线：已有 <b>${ctx.prof0.submission_count}</b> 份作业${subj ? `，${esc(subj.label)}平均得分率 <b>${pct(subj.avg_ratio)}</b>（${subj.count} 次）` : ""}。</p>
          <p class="small muted">今天要交的作业：《${esc(ctx.sc.assignment_title)}》</p>`;
      },
      // 2 拍照
      async () => {
        ctx.photoURL = await blobURL(ctx.parent, `/api/demo/sample-photo?assignment_id=${ctx.sc.assignment_id}`);
        ctx.photoBlob = await fetch(ctx.photoURL).then(r => r.blob());
        return `<div class="ad-pair"><figure><img src="${ctx.photoURL}" alt="家长拍的作业照片（程序合成的演示图）">
          <figcaption>家长拍的照片（演示数据：程序合成的仿手写图，${Math.round(ctx.photoBlob.size / 1024)} KB）</figcaption></figure>
          <div><p>照片里能看到：作业纸有点歪、四周露出桌面、纸面上有手机投下的阴影。这些都是真实拍照常见的问题，下一步由服务器自动处理。</p></div></div>`;
      },
      // 3 上传
      async () => {
        const fd = new FormData();
        fd.append("assignment_id", ctx.sc.assignment_id);
        fd.append("student_id", ctx.kid.id);
        fd.append("client_token", randomToken());
        fd.append("files", new File([ctx.photoBlob], "homework.png", { type: "image/png" }));
        const sub = await callAs(ctx.parent, "/api/submissions", { method: "POST", form: fd });
        ctx.subId = sub.id;
        return `<p>上传成功：提交编号 <b>#${sub.id}</b>，状态“排队中”。</p>
          <p class="small muted">${Math.round(ctx.photoBlob.size / 1024)} KB · 1 页 · 服务器已经开始在后台处理，家长可以关掉页面，处理完再回来看。</p>`;
      },
      // 4 预处理（等流水线跑完；mock 很快，通常 1–3 秒）
      async () => {
        let d;
        for (let k = 0; k < 300 && !stopped; k++) {
          d = await callAs(ctx.parent, `/api/submissions/${ctx.subId}`);
          setBody(`<p>当前阶段：<b>${esc(STAGE[d.stage] || d.stage || "排队中")}</b>…</p>`);
          if (!["queued", "processing"].includes(d.status)) break;
          await sleep(400);
        }
        if (d.status === "failed") throw new Error("处理失败：" + (d.error || "未知原因"));
        ctx.detail = d;
        const m = (d.preprocess_meta?.pages || [])[0] || {};
        const orig = await blobURL(ctx.parent, `/api/submissions/${ctx.subId}/pages/1/image?kind=original`);
        const proc = await blobURL(ctx.parent, `/api/submissions/${ctx.subId}/pages/1/image?kind=processed`);
        return `<div class="ad-pair">
          <figure><img src="${orig}" alt="原图"><figcaption>处理前（原图）</figcaption></figure>
          <figure><img src="${proc}" alt="处理后"><figcaption>处理后：矫正、去阴影、增强</figcaption></figure></div>
          <div class="table-wrap"><table><tbody>
            <tr><th>检测到作业纸</th><td>${m.paper_detected ? "是" : "否"}${m.paper_area_ratio != null ? `（占画面 ${pct(m.paper_area_ratio)}）` : ""}</td></tr>
            <tr><th>纠偏角度</th><td>${m.deskew_angle != null ? `${fmtScore(m.deskew_angle, 1)}°` : "—"}</td></tr>
            <tr><th>去阴影 / 增强</th><td>${m.enhanced ? "已做" : "未做"}</td></tr>
            <tr><th>清晰度评分</th><td>${m.blur_score != null ? fmtScore(m.blur_score, 0) : "—"}（拉普拉斯方差，越大越清楚）</td></tr>
          </tbody></table></div>`;
      },
      // 5 识别
      async () => {
        const d = ctx.detail;
        const text = d.ocr_text || "";
        return `<p>识别方式：<span class="chip demo">${esc(d.ocr_provider || "mock")}（离线模拟）</span>
            置信度 <b>${pct(d.ocr_confidence)}</b> · 共 ${text.length} 个字符</p>
          <div class="ad-text">${esc(text.length > 600 ? text.slice(0, 600) + "……" : text)}</div>`;
      },
      // 6 AI 初评（老师端能看到 AI 原始结果）
      async () => {
        const t = await callAs(ctx.teacher, `/api/submissions/${ctx.subId}`);
        const ai = t.ai_result;
        ctx.ai = ai;
        const errs = ai.errors || [];
        return `<div class="scorebox"><span class="big">${fmtScore(ai.total_score)}</span><span class="of">/ ${fmtScore(ai.max_score)} · ${esc(ai.band || "")}</span>
            <span class="chip demo">AI 初评 · ${esc(ai.provider)}（离线规则模拟）</span>
            ${ai.needs_review ? '<span class="chip danger">建议人工复核</span>' : '<span class="chip ok">AI 较有把握</span>'}</div>
          ${ai.criteria.map(c => `<div class="crit"><div class="spread"><span>${esc(c.name)}</span><span class="score">${fmtScore(c.score)} / ${fmtScore(c.max_score)}</span></div>${bar(c.score / (c.max_score || 1))}</div>`).join("")}
          <p class="small">标出 <b>${errs.length}</b> 处问题${errs.length ? "，例如：" + errs.slice(0, 3).map(e => `“${esc(e.quote)}”${e.suggestion ? "→“" + esc(e.suggestion) + "”" : ""}`).join("、") : ""}。
          置信度 ${pct(ai.confidence)}。${ai.review_reasons?.length ? "复核原因：" + esc(ai.review_reasons.join("；")) : ""}</p>
          ${ai.comment ? `<p class="small muted">评语：${esc(ai.comment)}</p>` : ""}`;
      },
      // 7 教师复核改分
      async () => {
        const ai = ctx.ai;
        // 选“离满分最远”的维度加分（最多 2 分）；都满分时给最高分维度扣 1 分
        const room = ai.criteria.map(c => ({ c, r: c.max_score - c.score })).sort((a, b) => b.r - a.r);
        let target = room[0].c, delta = Math.min(2, Math.floor(room[0].r * 2) / 2);
        if (delta <= 0) { target = [...ai.criteria].sort((a, b) => b.score - a.score)[0]; delta = -Math.min(1, target.score); }
        const reason = delta > 0 ? `老师认为“${target.name}”AI 给得偏严，加 ${fmtScore(delta)} 分，并写下修改理由。`
          : `老师认为“${target.name}”略高，扣 ${fmtScore(-delta)} 分。`;
        const body = {
          criteria: ai.criteria.map(c => ({ id: c.id, score: c.id === target.id ? c.score + delta : c.score, comment: c.comment || "" })),
          deductions: ai.deductions.map(x => ({ id: x.id, name: x.name || "", points: x.points, reason: x.reason || "" })),
          errors: ai.errors,
          comment: (ai.comment ? ai.comment + " " : "") + "（老师补充：整体完成得不错，记得订正标出的问题。）",
          note: "自动演示：" + reason,
        };
        const r = await callAs(ctx.teacher, `/api/submissions/${ctx.subId}/review`, { method: "POST", body });
        ctx.final = r.final_result;
        const rows = r.final_result.criteria.map(c => {
          const a = ai.criteria.find(x => x.id === c.id);
          const d = c.score - (a ? a.score : 0);
          return `<tr><td>${esc(c.name)}</td><td class="num">${fmtScore(a?.score)}</td><td class="num ${d > 0 ? "up" : d < 0 ? "down" : ""}">${fmtScore(c.score)}${d ? ` (${d > 0 ? "+" : ""}${fmtScore(d)})` : ""}</td></tr>`;
        }).join("");
        return `<p>${esc(reason)}</p>
          <div class="table-wrap"><table class="ad-diff"><thead><tr><th>评分维度</th><th class="num">AI 初评</th><th class="num">老师复核</th></tr></thead>
          <tbody>${rows}<tr><th>总分</th><th class="num">${fmtScore(ai.total_score)}</th><th class="num">${fmtScore(r.final_result.total_score)}（${esc(r.final_result.band)}）</th></tr></tbody></table></div>
          <p class="small muted">已保存为一条复核记录；AI 原始分 ${fmtScore(ai.total_score)} 保留，用于计算 AI 与老师的一致性。</p>`;
      },
      // 8 家长看到结果
      async () => {
        const d = await callAs(ctx.parent, `/api/submissions/${ctx.subId}`);
        const f = d.final_result;
        return `<div class="scorebox"><span class="big">${fmtScore(f.total_score)}</span><span class="of">/ ${fmtScore(f.max_score)} · ${esc(f.band)}</span>
            <span class="chip ok">${d.status === "reviewed" ? "教师已复核" : esc(d.status)}</span></div>
          <p>家长端看到的是老师复核后的分数和评语。</p>
          <ul class="small"><li>AI 原始结果：${d.ai_result ? "可见" : "<b>家长不可见</b>"}</li>
          <li>老师的修改理由（内部备注）：家长不可见</li>
          <li>页面始终提示：AI 评分仅供参考，以教师复核结果为准</li></ul>`;
      },
      // 9 画像更新
      async () => {
        const p1 = await callAs(ctx.parent, `/api/students/${ctx.kid.id}/profile`);
        const p0 = ctx.prof0;
        const s0 = p0.subjects[ctx.sc.subject], s1 = p1.subjects[ctx.sc.subject];
        const label = s1?.label || SUBJECTS[ctx.sc.subject] || "";
        const last = p1.trend.find(t => t.submission_id === ctx.subId);
        return `<div class="table-wrap"><table><thead><tr><th></th><th class="num">之前</th><th class="num">之后</th></tr></thead><tbody>
            <tr><td>作业总数</td><td class="num">${p0.submission_count}</td><td class="num">${p1.submission_count}</td></tr>
            <tr><td>${esc(label)}平均得分率</td><td class="num">${pct(s0?.avg_ratio)}</td><td class="num">${pct(s1?.avg_ratio)}</td></tr>
            <tr><td>${esc(label)}作业次数</td><td class="num">${s0?.count ?? 0}</td><td class="num">${s1?.count ?? 0}</td></tr>
            <tr><td>未复核作业</td><td class="num">${p0.unreviewed_count}</td><td class="num">${p1.unreviewed_count}</td></tr>
          </tbody></table></div>
          ${last ? `<p>成绩趋势新增一个点：${esc(last.date)}《${esc(last.assignment)}》得分率 <b>${pct(last.ratio)}</b>（老师复核后）。</p>` : ""}
          <div class="row"><a class="btn primary sm" href="#/student/${ctx.kid.id}">打开 ${esc(ctx.kid.name)} 的画像</a>
            <a class="btn sm" href="#/submission/${ctx.subId}">打开这份作业</a>
            <a class="btn sm" href="#/class/${ctx.sc.class_id}">班级看板</a></div>`;
      },
    ];

    for (let i = 0; i < steps.length; i++) {
      if (stopped) return;
      mark(i, "now");
      showStep(i, `<p class="muted">进行中…</p>`);
      try {
        const html = await steps[i]();
        if (stopped) return;
        setBody(html);
        mark(i, "done");
      } catch (e) {
        if (stopped) return;
        mark(i, "fail");
        setBody(`<div class="notice danger">这一步出错了：${esc(e.message || e)}</div>
          <p class="small">可以点“重新开始”再试一次；如果反复出错，关闭演示后重新双击“启动演示.bat”。</p>`);
        finish(false);
        return;
      }
      if (i < steps.length - 1) await dwell();
    }
    if (stopped) return;
    panel.insertAdjacentHTML("beforeend", `<div class="notice ok" id="ad-done" style="margin-top:12px"><b>演示完成。</b>一份作业走完了全流程：拍照 → 预处理 → 识别 → AI 初评 → 老师复核 → 家长查看 → 画像更新。${loop ? " 20 秒后自动重新开始。" : ""}</div>`);
    finish(true);
    if (loop) {
      for (let t = 0; t < 200 && !stopped; t++) await sleep(100);
      if (!stopped) run();
    }
  }

  function finish(ok) {
    running = false;
    btnStart.disabled = false;
    btnStart.textContent = "重新开始";
    btnPause.disabled = btnNext.disabled = true;
    paused = false; btnPause.textContent = "暂停";
    document.body.dataset.autodemo = ok ? "done" : "failed";  // 给自动化测试看的
  }

  btnStart.onclick = () => { document.body.dataset.autodemo = "running"; run(); };
  btnPause.onclick = () => { paused = !paused; btnPause.textContent = paused ? "继续" : "暂停"; };
  btnNext.onclick = () => { skip = true; };
  if (query.get("start") === "1") btnStart.onclick();

  return () => {
    stopped = true;
    delete document.body.dataset.autodemo;
    urls.forEach(u => URL.revokeObjectURL(u));
  };
}
