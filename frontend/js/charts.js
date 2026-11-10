// Chart.js（本地 vendored 版本，window.Chart）的薄封装：统一配色、深色模式、销毁旧图。
const charts = new Map();

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
export const PALETTE = ["#2f6f5e", "#c9822b", "#4a6fa5", "#a0527a", "#6b8e23", "#8a6d3b"];

function base() {
  const text = css("--muted") || "#5f6d66";
  const grid = css("--border") || "#d8e0da";
  return { text, grid };
}

function make(canvas, config) {
  if (!window.Chart) {
    canvas.replaceWith(Object.assign(document.createElement("div"), { className: "empty", textContent: "图表库未加载" }));
    return null;
  }
  const old = charts.get(canvas.id);
  if (old) old.destroy();
  const c = new window.Chart(canvas, config);
  charts.set(canvas.id, c);
  return c;
}

export function destroyAll() { for (const c of charts.values()) c.destroy(); charts.clear(); }

export function lineChart(canvas, labels, datasets, { yMax = 100, ySuffix = "%" } = {}) {
  const { text, grid } = base();
  return make(canvas, {
    type: "line",
    data: { labels, datasets: datasets.map((d, i) => ({ borderColor: PALETTE[i % 6], backgroundColor: PALETTE[i % 6], tension: .25, spanGaps: true, pointRadius: 4, ...d })) },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: "nearest", intersect: false },
      scales: { y: { min: 0, max: yMax, ticks: { color: text, callback: v => v + ySuffix }, grid: { color: grid } }, x: { ticks: { color: text, maxRotation: 45 }, grid: { display: false } } },
      plugins: { legend: { labels: { color: text, boxWidth: 12 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}：${c.parsed.y}${ySuffix}` } } },
    },
  });
}

export function radarChart(canvas, labels, datasets) {
  const { text, grid } = base();
  return make(canvas, {
    type: "radar",
    data: { labels, datasets: datasets.map((d, i) => ({ borderColor: PALETTE[i % 6], backgroundColor: PALETTE[i % 6] + "33", pointRadius: 3, ...d })) },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { r: { min: 0, max: 100, ticks: { display: false, stepSize: 20 }, grid: { color: grid }, angleLines: { color: grid }, pointLabels: { color: text, font: { size: 12 } } } },
      plugins: { legend: { labels: { color: text, boxWidth: 12 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}：${c.parsed.r}%` } } },
    },
  });
}

export function barChart(canvas, labels, values, { horizontal = false, color = PALETTE[0], label = "", suffix = "" } = {}) {
  const { text, grid } = base();
  return make(canvas, {
    type: "bar",
    data: { labels, datasets: [{ label, data: values, backgroundColor: Array.isArray(color) ? color : color, borderRadius: 4 }] },
    options: {
      indexAxis: horizontal ? "y" : "x", responsive: true, maintainAspectRatio: false,
      scales: { x: { ticks: { color: text }, grid: { color: horizontal ? grid : "transparent" } }, y: { ticks: { color: text, precision: 0 }, grid: { color: horizontal ? "transparent" : grid } } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.parsed[horizontal ? "x" : "y"]}${suffix}` } } },
    },
  });
}
