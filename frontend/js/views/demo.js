// 演示首页（启动演示.bat 打开的就是这一页）：自动以教师身份登录，给出演示入口。
import { api, STATIC_DEMO } from "../api.js";
import { demoHealth, ensureTeacher, shutdownDemo } from "../demo_common.js";
import { demoNotice, esc, fmtScore, render } from "../ui.js";

export default async function () {
  const h = await demoHealth();
  if (!h.demo) {
    render(`<div class="card"><h1>演示模式没有开启</h1>
      <p>这个页面只在本机演示服务上可用。请双击项目文件夹里的 <b>启动演示.bat</b>，或运行 <span class="mono">run.py --demo</span>。</p>
      <a class="btn primary" href="#/login">去登录页</a></div>`);
    return;
  }
  if (await ensureTeacher()) {  // 刚刚自动登录：重新走一次路由，让顶部导航换成教师菜单
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    return;
  }
  const sc = await api("/api/demo/scenario");
  const rv = sc.reviewed_submission;
  const c = sc.counts;
  render(`
    <div class="spread"><h1>作业智评 · ${STATIC_DEMO ? "在线演示" : "本机演示"}</h1><button class="sm danger" id="stop2">${STATIC_DEMO ? "退出演示" : "关闭演示"}</button></div>
    ${demoNotice("当前以教师“王老师”的身份自动登录。")}
    <p class="muted">演示班级 <b>${esc(sc.class_name)}</b>：${c.students} 名学生、${c.submissions} 份作业，其中 ${c.reviewed} 份已由老师复核，${c.pending} 份待复核。</p>
    <div class="demo-hero">
      <a class="card demo-card main" href="#/demo/auto?start=1" id="go-auto">
        <span class="ico" aria-hidden="true">▶</span><h2>自动演示（约 1 分钟）</h2>
        <span class="small">家长拍照上传 → 图像预处理与识别 → AI 初评 → 教师复核改分 → 画像更新。每一步自动进行并配有说明，无人值守也能看懂。</span>
      </a>
      <a class="card demo-card" href="#/class/${sc.class_id}">
        <span class="ico" aria-hidden="true">▦</span><h2>班级看板</h2>
        <span class="small muted">分数分布、各维度得分率、共性问题、需要关注的学生、AI 与老师的一致性。</span>
      </a>
      ${rv ? `<a class="card demo-card" href="#/submission/${rv.id}">
        <span class="ico" aria-hidden="true">✎</span><h2>一份批改好的作文</h2>
        <span class="small muted">${esc(rv.student_name)} ·《${esc(rv.title)}》· 教师复核后 ${fmtScore(rv.total)} / ${fmtScore(rv.max_score)} ${esc(rv.band || "")}。可看原图、识别文字、错误标注、分项得分。</span>
      </a>` : ""}
      <a class="card demo-card" href="#/submissions?status=ai_done">
        <span class="ico" aria-hidden="true">✓</span><h2>待复核队列</h2>
        <span class="small muted">AI 已初评、等老师确认的作业；AI 拿不准的标“建议复核”。</span>
      </a>
      <a class="card demo-card" href="#/student/${sc.student_id}">
        <span class="ico" aria-hidden="true">◔</span><h2>学生画像</h2>
        <span class="small muted">${esc(sc.student_name)}：成绩趋势、写作能力雷达、知识点掌握度、高频错误。</span>
      </a>
      <a class="card demo-card" href="#/rubrics">
        <span class="ico" aria-hidden="true">☰</span><h2>评分量规</h2>
        <span class="small muted">YAML 量规：档次、评分维度、扣分项、复核策略，老师可以复制后修改。</span>
      </a>
    </div>
    <div class="card"><h2>手机端怎么看</h2>
      ${STATIC_DEMO
        ? `<p class="small">手机浏览器直接打开本页网址即可。想看家长端：点右上角“退出”，再点登录页上的“家长：parent_zhang”。<br>
           网页版是录制好的回放，不能真正上传和保存；完整版（FastAPI 后端，离线 mock）的运行方法见仓库 README。</p>`
        : sc.lan_url
        ? `<p>手机连同一个 WiFi，浏览器打开 <b class="mono">${esc(sc.lan_url)}</b>，点“家长：parent_zhang”登录。</p>`
        : `<p class="small">现在只允许本机访问。没有手机时：按 <b>F12</b> 打开开发者工具，再按 <b>Ctrl+Shift+M</b> 切到手机尺寸，退出登录后点“家长：parent_zhang”。<br>
           要用真手机：先关闭演示，再双击 <b>启动演示-手机可访问.bat</b>，本页会显示手机要打开的地址。</p>`}
    </div>`);
  document.getElementById("stop2").onclick = () => shutdownDemo();
}
