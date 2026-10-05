// BlackLang Interop Bus · Node 宿主 runner
// 通过 stdio JSON-RPC 提供 load / get / call 三类操作。
// 对象存放在 node 进程内存，用整数 handle 引用；返回非 JSON 值时返回 {"$handle": id}
import readline from 'node:readline';

const objects = new Map();   // id -> value (module / fn / object)
let nextId = 1;

function wrapHandle(value) {
  const id = nextId++;
  objects.set(id, value);
  return { $handle: id };
}

function unwrap(value) {
  // 把 {"$handle": id} 还原成被引用的对象
  if (Array.isArray(value)) return value.map(unwrap);
  if (value && typeof value === 'object' && typeof value.$handle === 'number' && Object.keys(value).length === 1) {
    const id = value.$handle;
    if (!objects.has(id)) throw new Error(`Unknown handle ${id}`);
    return objects.get(id);
  }
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value)) out[k] = unwrap(value[k]);
    return out;
  }
  return value;
}

function isSerializable(value) {
  try { JSON.stringify(value); return true; } catch (e) { return false; }
}

function toWire(value) {
  // 把 node 值序列化为可返回 JSON 的形式；不可序列化的包装成 handle
  if (value === null || value === undefined) return value;
  if (typeof value === 'function') return wrapHandle(value);
  if (typeof value === 'object') {
    if (typeof value.$handle === 'number') return value;   // 已是引用
    if (Array.isArray(value)) return value.map(toWire);
    if (isSerializable(value)) {
      const out = {};
      for (const k of Object.keys(value)) out[k] = toWire(value[k]);
      return out;
    }
    return wrapHandle(value);   // 模块/类/实例
  }
  return value; // number/string/boolean
}

async function awaitImport(name) {
  // 先按原样导入；若失败，回退到 node: 内置模块前缀
  let m;
  try {
    m = await import(name);
  } catch (e) {
    m = await import('node:' + name);
  }
  return (m && typeof m === 'object' && 'default' in m) ? m.default : m;
}

const rl = readline.createInterface({ input: process.stdin });
rl.on('line', async (line) => {
  let req;
  try { req = JSON.parse(line); } catch (e) { return; }
  const respond = (payload) => {
    process.stdout.write(JSON.stringify({ id: req.id, ...payload }) + '\n');
  };
  try {
    const [method, params] = [req.method, req.params || []];
    if (method === 'load') {
      const mod = await awaitImport(params[0]);
      // 模块始终包成 handle，保持「远端代理」语义（避免展开成内联对象）
      respond({ result: wrapHandle(mod) });
    } else if (method === 'get') {
      const [handle, name] = params;
      const obj = objects.get(handle);
      if (obj == null) throw new Error(`Unknown handle ${handle}`);
      respond({ result: toWire(obj[name]) });
    } else if (method === 'call') {
      const [handle, rawArgs] = params;
      const obj = objects.get(handle);
      if (obj == null) throw new Error(`Unknown handle ${handle}`);
      const args = rawArgs.map(unwrap);
      respond({ result: toWire(obj(...args)) });
    } else {
      respond({ error: `Unknown method ${method}` });
    }
  } catch (e) {
    respond({ error: String((e && e.message) || e) });
  }
});