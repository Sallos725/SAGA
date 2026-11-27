const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../packs/risu-starter/saga_provider.js"), "utf8");
const sandbox = { console: { warn() {}, error() {} }, URL, TextEncoder, TextDecoder, ReadableStream, crypto: require("node:crypto").webcrypto, btoa: value => Buffer.from(value, "binary").toString("base64"), setTimeout, clearTimeout };
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
    saga: { schema_version: 3, identity_version: 2, legacy_read_only: false,
      event_id: "event-1", seq: 1, replayed: false,
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
  assert.match(response.content, /opponent<\/b> 22\/30/);
  assert.match(response.content, /1d20 → \[12\] = 12/);
  assert.match(response.content, /event-1/);
  assert.match(response.content, /^Resolved attack\.\n\n<div style="[^"]*font-size:[^"]*">/);
  assert.doesNotMatch(response.content, /\n---\n/);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].networkRoute, "local_network");
  assert.equal(f.calls[0].url, "http://saga-runtime:4850/v1/chat/completions");
  assert.deepEqual(f.calls[0].body.saga, { schema_version: 3, host_instance_id: "pocketrisu-test-uuid-1",
    chat_id: "chat-1", actor_id: "player", purpose: "action", actions: [{ message_id: "u1", text: "attack opponent", context_hidden: false }] });
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

test("context-hidden user history is retained and only comments are excluded", async () => {
  const f = fixture();
  f.character.chats[0].message.unshift(
    { chatId: "old", role: "user", data: "uncommitted old command" },
    { chatId: "cutoff", role: "char", data: "", disabled: "allBefore" },
    { chatId: "comment", role: "user", data: "comment", isComment: true });
  assert.equal((await f.send(await f.prepare())).success, true);
  assert.deepEqual(f.calls[0].body.saga.actions, [
    { message_id: "old", text: "uncommitted old command", context_hidden: true },
    { message_id: "u1", text: "attack opponent", context_hidden: false },
  ]);
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
  assert.deepEqual(f.calls[0].body.saga.actions, [{ message_id: "u1", text: "*says nothing*", context_hidden: false }]);
});

test("individually hidden draft is sent for backend review, never silently omitted", async () => {
  const f = fixture();
  f.character.chats[0].message.unshift({ chatId: "draft", role: "user", data: "attack opponent", disabled: true });
  f.reply = async () => ({ ok: false, status: 409, json: async () => ({ detail: "Hidden history has no matching committed prefix" }) });
  const result = await f.send(await f.prepare());
  assert.equal(result.success, false);
  assert.match(result.content, /Hidden history/);
  assert.equal(f.calls[0].body.saga.actions[0].message_id, "draft");
  assert.equal(f.calls[0].body.saga.actions[0].context_hidden, true);
});

test("hidden latest user turn cannot dispatch an earlier active turn", async () => {
  const f = fixture();
  f.character.chats[0].message.push({ chatId: "draft", role: "user", data: "attack opponent", disabled: true });
  const result = await f.send(await f.prepare());
  assert.equal(result.success, false);
  assert.match(result.content, /숨겨져/);
  assert.equal(f.calls.length, 0);
});

test("protocol 3 plugin preserves raw legacy text and shows read-only legacy response", async () => {
  const f = fixture();
  f.character.chats[0].message[0].data = "Attack  opponent";
  f.prompt[0].content = "Attack  opponent";
  const response = await f.reply();
  const data = await response.json();
  data.saga.identity_version = 1;
  data.saga.legacy_read_only = true;
  f.reply = async () => ({ ...response, json: async () => data });
  const result = await f.send(await f.prepare());
  assert.equal(result.success, true);
  assert.match(result.content, /읽기 전용/);
  assert.equal(f.calls[0].body.saga.actions[0].text, "Attack  opponent");
});

test("blank credentials keep fallback with no credential header or narration config", async () => {
  for (const value of [undefined, "", "   "]) {
    const f = fixture();
    f.args.vertex_service_account_json = value;
    f.api.runLLMModel = async () => { throw new Error("must never use a host model"); };
    assert.equal((await f.send(await f.prepare())).success, true);
    assert.equal(f.calls[0].headers["x-saga-vertex-secret"], undefined);
    assert.equal(f.calls[0].body.narration, undefined);
    assert.equal(f.calls[0].requestTimeoutMs, 15000);
  }
});

test("one JSON input travels only in the masked header with clean context", async () => {
  const f = fixture();
  const secret = JSON.stringify({ private_key: "SYNTHETIC SECRET", project_id: "test-project", note: "한글" });
  f.args.vertex_service_account_json = secret;
  f.character.chats[0].message.unshift({ chatId: "hidden", role: "user", data: "HIDDEN RAW", disabled: true });
  f.prompt.unshift({ role: "system", content: "Card: a misty arena.", metadata: "PRIVATE METADATA" },
    { role: "assistant", content: "Earlier prose." },
    { role: "user", content: [{ type: "image", data: "IMAGE CONTENT" }] },
    { role: "system", content: "Merged instruction\nSAGA request ticket: old-ticket" });
  const result = await f.send(await f.prepare());
  assert.equal(result.success, true);
  assert.equal(f.calls.length, 1);
  assert.equal(Buffer.from(f.calls[0].headers["x-saga-vertex-secret"], "base64").toString("utf8"), secret);
  assert.equal(f.calls[0].requestTimeoutMs, 65000);
  assert.equal(f.calls[0].body.narration.schema_version, 1);
  assert.equal(f.calls[0].body.narration.provider, "vertex");
  const context = JSON.stringify(f.calls[0].body.narration.context);
  assert.match(context, /misty arena/);
  assert.match(context, /Earlier prose/);
  for (const excluded of ["SYNTHETIC SECRET", "SAGA request ticket:", "test-uuid", "HIDDEN RAW", "PRIVATE METADATA", "IMAGE CONTENT"]) {
    assert.equal(context.includes(excluded), false, excluded);
  }
  assert.doesNotMatch(JSON.stringify(f.calls[0].body), /SYNTHETIC SECRET/);
  assert.doesNotMatch(result.content, /SYNTHETIC SECRET/);
});

test("narration context is bounded independently from full action history", async () => {
  const f = fixture();
  f.args.vertex_service_account_json = "{}";
  f.prompt.unshift(...Array.from({ length: 12 }, (_, i) => ({ role: "system", content: "system" + i + "x".repeat(6000) })),
    ...Array.from({ length: 40 }, (_, i) => ({ role: "assistant", content: "prose" + i + "x".repeat(6000) })));
  await f.send(await f.prepare());
  assert.equal(f.calls[0].body.narration.context.length, 32);
  assert.ok(f.calls[0].body.narration.context.every(line => line.length <= 4000));
  assert.equal(f.calls[0].body.saga.actions.length, 1);
});

test("credential changes invalidate a ticket or suppress an in-flight result", async () => {
  const f = fixture();
  f.args.vertex_service_account_json = "first secret";
  const prompt = await f.prepare();
  f.args.vertex_service_account_json = "second secret";
  assert.equal((await f.send(prompt)).success, false);
  assert.equal(f.calls.length, 0);
  const reply = f.reply;
  f.reply = async () => { f.args.vertex_service_account_json = "third secret"; return reply(); };
  assert.equal((await f.send(await f.prepare())).success, false);
  assert.equal(f.calls.length, 1);
});

test("narration and failure fallback retain the independent authoritative status", async () => {
  const f = fixture();
  f.args.vertex_service_account_json = "{}";
  const response = await f.reply();
  const data = await response.json();
  data.saga.replayed = true;
  let prose = "Fresh narration.";
  f.reply = async () => ({ ...response, json: async () => ({ ...data, choices: [{ message: { content: prose } }] }) });
  const first = await f.send(await f.prepare());
  prose = "Recorded facts. 모델 서술에 실패하여 확정된 기본 서술을 표시합니다.";
  const second = await f.send(await f.prepare());
  assert.equal(first.success, true);
  assert.equal(second.success, true);
  assert.notEqual(first.content, second.content);
  const footerOf = content => { const at = content.indexOf("<div"); assert.ok(at > 0); return content.slice(at); };
  assert.equal(footerOf(first.content), footerOf(second.content));
  assert.match(footerOf(first.content), /opponent<\/b> 22\/30/);
  assert.match(footerOf(first.content), /1d20 → \[12\] = 12/);
  assert.match(footerOf(first.content), /event-1/);
  assert.deepEqual(f.calls[0].body, f.calls[1].body);
});

test("invalid JSON error is safe and oversized input never dispatches", async () => {
  const f = fixture();
  f.args.vertex_service_account_json = "not-json";
  f.reply = async () => ({ ok: false, status: 422, json: async () => ({ detail: "Vertex 서비스 계정 JSON을 확인하세요." }) });
  const response = await f.send(await f.prepare());
  assert.equal(response.success, false);
  assert.match(response.content, /JSON을 확인/);
  assert.doesNotMatch(response.content, /not-json/);
  f.args.vertex_service_account_json = "x".repeat(16001);
  assert.equal((await f.send(await f.prepare())).success, false);
  assert.equal(f.calls.length, 1);
});

test("unload during asynchronous final config check suppresses the committed reply", async () => {
  const f = fixture();
  const getArgument = f.api.getArgument;
  let unload = false;
  f.api.getArgument = async name => {
    if (unload && name === "vertex_service_account_json") f.client.dispose();
    return getArgument(name);
  };
  const reply = f.reply;
  f.reply = async () => { unload = true; return reply(); };
  assert.equal((await f.send(await f.prepare())).success, false);
});


test("natural language keeps raw action identity and accepts protocol 3 results", async () => {
  const f = fixture();
  f.args.vertex_service_account_json = "{}";
  f.character.chats[0].message[0].data = "상대를 공격한다";
  f.prompt[0].content = "상대를 공격한다";
  const response = await f.reply();
  const data = await response.json();
  data.saga.identity_version = 3;
  f.reply = async () => ({ ...response, json: async () => data });
  const result = await f.send(await f.prepare());
  assert.equal(result.success, true);
  assert.deepEqual(f.calls[0].body.saga.actions, [{ message_id: "u1", text: "상대를 공격한다", context_hidden: false }]);
  assert.equal(f.calls[0].body.saga.schema_version, 3);
  assert.equal(f.calls[0].requestTimeoutMs, 65000);
  assert.equal(f.calls.length, 1);
});

test("status footer is one compact block with conditions, time, inventory and escaping", async () => {
  const f = fixture();
  f.reply = async () => ({ ok: true, status: 200, json: async () => ({
    choices: [{ message: { content: "Prose." } }],
    saga: { schema_version: 3, identity_version: 2, legacy_read_only: false,
      event_id: "event-9", seq: 4, replayed: true, rolls: [],
      state: { simulated_time: 3725, actors: {
        player: { actor_id: "player", hp: 13, max_hp: 20, conditions: [], inventory: { "silver_key": 1, "torch": 3 } },
        "<x>": { actor_id: "<x>", hp: 0, max_hp: 30, conditions: ["prone", "unconscious"], inventory: {} } } } },
  }) });
  const response = await f.send(await f.prepare());
  assert.equal(response.success, true);
  const footer = response.content.slice(response.content.indexOf("<div"));
  assert.equal((footer.match(/<div/g) || []).length, 1);
  assert.match(footer, /player<\/b> 13\/20/);
  assert.match(footer, /silver_key×1, torch×3/);
  assert.match(footer, /&lt;x&gt;<\/b> 0\/30 <i>prone, unconscious<\/i>/);
  assert.match(footer, /1h 2m 5s/);
  assert.match(footer, /주사위 없음/);
  assert.match(footer, /턴 4/);
  assert.match(footer, /기록 재사용/);
  assert.doesNotMatch(footer, /<x>/);
});

test("an enabled module's rulebook entry is attached to the turn; others are ignored", async () => {
  const f = fixture();
  const rulebook = "{\"schema_version\": 1}";
  const digest = require("node:crypto").createHash("sha256").update(rulebook, "utf8").digest("hex");
  f.api.getCurrentLorebookEntries = () => [
    { comment: "saga:narration:fate-condensed@0.2.0", content: "narration", alwaysActive: true },
    { comment: "saga:rulebook:fate-condensed@0.2.0:" + "0".repeat(64), content: "tampered" },
    { comment: "saga:rulebook:fate-condensed@0.2.0:" + digest, content: rulebook },
    { comment: "unrelated", content: "x" },
  ];
  await f.send(await f.prepare());
  assert.deepEqual(f.calls[0].body.saga.rulebook, { content: rulebook });
});

test("without a rulebook entry the turn carries no attachment", async () => {
  const f = fixture();
  f.api.getCurrentLorebookEntries = () => [{ comment: "unrelated", content: "x" }];
  await f.send(await f.prepare());
  assert.equal("rulebook" in f.calls[0].body.saga, false);
  delete f.api.getCurrentLorebookEntries;
  await f.send(await f.prepare());
  assert.equal("rulebook" in f.calls[1].body.saga, false);
});

test("two different enabled rulebook modules are an error, the same one twice is fine", async () => {
  const f = fixture();
  const hash = text => require("node:crypto").createHash("sha256").update(text, "utf8").digest("hex");
  const a = "{\"schema_version\": 1, \"id\": \"a\"}", b = "{\"schema_version\": 1, \"id\": \"b\"}";
  f.api.getCurrentLorebookEntries = () => [
    { comment: "saga:rulebook:a@1.0.0:" + hash(a), content: a },
    { comment: "saga:rulebook:a@1.0.0:" + hash(a), content: a },
  ];
  await f.send(await f.prepare());
  assert.deepEqual(f.calls[0].body.saga.rulebook, { content: a });
  f.api.getCurrentLorebookEntries = () => [
    { comment: "saga:rulebook:a@1.0.0:" + hash(a), content: a },
    { comment: "saga:rulebook:b@1.0.0:" + hash(b), content: b },
  ];
  const response = await f.send(await f.prepare());
  assert.equal(response.success, false);
  assert.match(response.content, /a@1\.0\.0.*b@1\.0\.0|b@1\.0\.0.*a@1\.0\.0/);
  assert.equal(f.calls.length, 1);
});

test("footer prints the backend's display lines when present and falls back otherwise", async () => {
  const f = fixture();
  const base = await (await f.reply()).json();
  f.reply = async () => ({ ok: true, status: 200, json: async () => ({ ...base, saga: { ...base.saga,
    display: { actors: [{ id: "player", items: ["athletics +1, notice +3"] }],
      records: [{ label: "fate_aspects", items: ["Mapped <route> (owner_id player, free_invokes 2)"] }],
      world: [] } } }) });
  const response = await f.send(await f.prepare());
  const footer = response.content.slice(response.content.indexOf("<div"));
  assert.match(footer, /<b>player<\/b> athletics \+1, notice \+3/);
  assert.match(footer, /<b>fate_aspects<\/b> Mapped &lt;route&gt; \(owner_id player, free_invokes 2\)/);
  assert.doesNotMatch(footer, /20\/20|⏱|🎒/);
  f.reply = async () => ({ ok: true, status: 200, json: async () => base });
  const legacy = await f.send(await f.prepare());
  assert.match(legacy.content, /opponent<\/b> 22\/30/);
});

// --- M1 Slice 3: Hook mode (RisuAI main/aux models) & v4 SSE Provider streaming --------------

function hookFixture() {
  const f = fixture();
  f.args.integration_mode = "hook";
  f.reply = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      saga: {
        schema_version: 4,
        identity_version: 3,
        legacy_read_only: false,
        event_id: "event-hook-1",
        seq: 1,
        replayed: false,
        rolls: [],
        state: {
          actors: {
            player: { actor_id: "player", hp: 20, max_hp: 20 },
            opponent: { actor_id: "opponent", hp: 30, max_hp: 30 },
          },
        },
        display: { actors: [{ id: "player", items: ["20/20"] }], records: [], world: ["⏱ 0s"] },
        prompt_block: "[SAGA Authoritative State — Read-Only]\nTurn event: narrative_turn",
      },
    }),
  });
  return f;
}

test("hook mode stays inactive on ordinary chats without a SAGA module or manual toggle", async () => {
  const f = hookFixture();
  const out = await f.prepare();
  assert.deepEqual(out, f.prompt);
  assert.equal(f.calls.length, 0);
  const reply = await f.client.afterRequest("Ordinary RP reply.", "model");
  assert.equal(reply, "Ordinary RP reply.");
});

test("hook mode auto-activates when a SAGA rulebook module is enabled and injects prompt + footer", async () => {
  const f = hookFixture();
  const rulebook = "{\"schema_version\": 1}";
  const digest = require("node:crypto").createHash("sha256").update(rulebook, "utf8").digest("hex");
  f.api.getCurrentLorebookEntries = () => [
    { comment: "saga:rulebook:srd-5.2.1@0.6.0:" + digest, content: rulebook },
  ];
  const prompt = await f.prepare();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, "http://saga-runtime:4850/v1/saga/turn");
  assert.equal(f.calls[0].body.saga.schema_version, 4);
  assert.deepEqual(f.calls[0].body.saga.rulebook, { content: rulebook });
  const injected = prompt.at(-1);
  assert.equal(injected.role, "system");
  assert.match(injected.content, /\[SAGA Hook Context\]/);
  assert.match(injected.content, /Turn event: narrative_turn/);

  const finalText = await f.client.afterRequest("Main model prose.", "model");
  assert.match(finalText, /^Main model prose\.\n\n<div /);
  assert.match(finalText, /event-hook-1/);
  assert.match(finalText, /주사위 없음/);
  // Auxiliary mode afterRequest is untouched.
  assert.equal(await f.client.afterRequest("Summary", "submodel"), "Summary");
});

test("hook mode per-chat manual toggle activates only the toggled chat (default OFF)", async () => {
  const f = hookFixture();
  assert.equal(await f.client.isChatHookEnabled("chat-1"), false);
  const toggled = await f.client.toggleChatHook();
  assert.deepEqual(clone(toggled), { chatId: "chat-1", enabled: true });
  assert.equal(await f.client.isChatHookEnabled("chat-1"), true);
  assert.equal(await f.client.isChatHookEnabled("chat-2"), false);

  const prompt = await f.prepare();
  assert.equal(f.calls.length, 1);
  assert.match(prompt.at(-1).content, /\[SAGA Hook Context\]/);

  // Switching to another chat keeps hook OFF.
  f.character.chats[0].id = "chat-2";
  const other = await f.prepare();
  assert.deepEqual(other, f.prompt);
  assert.equal(f.calls.length, 1);
});

test("hook mode fails closed when the SAGA backend returns an error or is unreachable", async () => {
  const f = hookFixture();
  await f.client.setChatHookEnabled("chat-1", true);
  f.reply = async () => ({
    ok: false,
    status: 503,
    json: async () => ({ detail: "backend unavailable" }),
  });
  await assert.rejects(() => f.prepare(), /SAGA 판정 실패 \(503\)/);

  f.reply = async () => { throw new Error("ECONNREFUSED"); };
  await assert.rejects(() => f.prepare(), /ECONNREFUSED/);
});

test("hook mode optionally queries RisuAI submodel via runLLMModel (with intent-context) and forwards client_proposal to SAGA", async () => {
  const f = hookFixture();
  f.args.use_risu_aux_intent = "true";
  await f.client.setChatHookEnabled("chat-1", true);
  f.character.chats[0].message[0].data = "상대를 검으로 벤다";
  f.prompt = [{ role: "user", content: "상대를 검으로 벤다", memo: "u1" }];
  const baseReply = f.reply;
  f.reply = async () => {
    const url = f.calls.at(-1)?.url || "";
    if (url.endsWith("/v1/saga/intent-context")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          already_committed: false,
          is_command: false,
          system_instruction: "GROUNDED_SYS",
          context: { text: "상대를 검으로 벤다", actors: ["opponent", "player"], exits: [] },
        }),
      };
    }
    return baseReply();
  };
  const auxCalls = [];
  f.api.runLLMModel = async opts => {
    auxCalls.push(opts);
    return {
      type: "streaming",
      result: new ReadableStream({
        start(controller) {
          controller.enqueue({ 0: '{"kind": "attack",' });
          controller.enqueue({ 0: '{"kind": "attack", "payload": {"target_id": "opponent", "advantage": false, "disadvantage": false}}' });
          controller.close();
        },
      }),
    };
  };
  await f.prepare();
  assert.equal(auxCalls.length, 1);
  assert.equal(auxCalls[0].mode, "submodel");
  assert.equal(auxCalls[0].allowPlugins, true);
  assert.equal(auxCalls[0].messages[0].content, "GROUNDED_SYS");
  assert.equal(f.calls.length, 2);
  assert.ok(f.calls[0].url.endsWith("/v1/saga/intent-context"));
  assert.ok(f.calls[1].url.endsWith("/v1/saga/turn"));
  assert.deepEqual(f.calls[1].body.client_proposal, {
    kind: "attack",
    payload: { target_id: "opponent", advantage: false, disadvantage: false },
  });
});

test("hook mode skips RisuAI submodel on reroll (already_committed) and explicit commands", async () => {
  const f = hookFixture();
  await f.client.setChatHookEnabled("chat-1", true);
  await f.client.saveUiSettings({ integrationMode: "hook", intentMode: "risu_aux" });
  let auxCount = 0;
  f.api.runLLMModel = async () => {
    auxCount++;
    return { type: "success", result: '{"kind":"narrative_turn","payload":{}}' };
  };
  const baseReply = f.reply;
  f.reply = async () => {
    const url = f.calls.at(-1)?.url || "";
    if (url.endsWith("/v1/saga/intent-context")) {
      return { ok: true, status: 200, json: async () => ({ already_committed: true, is_command: false }) };
    }
    return baseReply();
  };
  await f.prepare();
  assert.equal(auxCount, 0);
  assert.equal("client_proposal" in f.calls[1].body, false);
});

test("hook mode custom_llm forwards intent_llm override and x-saga-llm-api-key header, and compacts long event_id in footer", async () => {
  const f = hookFixture();
  await f.client.setChatHookEnabled("chat-1", true);
  await f.client.saveUiSettings({
    integrationMode: "hook",
    intentMode: "custom_llm",
    intentLlm: {
      base_url: "https://openrouter.ai/api/v1",
      model: "google/gemini-2.5-flash",
      api_key: "sk-test-header-only",
      reasoning_effort: "low",
      custom_headers: '{"X-Title":"SAGA"}',
      custom_body: '{"temperature":0}',
    },
  });
  const longId = "risu-" + "a".repeat(64);
  const baseReply = f.reply;
  f.reply = async () => {
    const res = await baseReply();
    const data = await res.json();
    data.saga.event_id = longId;
    return { ok: true, status: 200, json: async () => data };
  };
  await f.prepare();
  assert.equal(f.calls.length, 1);
  assert.ok(f.calls[0].url.endsWith("/v1/saga/turn"));
  assert.equal(f.calls[0].headers["x-saga-llm-api-key"], "sk-test-header-only");
  assert.equal("api_key" in f.calls[0].body.intent_llm, false);
  assert.deepEqual(f.calls[0].body.intent_llm, {
    base_url: "https://openrouter.ai/api/v1",
    model: "google/gemini-2.5-flash",
    reasoning_effort: "low",
    custom_headers: { "X-Title": "SAGA" },
    custom_body: { temperature: 0 },
  });
  const rendered = await f.client.afterRequest("검을 휘둘렀다.", "model");
  assert.match(rendered, new RegExp('title="' + longId + '"'));
  assert.match(rendered, /word-break:break-all/);
  assert.match(rendered, />risu-aaaaaaaaaaa…<\/code>/);

  // Next turn's outgoing prompt strips the previous SAGA footer so the main LLM does not mimic it.
  f.character.chats[0].message.push(
    { chatId: "a1", role: "char", data: rendered },
    { chatId: "u2", role: "user", data: "북쪽으로 이동한다" },
  );
  f.prompt = [
    { role: "user", content: "attack opponent", memo: "u1" },
    { role: "assistant", content: rendered },
    { role: "user", content: "북쪽으로 이동한다", memo: "u2" },
  ];
  const nextOut = await f.prepare();
  const prevAssistant = nextOut.find(m => m.role === "assistant");
  assert.equal(prevAssistant.content, "검을 휘둘렀다.");
});

test("provider mode v4 streams SSE chunks with risu_prompt and appends authoritative footer", async () => {
  const f = fixture();
  f.args.provider_stream = "true";
  f.prompt = [
    { role: "system", content: "Preset system instructions" },
    { role: "user", content: "attack opponent", memo: "u1" },
  ];
  const sseText =
    'data: {"choices":[{"delta":{"content":"검이 "}}]}\n\n' +
    'data: {"choices":[{"delta":{"content":"번쩍인다."}}]}\n\n' +
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"saga":{"schema_version":4,"identity_version":2,"legacy_read_only":false,"event_id":"event-s1","seq":1,"replayed":false,"rolls":[{"expression":"1d20","raw":[15],"total":15}],"state":{"actors":{"player":{"actor_id":"player","hp":20,"max_hp":20},"opponent":{"actor_id":"opponent","hp":24,"max_hp":30}}}}}\n\n' +
    "data: [DONE]\n\n";
  f.reply = async () => ({
    ok: true,
    status: 200,
    headers: { get: name => (name.toLowerCase() === "content-type" ? "text/event-stream" : null) },
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(sseText));
        controller.close();
      },
    }),
  });

  const result = await f.send(await f.prepare());
  assert.equal(result.success, true);
  assert.equal(f.calls[0].body.stream, true);
  assert.equal(f.calls[0].body.saga.schema_version, 4);
  assert.deepEqual(f.calls[0].body.risu_prompt, [
    { role: "system", content: "Preset system instructions" },
    { role: "user", content: "attack opponent" },
  ]);
  const reader = result.content.getReader();
  const chunks = [];
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    chunks.push(value);
  }
  const full = chunks.join("");
  assert.match(full, /^검이 번쩍인다\.\n\n<div /);
  assert.match(full, /1d20 → \[15\] = 15/);
  assert.match(full, /event-s1/);
});

test("UI settings saved in pluginStorage override plugin args and checkHealth queries /health", async () => {
  const f = hookFixture();
  delete f.args.integration_mode;
  assert.equal((await f.client.config()).integrationMode, "provider");
  await f.client.saveUiSettings({
    integrationMode: "hook",
    serverUrl: "http://127.0.0.1:4852",
    useAuxIntent: true,
    providerStream: true,
  });
  const cfg = await f.client.config();
  assert.equal(cfg.integrationMode, "hook");
  assert.equal(cfg.base, "http://127.0.0.1:4852");
  assert.equal(cfg.useAuxIntent, true);
  assert.equal(cfg.providerStream, true);

  let healthCall = null;
  f.api.nativeFetch = async (url, options) => {
    healthCall = { url, options };
    return { ok: true, status: 200 };
  };
  const health = await f.client.checkHealth();
  assert.deepEqual(clone(health), { ok: true, base: "http://127.0.0.1:4852" });
  assert.equal(healthCall.url, "http://127.0.0.1:4852/health");
  assert.equal(healthCall.options.method, "GET");
});

test("plugin install registers chat button and settings entry with inline SVG html icon", async () => {
  const registered = { settings: [], buttons: [], replacers: [], providers: [] };
  const fakeRisu = {
    onUnload: async () => {},
    addRisuReplacer: async (type, fn) => registered.replacers.push(type),
    removeRisuReplacer: async () => {},
    addProvider: async (name, fn) => registered.providers.push(name),
    registerSetting: async (name, fn, icon, iconType) => registered.settings.push({ name, icon, iconType }),
    registerButton: async (opts, fn) => registered.buttons.push(opts),
  };
  const fresh = {
    console: { warn() {}, error() {} },
    URL, TextEncoder, TextDecoder, ReadableStream,
    crypto: require("node:crypto").webcrypto,
    btoa: value => Buffer.from(value, "binary").toString("base64"),
    setTimeout, clearTimeout,
    risuai: fakeRisu,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../packs/risu-starter/saga_provider.js"), "utf8"), fresh);
  for (let i = 0; i < 20 && registered.buttons.length === 0; i++) await new Promise(r => setImmediate(r));
  assert.equal(registered.settings.length, 1);
  assert.equal(registered.settings[0].name, "SAGA");
  assert.equal(registered.settings[0].iconType, "html");
  assert.match(registered.settings[0].icon, /^<svg .*currentColor/);
  assert.equal(registered.buttons.length, 1);
  assert.equal(registered.buttons[0].location, "chat");
  assert.equal(registered.buttons[0].iconType, "html");
  assert.match(registered.buttons[0].icon, /^<svg .*currentColor/);
  assert.deepEqual(registered.replacers, ["beforeRequest", "afterRequest"]);
});

test("hook mode auto-activates for a SAGA scenario module, forwards saga.scenario, and renders B1 turn_card", async () => {
  const f = hookFixture();
  const scenario = '{"schema_version": 1, "id": "outpost"}';
  const digest = require("node:crypto").createHash("sha256").update(scenario, "utf8").digest("hex");
  f.api.getCurrentLorebookEntries = () => [
    { comment: "saga:scenario:outpost@0.1.0:" + digest, content: scenario },
  ];
  f.reply = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      saga: {
        schema_version: 4,
        identity_version: 3,
        legacy_read_only: false,
        event_id: "event-m3-1",
        seq: 1,
        replayed: false,
        rolls: [{ expression: "1d20", raw: [17], total: 17 }],
        state: {
          actors: {
            player: { actor_id: "player", hp: 20, max_hp: 20 },
            raider: { actor_id: "raider", hp: 8, max_hp: 14 },
          },
        },
        display: { actors: [{ id: "player", items: ["20/20"] }], records: [], world: ["📍 안뜰"] },
        prompt_block: "[SAGA Authoritative State — Read-Only]\nTurn event: attack",
        turn_card: {
          event_id: "event-m3-1",
          seq: 1,
          kind: "attack",
          actor_id: "player",
          rolls: [{ expression: "1d20", raw: [17], total: 17 }],
          resolution: { outcome: "hit", total: 21, target: 13, damage: 6, pending_adjudication: false },
          changes: ["actors.raider.hp: 14 → 8"],
          facts: ["hit"],
        },
        scene_board: {
          current_room: "전초기지 안뜰",
          exits: [{ key: "down", label: "지하 철문", locked: true, destination: "???" }],
          room_loot: { healing_herb: 1 },
          discovered_clues: {},
          participants: [{ actor_id: "player", is_player: true, hp: 20, max_hp: 20, conditions: [] }],
          quests: [{ quest_id: "black_tower", stage: 1, status: "active" }],
        },
      },
    }),
  });

  await f.prepare();
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.calls[0].body.saga.scenario, { content: scenario });
  assert.equal(f.client.lastState("chat-1").scene_board.current_room, "전초기지 안뜰");

  const finalText = await f.client.afterRequest("Prose.", "model");
  assert.match(finalText, /⚖ hit \(21 vs 13, 피해 6\)/);
  assert.match(finalText, /Δ actors\.raider\.hp: 14 → 8/);
});

test("A2 convertCurrentCard forwards LLM custom params and setChatScenario auto-activates Hook mode", async () => {
  const generatedScenario = {
    schema_version: 1,
    id: "abbey-card",
    version: "0.1.0",
    title: "안개 낀 수도원",
    actors: { player: { hp: 20, max_hp: 20, stats: {} } },
  };
  const f = hookFixture();
  f.reply = async () => {
    const url = f.calls.at(-1)?.url || "";
    if (url.endsWith("/v1/saga/convert")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ scenario: generatedScenario, mode: "llm", warnings: [] }),
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        saga: {
          schema_version: 4,
          identity_version: 3,
          legacy_read_only: false,
          event_id: "event-a2-1",
          seq: 1,
          replayed: false,
          rolls: [],
          state: { actors: { player: { actor_id: "player", hp: 20, max_hp: 20 } } },
          display: { actors: [{ id: "player", items: ["20/20"] }], records: [], world: [] },
          prompt_block: "[SAGA Authoritative State — Read-Only]",
        },
      }),
    };
  };

  const converted = await f.client.convertCurrentCard({
    mode: "backend",
    useLlm: true,
    llm: {
      base_url: "https://openrouter.ai/api/v1",
      model: "openai/gpt-5",
      reasoning_effort: "high",
      custom_headers: { "X-Title": "SAGA" },
      custom_body: { temperature: 0.2 },
    },
  });
  assert.equal(converted.mode, "llm");
  assert.equal(f.calls.length, 1);
  assert.ok(f.calls[0].url.endsWith("/v1/saga/convert"));
  assert.equal(f.calls[0].body.llm.reasoning_effort, "high");
  assert.deepEqual(clone(f.calls[0].body.llm.custom_headers), { "X-Title": "SAGA" });
  assert.deepEqual(clone(f.calls[0].body.llm.custom_body), { temperature: 0.2 });

  await f.client.setChatScenario("chat-1", JSON.stringify(generatedScenario));
  await f.prepare();
  assert.equal(f.calls.length, 2);
  assert.ok(f.calls[1].url.endsWith("/v1/saga/turn"));
  assert.deepEqual(JSON.parse(f.calls[1].body.saga.scenario.content), generatedScenario);
});
