#!/usr/bin/env node
// Drive the real PocketRisu plugin (packs/risu-starter/saga_provider.js) against a running
// SAGA backend with a mocked host. The plugin is evaluated with a `risuai` global, so its own
// bootstrap runs: the `beforeRequest` replacer and the provider it registers are what this
// script calls. An enabled module's lorebook comes from a risu-module.json and nativeFetch is
// Node's fetch. Only the PocketRisu UI itself (menus, import dialogs) is outside this check.
//
//   node scripts/smoke_plugin.cjs --base http://127.0.0.1:4852 \
//     --module packs/rulesets/fate-condensed/risu-module.json \
//     "overcome investigate 2" 'create_advantage notice 1 "Mapped route"' 'invoke "Mapped route"'
//
// Each argument is one user turn in one chat. A rejected turn is dropped from the chat (as a
// player would delete it); the last committed turn is regenerated once. The footer of every
// response is printed. Exit status is non-zero if any turn was rejected.

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");

const args = process.argv.slice(2);
const option = name => { const at = args.indexOf(name); return at >= 0 ? args.splice(at, 2)[1] : undefined; };
const base = option("--base") || "http://127.0.0.1:4852";
const modulePath = option("--module");
const instance = option("--instance") || "smoke-plugin-" + Date.now();
const turns = args.length ? args : ["attack opponent"];

const lorebook = modulePath ? JSON.parse(fs.readFileSync(modulePath, "utf8")).lorebook : [];
const character = { chatPage: 0, chats: [{ id: "chat-" + Date.now(), message: [] }] };
const storage = new Map();
// What the plugin registers with the host during its own install().
const hooks = { replacers: new Map(), providers: new Map(), unload: [] };
const api = {
  addRisuReplacer: async (kind, fn) => { hooks.replacers.set(kind, fn); },
  removeRisuReplacer: async kind => { hooks.replacers.delete(kind); },
  addProvider: async (name, fn) => { hooks.providers.set(name, fn); },
  onUnload: async fn => { hooks.unload.push(fn); },
  getArgument: async name => ({ server_url: base, host_instance_id: instance, vertex_service_account_json: "" })[name],
  getCurrentCharacterIndex: async () => "char-1",
  getCharacter: async () => JSON.parse(JSON.stringify(character)),
  getCurrentLorebookEntries: () => lorebook,
  pluginStorage: { getItem: async k => storage.get(k), setItem: async (k, v) => storage.set(k, v) },
  nativeFetch: async (url, options) => {
    const response = await fetch(url, { method: options.method, headers: options.headers, body: options.body });
    return { ok: response.ok, status: response.status, json: () => response.json() };
  },
};

const source = fs.readFileSync(path.join(__dirname, "../packs/risu-starter/saga_provider.js"), "utf8");
const sandbox = { console, URL, TextEncoder, crypto: crypto.webcrypto, btoa: v => Buffer.from(v, "binary").toString("base64"), setTimeout, clearTimeout, risuai: api };
vm.runInNewContext(source, sandbox);

async function installed() {
  // install() is fire-and-forget inside the plugin; give its awaits a tick to settle.
  for (let i = 0; i < 20 && !hooks.providers.has(sandbox.SagaRisu.PROVIDER); i++) await new Promise(r => setImmediate(r));
  const beforeRequest = hooks.replacers.get("beforeRequest");
  const provider = hooks.providers.get(sandbox.SagaRisu.PROVIDER);
  if (!beforeRequest || !provider) throw new Error("plugin did not register its beforeRequest replacer and provider");
  return { beforeRequest, provider };
}

async function send(label) {
  const { beforeRequest, provider } = await installed();
  const prompt = character.chats[0].message
    .filter(m => m.role === "user")
    .map(m => ({ role: "user", content: m.data, memo: m.chatId }));
  const prepared = await beforeRequest(prompt, "model");
  const response = await provider({ mode: "v3", prompt_chat: prepared });
  const footer = response.content.slice(response.content.indexOf("<div"));
  const text = footer.split("<br>").map(line =>
    line.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").trim()
  ).join("\n    ");
  console.log(`${response.success ? "OK " : "ERR"} ${label}: ${prompt.at(-1).content}`);
  console.log("    " + (response.success ? text : response.content.split("\n")[0]));
  return response;
}

(async () => {
  let failed = 0;
  for (let i = 0; i < turns.length; i++) {
    character.chats[0].message.push({ chatId: "u" + (i + 1), role: "user", data: turns[i] });
    const response = await send("turn " + (i + 1));
    if (!response.success) {
      // A rejected turn never commits; drop it as a player would delete the failed message.
      failed += 1;
      character.chats[0].message.pop();
      continue;
    }
    character.chats[0].message.push({ chatId: "a" + (i + 1), role: "char", data: response.content });
  }
  const messages = character.chats[0].message;
  if (messages.length && messages.at(-1).role === "char") {
    messages.pop();
    console.log("--- regenerate last committed turn");
    const again = await send("regenerate");
    if (!again.success || !/기록 재사용/.test(again.content)) failed += 1;
  }
  for (const fn of hooks.unload) await fn();
  if (hooks.replacers.size) { failed += 1; console.log("unload left a replacer registered"); }
  console.log(failed ? `${failed} turn(s) failed` : "all turns committed; plugin installed and unloaded cleanly");
  process.exit(failed ? 1 : 0);
})();
