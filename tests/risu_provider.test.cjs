const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../packs/risu-starter/saga_provider.js"), "utf8");
const sandbox = { console: { warn() {}, error() {} }, URL, setTimeout, clearTimeout };
vm.runInNewContext(source, sandbox);
const { createClient, snapshotOf } = sandbox.SagaRisu;
const clone = value => JSON.parse(JSON.stringify(value));

function fixture() {
  const state = {
    selection: "character-uuid", args: { server_url: "http://saga-runtime:4850" },
    character: { chatPage: 0, chats: [{ id: "chat-1", message: [
      { chatId: "u1", role: "user", data: "attack opponent" },
    ] }] },
    storage: new Map(), calls: [], counter: 0,
  };
  state.reply = async () => ({ ok: true, status: 200, json: async () => ({
    choices: [{ message: { content: "Resolved attack." } }],
    saga: { schema_version: 1, event_id: "event-1", seq: 1, replayed: false,
      rolls: [{ expression: "1d20", raw: [12], total: 12 }],
      state: { actors: { player: { actor_id: "player", hp: 20, max_hp: 20 },
        opponent: { actor_id: "opponent", hp: 22, max_hp: 30 } } } },
  }) });
  state.api = {
    getArgument: async name => state.args[name],
    getCurrentCharacterIndex: async () => state.selection,
    getCharacter: async () => clone(state.character),
    pluginStorage: { getItem: async key => state.storage.get(key), setItem: async (key, value) => state.storage.set(key, value) },
    nativeFetch: async (url, options) => {
      state.calls.push({ url, ...clone(options), body: JSON.parse(options.body) });
      return state.reply();
    },
  };
  state.client = createClient(state.api, () => "test-uuid-" + ++state.counter);
  state.prompt = [{ role: "user", content: "attack opponent", memo: "u1" }];
  state.prepare = async (mode = "model") => state.client.beforeRequest(state.prompt, mode);
  state.send = async prompt => state.client.provider({ mode: "v3", prompt_chat: prompt });
  return state;
}

test("provider forwards stable identity out of band and renders authoritative status", async () => {
  const f = fixture();
  const response = await f.send(await f.prepare());
  assert.equal(response.success, true);
  assert.match(response.content, /opponent HP 22\/30/);
  assert.match(response.content, /1d20 → \[12\] = 12/);
  assert.match(response.content, /event-1/);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].networkRoute, "local_network");
  assert.equal(f.calls[0].url, "http://saga-runtime:4850/v1/chat/completions");
  assert.deepEqual(f.calls[0].body.saga, { schema_version: 1, host_instance_id: "pocketrisu-test-uuid-1",
    chat_id: "chat-1", actor_id: "player", purpose: "action", actions: [{ message_id: "u1", text: "attack opponent" }] });
  assert.deepEqual(f.calls[0].body.messages, [{ role: "user", content: "attack opponent" }]);
});

test("regeneration and plugin reload retain identity; repeated text in a new message differs", async () => {
  const f = fixture();
  await f.send(await f.prepare());
  f.client = createClient(f.api, () => "next-uuid-" + ++f.counter);
  await f.send(await f.prepare());
  assert.deepEqual(f.calls[0].body, f.calls[1].body);
  f.character.chats[0].message.push({ chatId: "a1", role: "char", data: "regenerated text" },
    { chatId: "u2", role: "user", data: "attack opponent" });
  await f.send(await f.prepare());
  assert.deepEqual(f.calls[2].body.saga.actions.map(action => action.message_id), ["u1", "u2"]);
});

test("auxiliary modes, absent tickets, continuation and consumed tickets cannot dispatch", async () => {
  const f = fixture();
  for (const mode of ["otherAx", "submodel", "memory", "emotion", "v3"]) {
    assert.equal((await f.send(await f.prepare(mode))).success, false);
  }
  assert.equal(f.calls.length, 0);
  const prompt = await f.prepare();
  assert.equal((await f.send(prompt)).success, true);
  assert.equal((await f.send(prompt)).success, false);
  f.character.chats[0].message.push({ chatId: "a1", role: "char", data: "existing answer" });
  assert.equal((await f.send(await f.prepare())).success, false);
  assert.equal(f.calls.length, 1);
});

test("selection, swipe and config changes before dispatch fail closed", async () => {
  for (const mutate of [
    f => { f.selection = "other-character"; },
    f => { f.character.chats[0].id = "other-chat"; },
    f => { f.character.chats[0].message[0].data = "check strength 10"; },
    f => { f.args.host_instance_id = "changed-host"; },
  ]) {
    const f = fixture();
    const prompt = await f.prepare();
    mutate(f);
    assert.equal((await f.send(prompt)).success, false);
    assert.equal(f.calls.length, 0);
  }
});

test("changed in-flight snapshot suppresses the reply without retrying the request", async () => {
  const f = fixture();
  const response = f.reply;
  f.reply = async () => {
    f.character.chats[0].message[0].data = "edited while pending";
    return response();
  };
  const result = await f.send(await f.prepare());
  assert.equal(result.success, false);
  assert.match(result.content, /변경/);
  assert.equal(f.calls.length, 1);
});

test("network failure can be retried with a fresh ticket and identical event identity", async () => {
  const f = fixture();
  const response = f.reply;
  f.reply = async () => { throw new Error("transport failed after possible commit"); };
  assert.equal((await f.send(await f.prepare())).success, false);
  f.reply = response;
  assert.equal((await f.send(await f.prepare())).success, true);
  assert.deepEqual(f.calls[0].body, f.calls[1].body);
});

test("comments and disabled history never become user actions", async () => {
  const f = fixture();
  f.character.chats[0].message.unshift(
    { chatId: "old", role: "user", data: "uncommitted old command" },
    { chatId: "cutoff", role: "char", data: "", disabled: "allBefore" },
    { chatId: "comment", role: "user", data: "comment", isComment: true });
  assert.equal((await f.send(await f.prepare())).success, true);
  assert.deepEqual(f.calls[0].body.saga.actions, [{ message_id: "u1", text: "attack opponent" }]);
});

test("missing/duplicate host IDs and mismatched prompt cannot dispatch", async () => {
  for (const mutate of [
    f => { delete f.character.chats[0].message[0].chatId; },
    f => { f.character.chats[0].message.push(clone(f.character.chats[0].message[0])); },
    f => { f.prompt = [{ role: "user", content: "unrelated auxiliary request" }]; },
  ]) {
    const f = fixture();
    mutate(f);
    assert.equal((await f.send(await f.prepare())).success, false);
    assert.equal(f.calls.length, 0);
  }
  assert.throws(() => snapshotOf({ chats: [] }, "x"), /ID/);
});

test("cancellation and unloading suppress pending results and reject further dispatch", async () => {
  const f = fixture();
  const prompt = await f.prepare();
  assert.equal((await f.client.provider({ prompt_chat: prompt }, { aborted: true })).success, false);
  assert.equal(f.calls.length, 0);
  const next = await f.prepare();
  f.client.dispose();
  assert.equal((await f.send(next)).success, false);
  assert.equal(f.calls.length, 0);
});

test("overlapping provider requests do not consume a second action", async () => {
  const f = fixture();
  let release;
  let started;
  const entered = new Promise(resolve => { started = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const response = f.reply;
  f.reply = async () => { started(); await gate; return response(); };
  const first = f.send(await f.prepare());
  await entered;
  assert.equal((await f.send(await f.prepare())).success, false);
  release();
  assert.equal((await first).success, true);
  assert.equal(f.calls.length, 1);
});

test("server errors are visible and malformed results never display successful state", async () => {
  for (const reply of [
    async () => ({ ok: false, status: 409, json: async () => ({ detail: "Rewind edited history" }) }),
    async () => ({ ok: true, status: 200, json: async () => ({ choices: [] }) }),
  ]) {
    const f = fixture();
    f.reply = reply;
    assert.equal((await f.send(await f.prepare())).success, false);
    assert.equal(f.calls.length, 1);
  }
  // With the host's default useSayNothing enabled, Continue inserts a synthetic user turn.
  // The plugin forwards that host turn; the closed backend rejects it before game dispatch.
  const f = fixture();
  f.character.chats[0].message[0].data = "*says nothing*";
  f.prompt = [{ role: "user", content: "*says nothing*", memo: "u1" }];
  f.reply = async () => ({ ok: false, status: 422, json: async () => ({ detail: "Unsupported command" }) });
  const result = await f.send(await f.prepare());
  assert.equal(result.success, false);
  assert.match(result.content, /422/);
  assert.deepEqual(f.calls[0].body.saga.actions, [{ message_id: "u1", text: "*says nothing*" }]);
});
