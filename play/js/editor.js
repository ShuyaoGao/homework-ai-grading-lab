// 手机端图片编辑：旋转 + 拖动四角裁剪。只在手机上“记录编辑参数”，真正的旋转/裁剪由服务器在原图上做
// （保留原始分辨率，弱机型不用在浏览器里重新编码大图）。返回 {rotate, crop:[x0,y0,x1,y1]}，坐标归一化到 0–1。
import { esc } from "./ui.js";

export function openEditor(url, initial = null) {
  return new Promise(resolve => {
    const dlg = document.createElement("dialog");
    dlg.className = "editor";
    dlg.innerHTML = `
      <div class="spread"><b>调整照片</b><span class="small muted">拖动四个角裁掉桌面和多余部分</span></div>
      <div class="ed-stage"><canvas></canvas></div>
      <div class="row ed-tools">
        <button type="button" data-a="left" aria-label="向左旋转">↺ 左转</button>
        <button type="button" data-a="right" aria-label="向右旋转">↻ 右转</button>
        <button type="button" data-a="reset">还原</button>
      </div>
      <div class="row" style="justify-content:flex-end;margin-top:8px">
        <button type="button" data-a="cancel">取消</button>
        <button type="button" class="primary" data-a="ok">完成</button>
      </div>`;
    document.body.appendChild(dlg);
    const canvas = dlg.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    const img = new Image();
    let rotate = initial?.rotate || 0;
    let crop = initial?.crop ? [...initial.crop] : [0, 0, 1, 1];
    let drag = null;  // 正在拖动的角：0 左上 1 右上 2 右下 3 左下

    function layout() {
      const maxW = Math.min(window.innerWidth - 48, 640);
      const maxH = Math.min(window.innerHeight * 0.62, 760);
      const rw = rotate % 180 ? img.naturalHeight : img.naturalWidth;
      const rh = rotate % 180 ? img.naturalWidth : img.naturalHeight;
      const s = Math.min(maxW / rw, maxH / rh);
      canvas.width = Math.round(rw * s);
      canvas.height = Math.round(rh * s);
      draw();
    }
    function draw() {
      const W = canvas.width, H = canvas.height;
      ctx.save();
      ctx.clearRect(0, 0, W, H);
      ctx.translate(W / 2, H / 2);
      ctx.rotate((rotate * Math.PI) / 180);
      const iw = rotate % 180 ? H : W, ih = rotate % 180 ? W : H;
      ctx.drawImage(img, -iw / 2, -ih / 2, iw, ih);
      ctx.restore();
      const [x0, y0, x1, y1] = [crop[0] * W, crop[1] * H, crop[2] * W, crop[3] * H];
      ctx.fillStyle = "rgba(0,0,0,.45)";
      ctx.fillRect(0, 0, W, y0); ctx.fillRect(0, y1, W, H - y1);
      ctx.fillRect(0, y0, x0, y1 - y0); ctx.fillRect(x1, y0, W - x1, y1 - y0);
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      ctx.fillStyle = "#2f6f5e";
      for (const [cx, cy] of [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) {
        ctx.beginPath(); ctx.arc(cx, cy, 11, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = "#fff"; ctx.stroke();
      }
    }
    function pos(e) {
      const r = canvas.getBoundingClientRect();
      return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
    }
    canvas.addEventListener("pointerdown", e => {
      const [px, py] = pos(e);
      const corners = [[crop[0], crop[1]], [crop[2], crop[1]], [crop[2], crop[3]], [crop[0], crop[3]]];
      let best = -1, bd = 0.09;
      corners.forEach(([cx, cy], i) => { const d = Math.hypot(cx - px, cy - py); if (d < bd) { bd = d; best = i; } });
      if (best >= 0) { drag = best; canvas.setPointerCapture(e.pointerId); e.preventDefault(); }
    });
    canvas.addEventListener("pointermove", e => {
      if (drag === null) return;
      const [px, py] = pos(e).map(v => Math.max(0, Math.min(1, v)));
      const m = 0.06;  // 最小裁剪尺寸
      if (drag === 0 || drag === 3) crop[0] = Math.min(px, crop[2] - m); else crop[2] = Math.max(px, crop[0] + m);
      if (drag === 0 || drag === 1) crop[1] = Math.min(py, crop[3] - m); else crop[3] = Math.max(py, crop[1] + m);
      draw();
    });
    const end = () => { drag = null; };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);

    // 旋转时把裁剪框跟着转，保持框住的是同一块纸面
    const rotCrop = (c, dir) => dir > 0 ? [1 - c[3], c[0], 1 - c[1], c[2]] : [c[1], 1 - c[2], c[3], 1 - c[0]];
    dlg.addEventListener("click", e => {
      const a = e.target.closest("button")?.dataset.a;
      if (!a) return;
      if (a === "left" || a === "right") {
        const dir = a === "right" ? 1 : -1;
        rotate = (rotate + 90 * dir + 360) % 360;
        crop = rotCrop(crop, dir);
        layout();
      } else if (a === "reset") { rotate = 0; crop = [0, 0, 1, 1]; layout(); }
      else if (a === "cancel") { finish(null); }
      else if (a === "ok") {
        const r = v => Math.round(v * 1000) / 1000;
        finish({ rotate, crop: crop.map(r) });
      }
    });
    dlg.addEventListener("cancel", e => { e.preventDefault(); finish(null); });
    function finish(v) { dlg.close(); dlg.remove(); window.removeEventListener("resize", layout); resolve(v); }
    window.addEventListener("resize", layout);
    img.onload = () => { dlg.showModal(); layout(); };
    img.onerror = () => { finish(null); };
    img.src = url;
    void esc;
  });
}

// 预览：按编辑参数在缩略图上显示旋转与裁剪效果（只用于显示）
export function editLabel(e) {
  if (!e) return "";
  const parts = [];
  if (e.rotate) parts.push(`转${e.rotate}°`);
  if (e.crop && e.crop.join() !== "0,0,1,1") parts.push("已裁剪");
  return parts.join(" · ");
}
