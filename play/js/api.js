// API 封装：带 Bearer token 的 fetch、上传进度、受保护图片的 blob 读取。
// GitHub Pages 上的静态演示：mock/mock.js 先于本文件加载，设置这个标志并接管 /api/* 请求（回放录制好的合成数据）
export const STATIC_DEMO = !!window.EDU_STATIC_DEMO;

const TOKEN_KEY = "edu_eval_token";
const USER_KEY = "edu_eval_user";

function storeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storeSet(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* 私密模式等 */ } }

let memToken = storeGet(TOKEN_KEY);
let memUser = null;
try { memUser = JSON.parse(storeGet(USER_KEY) || "null"); } catch { memUser = null; }

export const auth = {
  get token() { return memToken; },
  get user() { return memUser; },
  set(token, user) { memToken = token; memUser = user; storeSet(TOKEN_KEY, token); storeSet(USER_KEY, user ? JSON.stringify(user) : null); },
  clear() { this.set(null, null); },
};

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function errMessage(data, status) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && Array.isArray(data.detail)) return data.detail.map(d => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("；");
  return `请求失败（${status}）`;
}

export async function api(path, { method = "GET", body, form, raw } = {}) {
  const headers = {};
  if (memToken) headers.Authorization = `Bearer ${memToken}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, "网络连接失败，请检查网络（手机需与服务器在同一局域网）");
  }
  if (res.status === 401 && !path.endsWith("/auth/login")) {
    auth.clear();
    location.hash = "#/login";
    throw new ApiError(401, "登录已失效，请重新登录");
  }
  if (raw) {
    if (!res.ok) throw new ApiError(res.status, `请求失败（${res.status}）`);
    return res;
  }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, errMessage(data, res.status));
  return data;
}

export async function login(username, password) {
  const form = new URLSearchParams({ username, password });
  const res = await fetch("/api/auth/login", { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, errMessage(data, res.status));
  auth.set(data.access_token, data.user);
  return data.user;
}

// 带进度的上传（fetch 不支持上传进度，用 XHR）
export function upload(path, formData, onProgress, timeoutMs = 180000) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.timeout = timeoutMs;
    if (memToken) xhr.setRequestHeader("Authorization", `Bearer ${memToken}`);
    xhr.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { data = null; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, errMessage(data, xhr.status)));
    };
    xhr.onerror = () => reject(new ApiError(0, "上传失败：网络中断"));
    xhr.ontimeout = () => reject(new ApiError(0, "上传超时：网络太慢"));
    xhr.send(formData);
  });
}

export function randomToken() {
  if (window.crypto?.randomUUID) { try { return crypto.randomUUID(); } catch { /* 非安全上下文 */ } }
  const a = new Uint8Array(16);
  (window.crypto || window.msCrypto).getRandomValues(a);
  return Array.from(a, b => b.toString(16).padStart(2, "0")).join("");
}

const RETRYABLE = s => s === 0 || s === 408 || s === 429 || s >= 500;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const waitOnline = () => (navigator.onLine === false ? new Promise(r => window.addEventListener("online", r, { once: true })) : null);

// 弱网上传：网络错误/超时/5xx 自动重试（退避 2s、4s、8s），离线时等联网后继续。
// 每次重试都带同一个 client_token，服务器据此去重，不会因为“其实已经传上去了但回包丢了”而建两份提交。
export async function uploadWithRetry(path, makeForm, { onProgress, onStatus, attempts = 4 } = {}) {
  let last;
  for (let i = 0; i < attempts; i++) {
    const w = waitOnline();
    if (w) { onStatus?.("当前离线，联网后会自动继续上传…"); await w; }
    try {
      if (i > 0) onStatus?.(`网络不稳定，正在第 ${i} 次重试…`);
      return await upload(path, makeForm(), onProgress);
    } catch (e) {
      last = e;
      if (!RETRYABLE(e.status) || i === attempts - 1) break;
      const delay = 2000 * 2 ** i;
      onStatus?.(`${e.message}，${delay / 1000} 秒后自动重试…`);
      await sleep(delay);
    }
  }
  throw last;
}

// 作业图片需要鉴权，不能直接用 <img src>；读成 blob URL
const blobCache = new Map();
export async function imageURL(path) {
  if (blobCache.has(path)) return blobCache.get(path);
  const res = await api(path, { raw: true });
  const url = URL.createObjectURL(await res.blob());
  blobCache.set(path, url);
  return url;
}
export function clearImageCache() {
  for (const u of blobCache.values()) URL.revokeObjectURL(u);
  blobCache.clear();
}
