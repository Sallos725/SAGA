//@name saga_runtime
//@display-name SAGA SRD Demo
//@api 3.0
//@version 0.11.2
//@arg server_url string SAGA URL (default http://saga-runtime:4850)
//@arg host_instance_id string Stable host identity (set it explicitly; empty generates one via plugin storage, which hung on one host)
//@arg integration_mode string provider | hook (default: provider). hook uses RisuAI's own main model (presets & streaming) while SAGA adjudicates in beforeRequest/afterRequest.
//@arg provider_stream string false | true (default: false). In provider mode, stream SSE prose and forward risu_prompt using protocol v4.
//@arg use_risu_aux_intent string false | true (default: false). In hook mode, ask RisuAI's submodel (runLLMModel) to propose a grounded action/narrative_turn for SAGA validation.
//@arg vertex_service_account_json string {{name::Vertex 서비스 계정 JSON}}{{textarea}} JSON 전체를 붙여넣으세요. Gemini 3.8 Flash로 자연어 행동·서술; 비우면 명령·기본 서술.

(() => {
  "use strict";
  const VERSION = "0.11.2";
  const PROVIDER = "SAGA SRD Demo";
  const MARKER = "SAGA request ticket: ";
  const HOOK_MARKER = "[SAGA Hook Context]";
  const HOOK_CHATS_KEY = "saga:hook-chats:v1";
  const UI_SETTINGS_KEY = "saga:ui-settings:v1";
  const CHAT_SCENARIOS_KEY = "saga:chat-scenarios:v1";
  const SAGA_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 21 7 21 17 12 22 3 17 3 7 12 2"/><polyline points="3.3 7 12 12 20.7 7"/><line x1="12" y1="22" x2="12" y2="12"/></svg>';
  const HELP = "명령은 룰북의 행동 이름과 입력 순서를 따릅니다: attack opponent / check strength 10 / damage opponent 5 / give player torch 1, 또는 overcome investigate 2 처럼요. name=value로 지정하거나 따옴표로 묶을 수 있고, 판정 명령 뒤에 advantage/disadvantage를 붙일 수 있습니다. 잘못된 명령의 오류 메시지에 그 룰북의 전체 명령 목록이 나옵니다. SAGA 룰북 모듈을 켜고 새 채팅을 시작하면 그 룰북으로 플레이합니다. Vertex JSON 설정 시 SRD 데모에서 자연어 단일 행동도 지원합니다.";

  const SAGA_FOOTER_RE = /\n*<div[^>]*style="margin-top:0\.75em;padding-top:0\.4em;border-top:1px solid rgba\(128,128,128,0\.35\);[^"]*"[^>]*>[\s\S]*?<\/div>(?:\n\n기존 v1 기록은 읽기 전용입니다\. 새 행동은 새 채팅에서 시작하세요\.)?\s*$/g;
  function stripSagaFooter(text) {
    return typeof text === "string" ? text.replace(SAGA_FOOTER_RE, "") : text;
  }

  function narrationContext(prompt) {
    const text = prompt.filter(message => ["system", "user", "assistant"].includes(message?.role) &&
      typeof message.content === "string" && message.content.trim() && !message.content.includes(MARKER) && !message.content.includes(HOOK_MARKER));
    return [...text.filter(message => message.role === "system").slice(0, 8),
      ...text.filter(message => message.role !== "system").slice(-24)]
      .map(message => (message.role + ": " + (message.role === "assistant" ? stripSagaFooter(message.content) : message.content)).slice(0, 4000));
  }

  function risuPromptMessages(prompt) {
    if (!Array.isArray(prompt)) return [];
    return prompt
      .filter(message => ["system", "user", "assistant", "developer"].includes(message?.role) &&
        typeof message.content === "string" && !message.content.includes(MARKER) && !message.content.includes(HOOK_MARKER))
      .slice(-256)
      .map(message => ({
        role: message.role,
        content: (message.role === "assistant" ? stripSagaFooter(message.content) : message.content).slice(0, 32000),
      }));
  }

  function snapshotOf(character, selection) {
    const chat = character?.chats?.[character.chatPage];
    if (!chat || typeof chat.id !== "string" || !chat.id || !Array.isArray(chat.message)) {
      throw new Error("안정적인 채팅 ID가 필요합니다.");
    }
    const cutoff = chat.message.reduce((last, message, index) => message.disabled === "allBefore" ? index : last, -1);
    const seen = new Set();
    const messages = chat.message.map((message, index) => {
      if (!message || typeof message.chatId !== "string" || !message.chatId ||
          typeof message.data !== "string" || !["user", "char"].includes(message.role) || seen.has(message.chatId)) {
        throw new Error("메시지 ID가 없거나 중복되었습니다. 이 요청은 실행하지 않습니다.");
      }
      seen.add(message.chatId);
      return { id: message.chatId, role: message.role, text: message.data,
        disabled: !!message.disabled || index < cutoff, comment: !!message.isComment };
    });
    return { selection, chatId: chat.id, messages };
  }

  function randomId() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("");
  }

  // Read-only view of the committed response. SAGA owns every value here; the host only renders.
  const escapeHtml = value => String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  function formatTime(seconds) {
    if (!Number.isInteger(seconds) || seconds < 0) return null;
    const parts = [];
    const units = [["d", 86400], ["h", 3600], ["m", 60], ["s", 1]];
    let rest = seconds;
    for (const [label, size] of units) {
      const count = Math.floor(rest / size);
      if (count > 0 || (label === "s" && parts.length === 0)) parts.push(count + label);
      rest %= size;
    }
    return parts.join(" ");
  }
  // Lines come from the backend's rulebook-driven `display` when present (the host knows
  // nothing about stats or records); older backends fall back to HP/conditions/inventory.
  function displayLines(state) {
    const display = state.display;
    if (display && Array.isArray(display.actors)) {
      const group = (label, items) => "<b>" + escapeHtml(label) + "</b> " + items.map(escapeHtml).join(", ");
      const actors = display.actors.filter(a => a && typeof a.id === "string" && Array.isArray(a.items)).map(a => group(a.id, a.items));
      const records = (Array.isArray(display.records) ? display.records : []).filter(r => r && typeof r.label === "string" && Array.isArray(r.items)).map(r => group(r.label, r.items));
      const world = (Array.isArray(display.world) ? display.world : []).map(escapeHtml);
      return actors.concat(world, records);
    }
    const actors = Object.values(state.state.actors).map(actor => {
      const conditions = Array.isArray(actor.conditions) && actor.conditions.length ? " <i>" + escapeHtml(actor.conditions.join(", ")) + "</i>" : "";
      return "<b>" + escapeHtml(actor.actor_id) + "</b> " + escapeHtml(actor.hp) + "/" + escapeHtml(actor.max_hp) + conditions;
    });
    const player = state.state.actors.player;
    const items = player && player.inventory && typeof player.inventory === "object"
      ? Object.entries(player.inventory).map(([key, count]) => escapeHtml(key) + "×" + escapeHtml(count)) : [];
    const time = formatTime(state.state.simulated_time);
    return actors.concat(time === null ? [] : ["⏱ " + time], ["🎒 " + (items.length ? items.join(", ") : "—")]);
  }
  function compactEventId(eventId) {
    const raw = String(eventId ?? "");
    return raw.length > 24 ? raw.slice(0, 16) + "…" : raw;
  }
  function statusFooter(state) {
    const rolls = state.rolls.map(roll => escapeHtml(roll.expression) + " → [" + roll.raw.map(escapeHtml).join(", ") + "] = " + escapeHtml(roll.total));
    const line1 = displayLines(state).join(" · ");
    const card = state.turn_card;
    const cardParts = [];
    if (card?.resolution) {
      const r = card.resolution;
      cardParts.push("⚖ " + escapeHtml(r.outcome) + " (" + escapeHtml(r.total) + " vs " + escapeHtml(r.target) + (r.damage != null ? ", 피해 " + escapeHtml(r.damage) : "") + ")");
    }
    if (Array.isArray(card?.changes) && card.changes.length) {
      cardParts.push("Δ " + card.changes.slice(0, 4).map(escapeHtml).join(" · "));
    }
    const shortId = compactEventId(state.event_id);
    const codeTag = '<code title="' + escapeHtml(state.event_id) + '" style="display:inline;white-space:normal;word-break:break-all;overflow-wrap:anywhere">' +
      escapeHtml(shortId) + "</code>";
    const line2 = ["🎲 " + (rolls.length ? rolls.join(" · ") : "주사위 없음"), "턴 " + escapeHtml(state.seq) + (state.replayed ? " · 기록 재사용" : ""),
      codeTag].join(" · ");
    const lines = [line1].concat(cardParts.length ? [cardParts.join(" · ")] : [], [line2]);
    return '<div style="margin-top:0.75em;padding-top:0.4em;border-top:1px solid rgba(128,128,128,0.35);font-size:0.78em;line-height:1.6;opacity:0.85;width:100%;max-width:100%;min-width:0;overflow:hidden;overflow-wrap:anywhere;word-break:break-word;box-sizing:border-box">' +
      lines.join("<br>") + "</div>";
  }

  function validSagaState(state) {
    return state && [3, 4].includes(state.schema_version) && [1, 2, 3].includes(state.identity_version) &&
      typeof state.event_id === "string" && Array.isArray(state.rolls) && state.state?.actors;
  }

  // A rulebook carried by an enabled SAGA module: the lorebook entry tagged
  // saga:rulebook:<id>@<version>:<sha256>. Only its verified content is forwarded; the backend
  // validates it again and pins a new world to it. Existing worlds keep their rulebook.
  const RULEBOOK_TAG = /^saga:rulebook:([a-z0-9][a-z0-9.-]{0,60})@([0-9]+\.[0-9]+\.[0-9]+):([0-9a-f]{64})$/;
  const SCENARIO_TAG = /^saga:scenario:([a-z0-9][a-z0-9.-]{0,60})@([0-9]+\.[0-9]+\.[0-9]+):([0-9a-f]{64})$/;
  async function sha256Hex(text) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, "0")).join("");
  }
  async function attachedTaggedEntry(api, pattern, label) {
    if (typeof api.getCurrentLorebookEntries !== "function") return null;
    let entries;
    try { entries = await api.getCurrentLorebookEntries(); } catch { return null; }
    if (!Array.isArray(entries)) return null;
    const found = new Map();
    for (const entry of entries) {
      const match = pattern.exec(String(entry?.comment ?? ""));
      if (!match || typeof entry.content !== "string") continue;
      if (await sha256Hex(entry.content) !== match[3]) continue;
      found.set(match[1] + "@" + match[2], entry.content);
    }
    if (found.size > 1) {
      throw new Error(label + " 모듈이 여러 개 켜져 있습니다 (" + [...found.keys()].join(", ") + "). 하나만 켜고 다시 시도하세요.");
    }
    const [pair] = found.entries();
    return pair ? { id: pair[0], content: pair[1] } : null;
  }
  async function attachedRulebookInfo(api) {
    return attachedTaggedEntry(api, RULEBOOK_TAG, "SAGA 룰북");
  }
  async function attachedRulebook(api) {
    const info = await attachedRulebookInfo(api);
    return info ? { content: info.content } : null;
  }
  async function attachedScenarioInfo(api) {
    return attachedTaggedEntry(api, SCENARIO_TAG, "SAGA 시나리오");
  }
  async function attachedScenario(api) {
    const info = await attachedScenarioInfo(api);
    return info ? { content: info.content } : null;
  }

  function extractJsonObject(text) {
    if (typeof text !== "string") return null;
    let cleaned = text.trim();
    if (cleaned.startsWith("```")) {
      const lines = cleaned.split(/\r?\n/);
      if (lines.length >= 2 && lines[lines.length - 1].trim() === "```") {
        cleaned = lines.slice(1, -1).join("\n").trim();
      }
    }
    try {
      const parsed = JSON.parse(cleaned);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
    } catch { /* try locating an embedded JSON object below */ }
    const start = cleaned.indexOf("{");
    const end = cleaned.lastIndexOf("}");
    if (start !== -1 && end > start) {
      try {
        const parsed = JSON.parse(cleaned.slice(start, end + 1));
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
      } catch {
        return null;
      }
    }
    return null;
  }

  function extractJsonProposal(text) {
    const parsed = extractJsonObject(text);
    if (parsed && typeof parsed.kind === "string" && parsed.kind) {
      const payload = parsed.payload && typeof parsed.payload === "object" && !Array.isArray(parsed.payload) ? parsed.payload : {};
      return { kind: parsed.kind, payload };
    }
    return null;
  }

  async function readAuxOutput(res) {
    if (typeof res === "string") return res;
    if (!res || typeof res !== "object") return "";
    if (res.type === "fail") {
      throw new Error(typeof res.result === "string" && res.result ? res.result : "RisuAI 보조 모델 호출에 실패했습니다.");
    }
    if (res.result && typeof res.result.getReader === "function") {
      const reader = res.result.getReader();
      let accumulated = "";
      while (true) {
        const { done, value } = await reader.read();
        if (value !== undefined && value !== null) {
          if (typeof value === "string") {
            accumulated += value;
          } else if (typeof value === "object") {
            const firstKey = Object.keys(value)[0];
            if (firstKey !== undefined && typeof value[firstKey] === "string") {
              accumulated = value[firstKey];
            }
          }
        }
        if (done) break;
      }
      return accumulated;
    }
    const raw = res.content ?? res.result ?? res.message ?? res.choices?.[0]?.message?.content ?? "";
    return typeof raw === "string" ? raw : "";
  }

  async function callRisuAux(api, messages) {
    // allowPlugins: true lets users whose PocketRisu submodel is a plugin provider use it;
    // SAGA's own provider still rejects recursive calls via busy + single-use ticket guards.
    const opts = { mode: "submodel", allowPlugins: true, messages };
    if (typeof api.runLLMModel === "function") {
      try {
        return await readAuxOutput(await api.runLLMModel(opts));
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        if (!msg.includes("not found") || typeof api.request !== "function") {
          throw error;
        }
      }
    }
    if (typeof api.request === "function") {
      return await readAuxOutput(await api.request(opts));
    }
    throw new Error("이 호스트는 RisuAI 보조 모델 API(runLLMModel)를 지원하지 않습니다.");
  }

  function createClient(api, uuid = randomId) {
    const tickets = new Map();
    const pendingFooters = new Map();
    const lastCommitted = new Map();
    let identityPromise;
    let busy = false;
    let disposed = false;
    const argument = async name => String((await api.getArgument(name)) ?? "").trim();
    const isTruthy = value => ["1", "true", "yes", "on"].includes(String(value ?? "").trim().toLowerCase());

    async function readJsonStorage(key) {
      if (!api.pluginStorage || typeof api.pluginStorage.getItem !== "function") return {};
      const saved = await api.pluginStorage.getItem(key);
      if (saved && typeof saved === "object" && !Array.isArray(saved)) return { ...saved };
      if (typeof saved === "string" && saved) {
        try {
          const parsed = JSON.parse(saved);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
        } catch {
          return {};
        }
      }
      return {};
    }

    async function writeJsonStorage(key, value) {
      if (!api.pluginStorage || typeof api.pluginStorage.setItem !== "function") return;
      await api.pluginStorage.setItem(key, JSON.stringify(value));
    }

    async function readUiSettings() {
      return readJsonStorage(UI_SETTINGS_KEY);
    }

    async function saveUiSettings(patch) {
      const currentSettings = await readUiSettings();
      const next = { ...currentSettings };
      for (const [k, v] of Object.entries(patch)) {
        if (v === undefined || v === null || v === "") delete next[k];
        else next[k] = v;
      }
      await writeJsonStorage(UI_SETTINGS_KEY, next);
      return next;
    }

    async function current() {
      const selection = await api.getCurrentCharacterIndex();
      const character = await api.getCharacter();
      if (selection !== await api.getCurrentCharacterIndex()) throw new Error("선택한 캐릭터가 변경되었습니다. 다시 생성하세요.");
      return snapshotOf(character, selection);
    }

    async function config() {
      const ui = await readUiSettings();
      const base = String(ui.serverUrl || await argument("server_url") || "http://saga-runtime:4850").trim();
      const url = new URL(base);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) {
        throw new Error("SAGA 서버의 기본 URL을 확인하세요.");
      }
      let instance = String(ui.hostInstanceId || await argument("host_instance_id") || "").trim();
      if (!instance) {
        identityPromise ??= (async () => {
          let saved = await api.pluginStorage.getItem("saga:host-instance:v1");
          if (!saved) {
            saved = "pocketrisu-" + uuid();
            await api.pluginStorage.setItem("saga:host-instance:v1", saved);
          }
          return saved;
        })();
        instance = await identityPromise;
      }
      if (typeof instance !== "string" || !/^\S{1,200}$/.test(instance)) throw new Error("호스트 식별자 형식이 잘못되었습니다.");
      const credentials = await argument("vertex_service_account_json");
      if (new TextEncoder().encode(credentials).length > 16000) throw new Error("Vertex 서비스 계정 JSON이 너무 깁니다. 파일 내용만 붙여넣으세요.");
      const rawMode = String(ui.integrationMode || await argument("integration_mode") || "provider").trim().toLowerCase();
      const integrationMode = rawMode === "hook" ? "hook" : "provider";
      const providerStream = typeof ui.providerStream === "boolean" ? ui.providerStream : isTruthy(await argument("provider_stream"));
      const rawIntentMode = String(ui.intentMode || "").trim().toLowerCase();
      const explicitAux = typeof ui.useAuxIntent === "boolean" ? ui.useAuxIntent : isTruthy(await argument("use_risu_aux_intent"));
      const intentMode = ["risu_aux", "custom_llm", "server"].includes(rawIntentMode)
        ? rawIntentMode
        : (explicitAux ? "risu_aux" : "server");
      const useAuxIntent = intentMode === "risu_aux";
      const intentLlm = ui.intentLlm && typeof ui.intentLlm === "object" && !Array.isArray(ui.intentLlm)
        ? {
            base_url: String(ui.intentLlm.base_url ?? "").trim(),
            model: String(ui.intentLlm.model ?? "").trim(),
            api_key: String(ui.intentLlm.api_key ?? "").trim(),
            reasoning_effort: String(ui.intentLlm.reasoning_effort ?? "").trim(),
            custom_headers: String(ui.intentLlm.custom_headers ?? "").trim(),
            custom_body: String(ui.intentLlm.custom_body ?? "").trim(),
          }
        : { base_url: "", model: "", api_key: "", reasoning_effort: "", custom_headers: "", custom_body: "" };
      return { base: url.origin, instance, credentials, integrationMode, providerStream, useAuxIntent, intentMode, intentLlm };
    }

    async function isChatHookEnabled(chatId) {
      const map = await readJsonStorage(HOOK_CHATS_KEY);
      return Boolean(map[chatId]);
    }

    async function setChatHookEnabled(chatId, enabled) {
      const map = await readJsonStorage(HOOK_CHATS_KEY);
      if (enabled) map[chatId] = true;
      else delete map[chatId];
      await writeJsonStorage(HOOK_CHATS_KEY, map);
      return Boolean(enabled);
    }

    async function toggleChatHook() {
      const snapshot = await current();
      const next = !(await isChatHookEnabled(snapshot.chatId));
      await setChatHookEnabled(snapshot.chatId, next);
      return { chatId: snapshot.chatId, enabled: next };
    }

    async function checkHealth() {
      const settings = await config();
      const response = await api.nativeFetch(settings.base + "/health", {
        method: "GET", headers: {}, networkRoute: "local_network", requestTimeoutMs: 5000,
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      return { ok: true, base: settings.base };
    }

    async function checkStatus() {
      const settings = await config();
      try {
        const response = await api.nativeFetch(settings.base + "/v1/saga/status", {
          method: "GET", headers: {}, networkRoute: "local_network", requestTimeoutMs: 5000,
        });
        if (response.ok) {
          const data = await response.json().catch(() => ({}));
          return {
            ok: true,
            base: settings.base,
            intent_configured: Boolean(data?.intent_configured),
            narrator_configured: Boolean(data?.narrator_configured),
          };
        }
      } catch { /* fall through to /health */ }
      const health = await checkHealth();
      return { ...health, intent_configured: null, narrator_configured: null };
    }

    async function getChatScenario(chatId) {
      if (!chatId) return null;
      const map = await readJsonStorage(CHAT_SCENARIOS_KEY);
      const raw = map[chatId];
      return typeof raw === "string" && raw.trim() ? raw : null;
    }

    async function setChatScenario(chatId, scenarioText) {
      if (!chatId) throw new Error("열린 채팅 ID가 없습니다.");
      const map = await readJsonStorage(CHAT_SCENARIOS_KEY);
      if (scenarioText && String(scenarioText).trim()) {
        const parsed = JSON.parse(String(scenarioText));
        if (!parsed || typeof parsed !== "object" || !parsed.actors?.player) {
          throw new Error("유효한 ScenarioSpec JSON(actors.player 포함)이어야 합니다.");
        }
        map[chatId] = JSON.stringify(parsed, null, 2);
      } else {
        delete map[chatId];
      }
      await writeJsonStorage(CHAT_SCENARIOS_KEY, map);
      return map[chatId] ?? null;
    }

    async function convertCurrentCard(options = {}) {
      const settings = await config();
      const character = await api.getCharacter();
      if (!character || typeof character !== "object") {
        throw new Error("열린 캐릭터 카드를 읽을 수 없습니다.");
      }
      let extraLore = [];
      if (typeof api.getCurrentLorebookEntries === "function") {
        try {
          const entries = await api.getCurrentLorebookEntries();
          if (Array.isArray(entries)) extraLore = entries;
        } catch {
          extraLore = [];
        }
      }
      const safeCard = {
        name: character.name ?? "",
        desc: character.desc ?? character.description ?? "",
        personality: character.personality ?? "",
        scenario: character.scenario ?? "",
        firstMessage: character.firstMessage ?? character.first_mes ?? "",
        tags: Array.isArray(character.tags) ? character.tags : [],
        globalLore: Array.isArray(character.globalLore) ? character.globalLore : [],
      };
      if (options.mode === "aux") {
        const sysPrompt =
          "Convert the untrusted RisuAI character card JSON into a valid SAGA ScenarioSpec JSON " +
          "(schema_version: 1, id, version: '0.1.0', title, summary, actors including 'player', " +
          "map with start_room/rooms/placements, quest_state, narration). Mark provenance fields as " +
          "'card' only when stated in the card, else 'inferred'. Output strict JSON only.";
        const rawText = await callRisuAux(api, [
          { role: "system", content: sysPrompt },
          { role: "user", content: JSON.stringify({ card: safeCard, lorebook: extraLore.slice(0, 25) }) },
        ]);
        const scenario = extractJsonObject(rawText);
        if (!scenario || typeof scenario !== "object" || !scenario.actors?.player) {
          throw new Error("보조 모델이 유효한 ScenarioSpec JSON을 반환하지 않았습니다.");
        }
        return { scenario, warnings: ["RisuAI 보조 모델로 생성된 초안입니다. 확정 전 수치를 검토하세요."], mode: "llm" };
      }
      const body = {
        card: safeCard,
        extra_lorebook: extraLore.slice(0, 120),
        use_llm: Boolean(options.useLlm),
      };
      if (options.useLlm && options.llm?.base_url && options.llm?.model) {
        body.llm = options.llm;
      }
      const response = await api.nativeFetch(settings.base + "/v1/saga/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        networkRoute: "local_network",
        requestTimeoutMs: 60000,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof result.detail === "string" ? result.detail : ("변환 실패 (HTTP " + response.status + ")"));
      }
      return result;
    }

    const signature = value => JSON.stringify(value);
    async function assertCurrent(snapshot, settings) {
      if (disposed || signature(await current()) !== signature(snapshot) ||
          signature(await config()) !== signature(settings) || disposed) {
        throw new Error("요청 중 채팅이나 설정이 변경되었습니다. 현재 채팅에서 다시 생성하세요. 이전 결과는 기록에 보존됩니다.");
      }
    }

    async function fetchIntentContext(settings, sagaBody) {
      try {
        const res = await api.nativeFetch(settings.base + "/v1/saga/intent-context", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ saga: sagaBody }),
          networkRoute: "local_network",
          requestTimeoutMs: 10000,
        });
        if (!res.ok) return null;
        const data = await res.json().catch(() => null);
        if (data && typeof data === "object" && ("already_committed" in data || "is_command" in data)) {
          return data;
        }
        return null;
      } catch {
        return null;
      }
    }

    async function proposeWithRisuAux(text, intentContext = null) {
      const sys = typeof intentContext?.system_instruction === "string" && intentContext.system_instruction
        ? intentContext.system_instruction
        : 'Classify the player message into strict JSON {"kind": "<action_kind>", "payload": {...}}. If it is ordinary roleplay/dialogue without a rule action, return {"kind": "narrative_turn", "payload": {}}. Output JSON only.';
      const userContent = intentContext?.context && typeof intentContext.context === "object"
        ? JSON.stringify(intentContext.context)
        : text;
      const rawText = await callRisuAux(api, [
        { role: "system", content: sys },
        { role: "user", content: userContent },
      ]);
      return extractJsonProposal(rawText);
    }

    async function adjudicateHookTurn(snapshot, settings, rulebook, scenario) {
      const actions = snapshot.messages.filter(message => message.role === "user" && !message.comment)
        .map(message => ({ message_id: message.id, text: message.text, context_hidden: message.disabled }));
      if (!actions.length) throw new Error("실행할 사용자 메시지가 없습니다.");
      if (actions.at(-1)?.context_hidden) throw new Error("마지막 사용자 메시지가 숨겨져 있습니다. 다시 표시한 뒤 생성하세요.");
      const body = {
        saga: {
          schema_version: 4,
          host_instance_id: settings.instance,
          chat_id: snapshot.chatId,
          actor_id: "player",
          purpose: "action",
          actions,
        },
      };
      if (rulebook) body.saga.rulebook = rulebook;
      if (scenario) body.saga.scenario = scenario;
      const headers = { "content-type": "application/json" };
      if (settings.intentMode === "risu_aux") {
        const ctx = await fetchIntentContext(settings, body.saga);
        if (!ctx?.already_committed && !ctx?.is_command) {
          let proposal;
          try {
            proposal = await proposeWithRisuAux(actions.at(-1).text, ctx);
          } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            throw new Error("RisuAI 보조 모델 자연어 판정 실패: " + detail);
          }
          if (proposal) body.client_proposal = proposal;
        }
      } else if (settings.intentMode === "custom_llm") {
        const llm = settings.intentLlm || {};
        if (!llm.base_url || !llm.model) {
          throw new Error("SAGA 자연어 판정(직접 LLM)의 Base URL과 Model ID를 SAGA 패널에서 설정하세요.");
        }
        const override = { base_url: llm.base_url, model: llm.model };
        if (llm.reasoning_effort) override.reasoning_effort = llm.reasoning_effort;
        if (llm.custom_headers) {
          try { override.custom_headers = JSON.parse(llm.custom_headers); }
          catch { throw new Error("Intent LLM Custom Headers가 유효한 JSON이 아닙니다."); }
        }
        if (llm.custom_body) {
          try { override.custom_body = JSON.parse(llm.custom_body); }
          catch { throw new Error("Intent LLM Custom Body가 유효한 JSON이 아닙니다."); }
        }
        body.intent_llm = override;
        if (llm.api_key) {
          headers["x-saga-llm-api-key"] = llm.api_key;
        }
      }
      if (settings.credentials) {
        headers["x-saga-vertex-secret"] = btoa(String.fromCharCode(...new TextEncoder().encode(settings.credentials)));
      }
      const timeout = (settings.credentials || settings.intentMode === "custom_llm") ? 65000 : 25000;
      let timer;
      let response;
      try {
        response = await Promise.race([
          api.nativeFetch(settings.base + "/v1/saga/turn", {
            method: "POST", headers,
            body: JSON.stringify(body), networkRoute: "local_network", requestTimeoutMs: timeout,
          }),
          new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error("SAGA 판정 응답 시간이 초과되었습니다. 같은 메시지를 재생성하면 저장된 결과를 복구합니다.")), timeout + 2000);
          }),
        ]);
      } finally { clearTimeout(timer); }
      const result = await response.json();
      if (!response.ok) {
        const detail = typeof result?.detail === "string" ? result.detail : HELP;
        throw new Error("SAGA 판정 실패 (" + response.status + "): " + detail);
      }
      await assertCurrent(snapshot, settings);
      const state = result?.saga;
      if (!validSagaState(state) || typeof state.prompt_block !== "string") {
        throw new Error("SAGA 판정 응답 형식이 잘못되었습니다.");
      }
      return state;
    }

    async function beforeRequest(prompt, mode) {
      if (!Array.isArray(prompt)) return prompt;
      const clean = prompt.filter(message => !(message?.role === "system" &&
        typeof message.content === "string" &&
        (message.content.startsWith(MARKER) || message.content.startsWith(HOOK_MARKER))));
      if (mode !== "model" || disposed) return clean;
      tickets.clear();
      let settings;
      try {
        settings = await config();
      } catch (error) {
        console.warn("[SAGA] Request preparation failed:", error instanceof Error ? error.message : "unknown error");
        return clean;
      }

      if (settings.integrationMode === "hook") {
        // Hook mode activates automatically when a SAGA rulebook or scenario module is enabled,
        // or when this chat was manually toggled ON (default OFF).
        let snapshot;
        let rulebook;
        let scenario;
        let active = false;
        try {
          snapshot = await current();
          rulebook = await attachedRulebook(api);
          scenario = await attachedScenario(api);
          if (!scenario) {
            const savedSc = await getChatScenario(snapshot.chatId);
            if (savedSc) scenario = { content: savedSc };
          }
          active = Boolean(rulebook) || Boolean(scenario) || await isChatHookEnabled(snapshot.chatId);
        } catch (error) {
          // Multiple conflicting rulebooks/scenarios in an active chat must fail closed.
          if (error instanceof Error && error.message.includes("모듈이 여러 개")) {
            throw error;
          }
          return clean;
        }
        if (!active) return clean;
        const activeMessages = snapshot.messages.filter(message => !message.disabled && !message.comment);
        const latest = activeMessages.at(-1);
        if (!latest || latest.role !== "user") return clean;
        if (!clean.some(message => message.role === "user" &&
            (message.memo === latest.id || message.content === latest.text))) return clean;
        if (busy) throw new Error("SAGA 요청이 이미 진행 중입니다. 완료 후 다시 생성하세요.");
        busy = true;
        try {
          await assertCurrent(snapshot, settings);
          const committed = await adjudicateHookTurn(snapshot, settings, rulebook, scenario);
          lastCommitted.set(snapshot.chatId, committed);
          pendingFooters.set(snapshot.chatId, { messageId: latest.id, state: committed });
          const stripped = clean.map(message =>
            message?.role === "assistant" && typeof message.content === "string"
              ? { ...message, content: stripSagaFooter(message.content) }
              : message
          );
          return [...stripped, { role: "system", content: HOOK_MARKER + "\n" + committed.prompt_block }];
        } finally {
          busy = false;
        }
      }

      // Provider mode: attach a single-use opaque ticket consumed only by SAGA's provider.
      try {
        const snapshot = await current();
        const active = snapshot.messages.filter(message => !message.disabled && !message.comment);
        const latest = active.at(-1);
        // Pinned PocketRisu removes the assistant on regeneration, but retains it on continue.
        if (!latest || latest.role !== "user") return clean;
        if (!clean.some(message => message.role === "user" &&
            (message.memo === latest.id || message.content === latest.text))) return clean;
        await assertCurrent(snapshot, settings);
        const ticket = MARKER + uuid();
        tickets.set(ticket, { snapshot, settings });
        return [...clean, { role: "system", content: ticket }];
      } catch (error) {
        console.warn("[SAGA] Request preparation failed:", error instanceof Error ? error.message : "unknown error");
        return clean;
      }
    }

    async function afterRequest(content, mode) {
      if (mode !== "model" || disposed || typeof content !== "string") return content;
      let snapshot;
      try {
        snapshot = await current();
      } catch {
        return content;
      }
      const pending = pendingFooters.get(snapshot.chatId);
      if (!pending) return content;
      pendingFooters.delete(snapshot.chatId);
      const legacy = pending.state.legacy_read_only ? "\n\n기존 v1 기록은 읽기 전용입니다. 새 행동은 새 채팅에서 시작하세요." : "";
      return content + "\n\n" + statusFooter(pending.state) + legacy;
    }

    function streamFromSse(response, snapshot, settings, signal) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalSaga = null;
      return new ReadableStream({
        async pull(controller) {
          try {
            while (true) {
              if (disposed || signal?.aborted) {
                controller.error(new Error("요청이 취소되었습니다. 완료된 판정은 기록에 보존됩니다."));
                return;
              }
              const { value, done } = await reader.read();
              if (done) {
                if (finalSaga && validSagaState(finalSaga)) {
                  const legacy = finalSaga.legacy_read_only ? "\n\n기존 v1 기록은 읽기 전용입니다. 새 행동은 새 채팅에서 시작하세요." : "";
                  controller.enqueue("\n\n" + statusFooter(finalSaga) + legacy);
                }
                controller.close();
                return;
              }
              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split(/\r?\n/);
              buffer = lines.pop() ?? "";
              let enqueued = false;
              for (const rawLine of lines) {
                const line = rawLine.trim();
                if (!line.startsWith("data:")) continue;
                const data = line.slice(5).trim();
                if (!data || data === "[DONE]") continue;
                let parsed;
                try { parsed = JSON.parse(data); } catch { continue; }
                if (parsed.saga) finalSaga = parsed.saga;
                const delta = parsed.choices?.[0]?.delta?.content;
                if (typeof delta === "string" && delta) {
                  await assertCurrent(snapshot, settings);
                  controller.enqueue(delta);
                  enqueued = true;
                }
              }
              if (enqueued) return;
            }
          } catch (error) {
            controller.error(error);
          }
        },
        cancel() {
          return reader.cancel();
        },
      });
    }

    async function provider(arg, signal) {
      let acquired = false;
      try {
        if (disposed || busy) throw new Error("SAGA 요청이 이미 진행 중입니다. 완료 후 다시 생성하세요.");
        const markers = (arg?.prompt_chat ?? []).filter(message => message?.role === "system" && tickets.has(message.content));
        if (markers.length !== 1) throw new Error("SAGA는 사용자 턴의 보내기·재생성만 지원합니다. 이어쓰기·보조 요청은 실행하지 않습니다. " + HELP);
        const ticket = markers[0].content;
        const { snapshot, settings } = tickets.get(ticket);
        tickets.delete(ticket);
        busy = acquired = true;
        await assertCurrent(snapshot, settings);
        if (signal?.aborted) throw new Error("요청이 취소되었습니다.");
        const actions = snapshot.messages.filter(message => message.role === "user" && !message.comment)
          .map(message => ({ message_id: message.id, text: message.text, context_hidden: message.disabled }));
        if (actions.at(-1)?.context_hidden) throw new Error("마지막 사용자 메시지가 숨겨져 있습니다. 다시 표시한 뒤 생성하세요.");
        const useV4 = settings.providerStream;
        const body = { model: "saga-srd-demo", stream: useV4,
          messages: [{ role: "user", content: actions.at(-1).text }],
          saga: { schema_version: useV4 ? 4 : 3, host_instance_id: settings.instance, chat_id: snapshot.chatId,
            actor_id: "player", purpose: "action", actions } };
        if (useV4) {
          body.risu_prompt = risuPromptMessages(arg?.prompt_chat);
        }
        const rulebook = await attachedRulebook(api);
        if (rulebook) body.saga.rulebook = rulebook;
        if (useV4) {
          let scenario = await attachedScenario(api);
          if (!scenario) {
            const savedSc = await getChatScenario(snapshot.chatId);
            if (savedSc) scenario = { content: savedSc };
          }
          if (scenario) body.saga.scenario = scenario;
        }
        const headers = { "content-type": "application/json" };
        if (settings.credentials) {
          // This header is fully masked by the pinned host's request logger; a JSON body is not.
          headers["x-saga-vertex-secret"] = btoa(String.fromCharCode(...new TextEncoder().encode(settings.credentials)));
          body.narration = { schema_version: 1, provider: "vertex", context: narrationContext(arg.prompt_chat) };
        }
        const timeout = settings.credentials || useV4 ? 65000 : 15000;
        let timer;
        let response;
        try {
          response = await Promise.race([
            api.nativeFetch(settings.base + "/v1/chat/completions", {
              method: "POST", headers,
              body: JSON.stringify(body), networkRoute: "local_network", requestTimeoutMs: timeout,
            }),
            new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("SAGA 응답 시간이 초과되었습니다. 같은 메시지를 재생성하면 저장된 결과를 복구합니다.")), timeout + 2000); }),
          ]);
        } finally { clearTimeout(timer); }
        const contentType = String(response.headers?.get?.("content-type") ?? "");
        if (useV4 && response.ok && contentType.includes("text/event-stream") && response.body && typeof response.body.getReader === "function") {
          await assertCurrent(snapshot, settings);
          return { success: true, content: streamFromSse(response, snapshot, settings, signal) };
        }
        const result = await response.json();
        if (!response.ok) {
          const detail = typeof result.detail === "string" ? result.detail : HELP;
          throw new Error("SAGA 요청 실패 (" + response.status + "): " + detail);
        }
        await assertCurrent(snapshot, settings);
        if (signal?.aborted) throw new Error("요청이 취소되었습니다. 완료된 판정은 기록에 보존됩니다.");
        const state = result.saga;
        const prose = result.choices?.[0]?.message?.content;
        if (!validSagaState(state) || typeof prose !== "string") {
          throw new Error("SAGA 응답 형식이 잘못되었습니다.");
        }
        lastCommitted.set(snapshot.chatId, state);
        const legacy = state.legacy_read_only ? "\n\n기존 v1 기록은 읽기 전용입니다. 새 행동은 새 채팅에서 시작하세요." : "";
        return { success: true, content: prose + "\n\n" + statusFooter(state) + legacy };
      } catch (error) {
        return { success: false, content: error instanceof Error ? error.message : "SAGA 요청 실패" };
      } finally {
        if (acquired) busy = false;
      }
    }

    function lastState(chatId) {
      return lastCommitted.get(chatId) ?? null;
    }

    function dispose() {
      disposed = true;
      tickets.clear();
      pendingFooters.clear();
      lastCommitted.clear();
    }

    return {
      current, config, readUiSettings, saveUiSettings, checkHealth, checkStatus, lastState,
      getChatScenario, setChatScenario, convertCurrentCard,
      beforeRequest, afterRequest, provider,
      toggleChatHook, isChatHookEnabled, setChatHookEnabled, dispose,
    };
  }

  // Host-theme-aware control panel UI built strictly with safe DOM APIs (no innerHTML).
  const PANEL_CSS = [
    ":root{--bg:#14161a;--card:#1d2026;--border:#2e333d;--fg:#eef1f6;--muted:#98a2b3;--accent:#3b82f6;--ok:#10b981;--err:#ef4444;}",
    "*{box-sizing:border-box;}html,body{height:100%;margin:0;overflow-y:auto;overscroll-behavior:contain;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;}",
    ".saga-wrap{max-width:620px;margin:0 auto;padding:18px 16px 28px;display:flex;flex-direction:column;gap:14px;}",
    ".saga-head{display:flex;align-items:center;justify-content:space-between;gap:10px;border-bottom:1px solid var(--border);padding-bottom:10px;}",
    ".saga-title{font-size:17px;font-weight:700;display:flex;align-items:center;gap:8px;}",
    ".saga-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px;}",
    ".saga-card h3{margin:0;font-size:13px;font-weight:700;color:var(--muted);letter-spacing:0.02em;}",
    ".saga-row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;}",
    ".saga-badge{display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700;border:1px solid var(--border);}",
    ".saga-badge.on{background:rgba(16,185,129,0.16);border-color:var(--ok);color:var(--ok);}",
    ".saga-badge.off{background:rgba(152,162,179,0.12);color:var(--muted);}",
    ".saga-btn{appearance:none;border:1px solid var(--border);background:transparent;color:var(--fg);padding:7px 12px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600;}",
    ".saga-btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;}",
    ".saga-btn.on{background:rgba(16,185,129,0.18);border-color:var(--ok);color:var(--ok);}",
    ".saga-seg{display:grid;grid-template-columns:1fr 1fr;gap:8px;}",
    ".saga-seg3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;}",
    ".saga-field{display:flex;flex-direction:column;gap:4px;}",
    ".saga-field label{font-size:12px;color:var(--muted);}",
    ".saga-field input,.saga-field select,.saga-field textarea{width:100%;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--fg);font-size:13px;font-family:inherit;}",
    ".saga-field textarea{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;resize:vertical;}",
    ".saga-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--muted);word-break:break-all;}",
    ".saga-toast{min-height:20px;font-size:12px;color:var(--ok);}.saga-toast.err{color:var(--err);}",
  ].join("");

  function el(doc, tag, props = {}, ...children) {
    const node = doc.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null) continue;
      if (key === "class") node.setAttribute("class", value);
      else if (key === "onclick") node.addEventListener("click", value);
      else if (key === "value") node.value = value;
      else if (key === "disabled") node.disabled = Boolean(value);
      else node.setAttribute(key, String(value));
    }
    for (const child of children) {
      if (child === undefined || child === null || child === false) continue;
      node.appendChild(typeof child === "string" ? doc.createTextNode(child) : child);
    }
    return node;
  }

  async function applyHostTheme(doc, api) {
    if (typeof api.getColorScheme !== "function") return;
    try {
      const res = await api.getColorScheme();
      const s = res?.scheme;
      if (!s) return;
      const root = doc.documentElement.style;
      if (s.darkbg) root.setProperty("--bg", s.darkbg);
      if (s.bgcolor) root.setProperty("--card", s.bgcolor);
      if (s.darkBorderc || s.borderc) root.setProperty("--border", s.darkBorderc || s.borderc);
      if (s.textcolor) root.setProperty("--fg", s.textcolor);
      if (s.textcolor2) root.setProperty("--muted", s.textcolor2);
      if (s.primary || s.selected) root.setProperty("--accent", s.primary || s.selected);
      if (s.draculared) root.setProperty("--err", s.draculared);
    } catch { /* keep dark fallback */ }
  }

  async function renderPanel(doc, api, client) {
    doc.body.replaceChildren();
    if (!doc.getElementById("saga-panel-style")) {
      doc.head.appendChild(el(doc, "style", { id: "saga-panel-style" }, PANEL_CSS));
    }
    await applyHostTheme(doc, api);

    const toast = el(doc, "div", { class: "saga-toast", role: "status" });
    const notify = (msg, err = false) => {
      toast.textContent = msg;
      toast.setAttribute("class", err ? "saga-toast err" : "saga-toast");
    };

    const close = () => {
      doc.removeEventListener("keydown", onKey);
      if (typeof doc.activeElement?.blur === "function") doc.activeElement.blur();
      if (typeof api.hideContainer === "function") void api.hideContainer();
    };
    const onKey = e => { if (e.key === "Escape") close(); };
    doc.addEventListener("keydown", onKey);

    const wrap = el(doc, "div", { class: "saga-wrap" });
    doc.body.appendChild(wrap);

    async function draw() {
      let settings;
      let configError = null;
      try { settings = await client.config(); }
      catch (e) { configError = e instanceof Error ? e.message : String(e); }

      let snapshot = null;
      try { snapshot = await client.current(); } catch { snapshot = null; }

      let rulebookInfo = null;
      let rulebookError = null;
      try { rulebookInfo = await attachedRulebookInfo(api); }
      catch (e) { rulebookError = e instanceof Error ? e.message : String(e); }

      let scenarioInfo = null;
      let scenarioError = null;
      try { scenarioInfo = await attachedScenarioInfo(api); }
      catch (e) { scenarioError = e instanceof Error ? e.message : String(e); }

      const manualOn = snapshot ? await client.isChatHookEnabled(snapshot.chatId) : false;
      const autoModule = Boolean(rulebookInfo) || Boolean(scenarioInfo);
      const hookActive = autoModule || manualOn;
      const statusLabel = !snapshot
        ? "채팅 없음"
        : (settings?.integrationMode !== "hook"
          ? "Provider 모드 사용 중"
          : (autoModule ? "ON (모듈 자동)" : (manualOn ? "ON (이 채팅 수동)" : "OFF")));

      const badgeClass = (settings?.integrationMode === "hook" && hookActive) ? "saga-badge on" : "saga-badge off";

      const head = el(doc, "div", { class: "saga-head" },
        el(doc, "div", { class: "saga-title" }, "SAGA v" + VERSION),
        el(doc, "button", { type: "button", class: "saga-btn", onclick: close }, "닫기"),
      );

      // Card 1: Current chat status & Hook toggle
      const toggleBtn = snapshot
        ? el(doc, "button", {
            type: "button",
            class: manualOn ? "saga-btn on" : "saga-btn primary",
            onclick: async () => {
              try {
                const res = await client.toggleChatHook();
                if (res.enabled && settings?.integrationMode !== "hook") {
                  await client.saveUiSettings({ integrationMode: "hook" });
                  notify("이 채팅 SAGA Hook ON + 동작 모드를 Hook으로 전환했습니다.");
                } else {
                  notify("이 채팅 수동 Hook: " + (res.enabled ? "ON" : "OFF"));
                }
                await draw();
              } catch (e) { notify(e instanceof Error ? e.message : String(e), true); }
            },
          }, manualOn ? "이 채팅 Hook 끄기 (현재 ON)" : "이 채팅 Hook 켜기 (현재 OFF)")
        : null;

      const healthBtn = el(doc, "button", {
        type: "button",
        class: "saga-btn",
        onclick: async () => {
          notify("서버 연결 확인 중...");
          try {
            const res = await client.checkHealth();
            notify("SAGA 백엔드 연결 정상 (" + res.base + ")");
          } catch (e) { notify("백엔드 연결 실패: " + (e instanceof Error ? e.message : String(e)), true); }
        },
      }, "서버 연결 테스트");

      const chatCard = el(doc, "div", { class: "saga-card" },
        el(doc, "h3", {}, "현재 채팅 상태"),
        el(doc, "div", { class: "saga-row" },
          el(doc, "span", { class: badgeClass, "data-hook-status": statusLabel }, statusLabel),
          el(doc, "div", { class: "saga-row" }, toggleBtn, healthBtn),
        ),
        el(doc, "div", { class: "saga-mono" }, "채팅 ID: " + (snapshot ? snapshot.chatId : "(열린 채팅 없음)")),
        el(doc, "div", { class: "saga-mono" }, "감지된 룰북 모듈: " + (rulebookError ? "오류 — " + rulebookError : (rulebookInfo ? rulebookInfo.id : "없음"))),
        el(doc, "div", { class: "saga-mono" }, "감지된 시나리오 모듈: " + (scenarioError ? "오류 — " + scenarioError : (scenarioInfo ? scenarioInfo.id : "없음"))),
        autoModule ? el(doc, "div", { class: "saga-mono" }, "※ SAGA 모듈이 켜져 있으면 Hook 모드에서 자동 활성화됩니다.") : null,
      );

      // B2 & B1: Scene Board and latest Turn Card from the last committed turn in this chat
      const latest = snapshot ? client.lastState(snapshot.chatId) : null;
      let sceneCard = null;
      if (latest?.scene_board) {
        const sb = latest.scene_board;
        const people = (sb.participants ?? []).map(p =>
          (p.is_player ? "★ " : "") + (p.display_name ? p.display_name + " (" + p.actor_id + ")" : p.actor_id) +
          " " + p.hp + "/" + p.max_hp +
          (p.conditions?.length ? ", " + p.conditions.join(",") : "")
        );
        const exits = (sb.exits ?? []).map(e =>
          e.key + ": " + e.label + (e.locked ? " [잠김]" : "") + " → " + e.destination
        );
        const lootEntries = Object.entries(sb.room_loot ?? {}).map(([k, v]) => k + "×" + v);
        const clueEntries = Object.entries(sb.discovered_clues ?? {}).map(([k, v]) => k + ": " + v);
        const questEntries = (sb.quests ?? []).map(q =>
          q.quest_id + " [" + (q.status ?? "active") + (q.stage != null ? " #" + q.stage : "") + "]" +
          (q.objective ? " — " + q.objective : "")
        );
        sceneCard = el(doc, "div", { class: "saga-card" },
          el(doc, "h3", {}, "현재 장면 보드 (B2)"),
          sb.current_room ? el(doc, "div", { class: "saga-mono" }, "위치: " + sb.current_room) : null,
          el(doc, "div", { class: "saga-mono" }, "참가자: " + (people.length ? people.join(" · ") : "없음")),
          exits.length ? el(doc, "div", { class: "saga-mono" }, "출구: " + exits.join(" | ")) : null,
          lootEntries.length ? el(doc, "div", { class: "saga-mono" }, "놓인 물품: " + lootEntries.join(", ")) : null,
          clueEntries.length ? el(doc, "div", { class: "saga-mono" }, "발견한 단서: " + clueEntries.join(" | ")) : null,
          questEntries.length ? el(doc, "div", { class: "saga-mono" }, "퀘스트: " + questEntries.join(" | ")) : null,
        );
      }
      let turnCardEl = null;
      if (latest?.turn_card) {
        const tc = latest.turn_card;
        const rollsTxt = (tc.rolls ?? []).map(r => r.expression + "→[" + r.raw.join(",") + "]=" + r.total).join(" · ");
        turnCardEl = el(doc, "div", { class: "saga-card" },
          el(doc, "h3", {}, "최근 판정 카드 (B1)"),
          el(doc, "div", { class: "saga-mono" }, "턴 #" + tc.seq + " · " + tc.kind + (latest.replayed ? " (기록 재사용)" : "") + " · " + tc.event_id),
          rollsTxt ? el(doc, "div", { class: "saga-mono" }, "주사위: " + rollsTxt) : null,
          tc.resolution ? el(doc, "div", { class: "saga-mono" }, "결과: " + tc.resolution.outcome + " (" + tc.resolution.total + " vs " + tc.resolution.target + (tc.resolution.damage != null ? ", 피해 " + tc.resolution.damage : "") + ")") : null,
          (tc.changes ?? []).length ? el(doc, "div", { class: "saga-mono" }, "변화: " + tc.changes.join(" | ")) : null,
          (tc.facts ?? []).length ? el(doc, "div", { class: "saga-mono" }, "기록: " + tc.facts.join(" | ")) : null,
        );
      }

      let serverStatus = null;
      if (settings && !configError) {
        try { serverStatus = await client.checkStatus(); } catch { serverStatus = null; }
      }

      // Card 2: Mode & options
      const curMode = settings?.integrationMode ?? "provider";
      const curIntentMode = settings?.intentMode ?? "server";
      const modeCard = el(doc, "div", { class: "saga-card" },
        el(doc, "h3", {}, "동작 모드"),
        el(doc, "div", { class: "saga-seg" },
          el(doc, "button", {
            type: "button",
            class: curMode === "hook" ? "saga-btn on" : "saga-btn",
            onclick: async () => {
              await client.saveUiSettings({ integrationMode: "hook" });
              notify("Hook 모드로 저장했습니다. (RisuAI 메인 모델 그대로 사용)");
              await draw();
            },
          }, (curMode === "hook" ? "✓ " : "") + "Hook 모드 (RisuAI 메인 모델)"),
          el(doc, "button", {
            type: "button",
            class: curMode === "provider" ? "saga-btn on" : "saga-btn",
            onclick: async () => {
              await client.saveUiSettings({ integrationMode: "provider" });
              notify("Provider 모드로 저장했습니다. (SAGA SRD Demo 프로바이더)");
              await draw();
            },
          }, (curMode === "provider" ? "✓ " : "") + "Provider 모드 (SAGA 프로바이더)"),
        ),
        el(doc, "div", { class: "saga-row" },
          el(doc, "span", {}, "Provider v4 SSE 스트리밍 + risu_prompt"),
          el(doc, "button", {
            type: "button",
            class: settings?.providerStream ? "saga-btn on" : "saga-btn",
            onclick: async () => {
              const next = !settings?.providerStream;
              await client.saveUiSettings({ providerStream: next });
              notify("Provider v4 스트리밍: " + (next ? "ON" : "OFF"));
              await draw();
            },
          }, settings?.providerStream ? "ON" : "OFF"),
        ),
      );

      // Card 2b: Natural-language intent & dice classifier (Hook mode)
      const savedIntentLlm = settings?.intentLlm ?? {};
      const intentUrlIn = el(doc, "input", { type: "text", value: savedIntentLlm.base_url ?? "", placeholder: "https://openrouter.ai/api/v1 또는 http://127.0.0.1:11434/v1" });
      const intentModelIn = el(doc, "input", { type: "text", value: savedIntentLlm.model ?? "", placeholder: "google/gemini-2.5-flash" });
      const intentKeyIn = el(doc, "input", { type: "password", autocomplete: "off", value: savedIntentLlm.api_key ?? "", placeholder: "API Key (비우면 서버 SAGA_LLM_API_KEY 사용)" });
      const intentEffortSel = el(doc, "select", {},
        el(doc, "option", { value: "" }, "reasoning_effort: 지정 안 함"),
        el(doc, "option", { value: "none" }, "none"),
        el(doc, "option", { value: "minimal" }, "minimal"),
        el(doc, "option", { value: "low" }, "low"),
        el(doc, "option", { value: "medium" }, "medium"),
        el(doc, "option", { value: "high" }, "high"),
        el(doc, "option", { value: "xhigh" }, "xhigh"),
      );
      intentEffortSel.value = savedIntentLlm.reasoning_effort ?? "";
      const intentHeadersIn = el(doc, "textarea", { rows: "2", placeholder: '{"HTTP-Referer":"https://example.com"}' });
      intentHeadersIn.value = savedIntentLlm.custom_headers ?? "";
      const intentBodyIn = el(doc, "textarea", { rows: "2", placeholder: '{"temperature":0}' });
      intentBodyIn.value = savedIntentLlm.custom_body ?? "";

      const intentStatusText = curIntentMode === "risu_aux"
        ? "현재: RisuAI 보조 모델 (submodel · runLLMModel)로 자연어 행동을 판정하고 주사위를 굴립니다."
        : (curIntentMode === "custom_llm"
          ? (savedIntentLlm.base_url && savedIntentLlm.model
            ? "현재: 직접 설정한 LLM (" + savedIntentLlm.model + ")로 자연어 행동을 판정합니다."
            : "주의: Base URL과 Model ID를 입력하고 저장해야 자연어 판정이 작동합니다.")
          : (serverStatus?.intent_configured === true
            ? "현재: 백엔드 서버(.env) Intent LLM이 활성화되어 있습니다."
            : (serverStatus?.intent_configured === false
              ? "주의: 백엔드 서버에 Intent LLM이 미설정입니다. 자연어는 주사위 없이 narrative_turn으로만 기록됩니다. 위에서 RisuAI 보조 모델 또는 직접 LLM을 선택하세요."
              : "현재: 백엔드 서버 설정(.env / Vertex JSON)을 사용합니다.")));

      const intentCustomBox = curIntentMode === "custom_llm"
        ? el(doc, "div", { class: "saga-field", style: "gap:8px;" },
            el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "LLM Base URL (https:// 또는 localhost http://)"), intentUrlIn),
            el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Model ID"), intentModelIn),
            el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "API Key (x-saga-llm-api-key 헤더로만 전송)"), intentKeyIn),
            el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Reasoning Effort"), intentEffortSel),
            el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Custom Headers (JSON)"), intentHeadersIn),
            el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Custom Body (JSON)"), intentBodyIn),
            el(doc, "div", { class: "saga-row" },
              el(doc, "button", {
                type: "button",
                class: "saga-btn primary",
                onclick: async () => {
                  try {
                    const base_url = intentUrlIn.value.trim();
                    const model = intentModelIn.value.trim();
                    if (!base_url || !model) throw new Error("Base URL과 Model ID를 모두 입력하세요.");
                    const parsed = new URL(base_url);
                    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("http:// 또는 https:// URL만 허용됩니다.");
                    const hdrTxt = intentHeadersIn.value.trim();
                    if (hdrTxt) JSON.parse(hdrTxt);
                    const bodyTxt = intentBodyIn.value.trim();
                    if (bodyTxt) JSON.parse(bodyTxt);
                    await client.saveUiSettings({
                      intentMode: "custom_llm",
                      useAuxIntent: false,
                      intentLlm: {
                        base_url,
                        model,
                        api_key: intentKeyIn.value.trim(),
                        reasoning_effort: intentEffortSel.value.trim(),
                        custom_headers: hdrTxt,
                        custom_body: bodyTxt,
                      },
                    });
                    notify("자연어 판정 LLM 설정을 저장했습니다.");
                    await draw();
                  } catch (e) { notify("저장 실패: " + (e instanceof Error ? e.message : String(e)), true); }
                },
              }, "판정 LLM 저장"),
            ),
          )
        : null;

      const intentCard = el(doc, "div", { class: "saga-card" },
        el(doc, "h3", {}, "자연어 판정 / 주사위 해석 (Hook 모드)"),
        el(doc, "div", { class: "saga-seg3" },
          el(doc, "button", {
            type: "button",
            class: curIntentMode === "risu_aux" ? "saga-btn on" : "saga-btn",
            onclick: async () => {
              await client.saveUiSettings({ intentMode: "risu_aux", useAuxIntent: true });
              notify("자연어 판정: RisuAI 보조 모델 (submodel)로 전환했습니다.");
              await draw();
            },
          }, (curIntentMode === "risu_aux" ? "✓ " : "") + "RisuAI 보조 모델"),
          el(doc, "button", {
            type: "button",
            class: curIntentMode === "custom_llm" ? "saga-btn on" : "saga-btn",
            onclick: async () => {
              await client.saveUiSettings({ intentMode: "custom_llm", useAuxIntent: false });
              notify("자연어 판정: 직접 LLM 설정 모드로 전환했습니다.");
              await draw();
            },
          }, (curIntentMode === "custom_llm" ? "✓ " : "") + "직접 LLM 설정"),
          el(doc, "button", {
            type: "button",
            class: curIntentMode === "server" ? "saga-btn on" : "saga-btn",
            onclick: async () => {
              await client.saveUiSettings({ intentMode: "server", useAuxIntent: false });
              notify("자연어 판정: 백엔드 서버 설정(.env) 사용으로 전환했습니다.");
              await draw();
            },
          }, (curIntentMode === "server" ? "✓ " : "") + "서버 설정"),
        ),
        el(doc, "div", { class: "saga-mono" }, intentStatusText),
        intentCustomBox,
      );

      // Card 3: Server connection
      const urlInput = el(doc, "input", { type: "text", value: settings?.base ?? "http://127.0.0.1:4852", placeholder: "http://127.0.0.1:4852" });
      const hostInput = el(doc, "input", { type: "text", value: settings?.instance ?? "", placeholder: "risu-6131" });
      const serverCard = el(doc, "div", { class: "saga-card" },
        el(doc, "h3", {}, "서버 연결 설정 (플러그인 인자보다 우선 적용)"),
        configError ? el(doc, "div", { class: "saga-toast err" }, configError) : null,
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "SAGA Server URL"), urlInput),
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Host Instance ID"), hostInput),
        el(doc, "div", { class: "saga-row" },
          el(doc, "button", {
            type: "button",
            class: "saga-btn primary",
            onclick: async () => {
              try {
                const candidate = urlInput.value.trim();
                const parsed = new URL(candidate);
                if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("http:// 또는 https:// URL만 허용됩니다.");
                await client.saveUiSettings({ serverUrl: parsed.origin, hostInstanceId: hostInput.value.trim() });
                notify("서버 설정을 저장했습니다.");
                await draw();
              } catch (e) { notify(e instanceof Error ? e.message : String(e), true); }
            },
          }, "설정 저장"),
        ),
      );

      // Card A2: Convert current RisuAI card/lorebook into a SAGA scenario draft
      let draftJsonText = snapshot ? (await client.getChatScenario(snapshot.chatId) ?? "") : "";
      let a2Mode = "backend_rule";
      let a2LlmUrl = "";
      let a2LlmModel = "";
      let a2LlmKey = "";
      let a2Effort = "";
      let a2Headers = "";
      let a2Body = "";

      const a2ModeSel = el(doc, "select", {},
        el(doc, "option", { value: "backend_rule" }, "백엔드 규칙 변환 (무료 · 즉시)"),
        el(doc, "option", { value: "backend_llm" }, "백엔드 LLM 변환 (OpenAI 호환 / 고급 설정)"),
        el(doc, "option", { value: "aux" }, "RisuAI 보조 모델(submodel) 변환"),
      );
      const a2UrlIn = el(doc, "input", { type: "text", placeholder: "https://openrouter.ai/api/v1 (비우면 서버 SAGA_LLM_* 사용)" });
      const a2ModelIn = el(doc, "input", { type: "text", placeholder: "google/gemini-2.5-flash" });
      const a2KeyIn = el(doc, "input", { type: "password", autocomplete: "off", placeholder: "API Key (메모리 전용, 저장 안 됨)" });
      const a2EffortSel = el(doc, "select", {},
        el(doc, "option", { value: "" }, "reasoning_effort: 지정 안 함"),
        el(doc, "option", { value: "none" }, "none"),
        el(doc, "option", { value: "minimal" }, "minimal"),
        el(doc, "option", { value: "low" }, "low"),
        el(doc, "option", { value: "medium" }, "medium"),
        el(doc, "option", { value: "high" }, "high"),
        el(doc, "option", { value: "xhigh" }, "xhigh"),
      );
      const a2HeadersIn = el(doc, "textarea", { rows: "2", placeholder: '{"HTTP-Referer":"https://example.com"}' });
      const a2BodyIn = el(doc, "textarea", { rows: "2", placeholder: '{"temperature":0.2}' });
      const a2LlmBox = el(doc, "div", { class: "saga-field", style: "display:none;gap:8px;" },
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "LLM Base URL"), a2UrlIn),
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Model ID"), a2ModelIn),
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "API Key (저장 안 됨)"), a2KeyIn),
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Reasoning Effort"), a2EffortSel),
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Custom Headers (JSON)"), a2HeadersIn),
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "Custom Body (JSON)"), a2BodyIn),
      );
      a2ModeSel.addEventListener("change", () => {
        a2Mode = a2ModeSel.value;
        a2LlmBox.style.display = a2Mode === "backend_llm" ? "flex" : "none";
      });
      const a2DraftArea = el(doc, "textarea", { rows: "6", placeholder: "초안을 생성하면 ScenarioSpec JSON이 여기에 표시됩니다." });
      a2DraftArea.value = draftJsonText;
      const a2Summary = el(doc, "div", { class: "saga-mono" },
        draftJsonText ? "이 채팅에 확정된 A2 시나리오가 저장되어 있습니다." : "현재 열린 일반 카드를 SAGA 시나리오로 변환할 수 있습니다."
      );
      const a2GenerateBtn = el(doc, "button", {
        type: "button",
        class: "saga-btn primary",
        onclick: async () => {
          notify("현재 카드에서 SAGA 시나리오 초안 생성 중...");
          try {
            a2LlmUrl = a2UrlIn.value.trim();
            a2LlmModel = a2ModelIn.value.trim();
            a2LlmKey = a2KeyIn.value.trim();
            a2Effort = a2EffortSel.value.trim();
            a2Headers = a2HeadersIn.value.trim();
            a2Body = a2BodyIn.value.trim();
            const opts = a2Mode === "aux"
              ? { mode: "aux" }
              : { mode: "backend", useLlm: a2Mode === "backend_llm" };
            if (a2Mode === "backend_llm" && a2LlmUrl) {
              opts.llm = {
                base_url: a2LlmUrl,
                model: a2LlmModel,
                api_key: a2LlmKey || null,
                reasoning_effort: a2Effort || null,
                custom_headers: a2Headers ? JSON.parse(a2Headers) : null,
                custom_body: a2Body ? JSON.parse(a2Body) : null,
              };
            }
            const res = await client.convertCurrentCard(opts);
            draftJsonText = JSON.stringify(res.scenario, null, 2);
            a2DraftArea.value = draftJsonText;
            const actorCount = Object.keys(res.scenario?.actors ?? {}).length;
            const roomCount = Object.keys(res.scenario?.map?.rooms ?? {}).length;
            a2Summary.textContent = "초안 생성 완료 (" + (res.mode ?? "deterministic") + "): 배우 " + actorCount + "명 · 방 " + roomCount + "개" +
              (res.warnings?.length ? " — " + res.warnings[0] : "");
            notify("시나리오 초안을 생성했습니다. 검토 후 '이 채팅에 확정 적용'을 누르세요.");
          } catch (e) { notify("카드 변환 실패: " + (e instanceof Error ? e.message : String(e)), true); }
        },
      }, "현재 카드 초안 생성");
      const a2ApplyBtn = snapshot
        ? el(doc, "button", {
            type: "button",
            class: "saga-btn on",
            onclick: async () => {
              try {
                await client.setChatScenario(snapshot.chatId, a2DraftArea.value);
                await client.setChatHookEnabled(snapshot.chatId, true);
                if (settings?.integrationMode !== "hook") {
                  await client.saveUiSettings({ integrationMode: "hook" });
                }
                notify("시나리오를 이 채팅에 확정 적용하고 Hook 모드를 ON 했습니다.");
                await draw();
              } catch (e) { notify("적용 실패: " + (e instanceof Error ? e.message : String(e)), true); }
            },
          }, "이 채팅에 확정 적용")
        : null;
      const a2ClearBtn = snapshot && draftJsonText
        ? el(doc, "button", {
            type: "button",
            class: "saga-btn",
            onclick: async () => {
              await client.setChatScenario(snapshot.chatId, null);
              notify("이 채팅의 A2 시나리오를 해제했습니다.");
              await draw();
            },
          }, "적용 해제")
        : null;
      const a2Card = el(doc, "div", { class: "saga-card" },
        el(doc, "h3", {}, "카드 → 시나리오 변환 (A2)"),
        a2Summary,
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "변환 방식"), a2ModeSel),
        a2LlmBox,
        el(doc, "div", { class: "saga-row" }, a2GenerateBtn, a2ApplyBtn, a2ClearBtn),
        el(doc, "div", { class: "saga-field" }, el(doc, "label", {}, "시나리오 초안 JSON (수정 후 확정 가능)"), a2DraftArea),
        settings?.base ? el(doc, "div", { class: "saga-mono" }, "단독 웹앱: " + settings.base + "/converter") : null,
      );

      // Card 4: Quick commands
      const helpCard = el(doc, "div", { class: "saga-card" },
        el(doc, "h3", {}, "빠른 사용법"),
        el(doc, "div", { class: "saga-mono" }, "일반 RP: 평소처럼 자유롭게 입력 (자동으로 narrative_turn 커밋)"),
        el(doc, "div", { class: "saga-mono" }, "명령 예시: move north · attack opponent · check strength 10 · damage opponent 5"),
      );

      wrap.replaceChildren(...[head, chatCard, sceneCard, turnCardEl, intentCard, a2Card, modeCard, serverCard, helpCard, toast].filter(Boolean));
    }

    await draw();
    return { redraw: draw };
  }

  globalThis.SagaRisu = { VERSION, snapshotOf, createClient, renderPanel, PROVIDER, HOOK_MARKER, SAGA_ICON };
  if (typeof risuai === "undefined") return;
  const client = createClient(risuai);
  const openUi = async () => {
    if (typeof risuai.showContainer === "function") await risuai.showContainer("fullscreen");
    if (typeof document !== "undefined") await renderPanel(document, risuai, client);
  };
  async function install() {
    await risuai.onUnload(async () => {
      client.dispose();
      await risuai.removeRisuReplacer("beforeRequest", client.beforeRequest);
      if (typeof risuai.removeRisuReplacer === "function") {
        await risuai.removeRisuReplacer("afterRequest", client.afterRequest);
      }
    });
    await risuai.addRisuReplacer("beforeRequest", client.beforeRequest);
    await risuai.addRisuReplacer("afterRequest", client.afterRequest);
    await risuai.addProvider(PROVIDER, client.provider);
    if (typeof risuai.registerSetting === "function") {
      await risuai.registerSetting("SAGA", openUi, SAGA_ICON, "html");
    }
    if (typeof risuai.registerButton === "function") {
      await risuai.registerButton(
        { name: "SAGA", icon: SAGA_ICON, iconType: "html", location: "chat", id: "saga-panel" },
        openUi,
      );
    }
  }
  void install().catch(() => console.error("[SAGA] Plugin installation failed."));
})();
