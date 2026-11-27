//@name saga_runtime
//@display-name SAGA SRD Demo
//@api 3.0
//@version 0.8.0
//@arg server_url string SAGA URL (default http://saga-runtime:4850)
//@arg host_instance_id string Stable host identity (set it explicitly; empty generates one via plugin storage, which hung on one host)
//@arg integration_mode string provider | hook (default: provider). hook uses RisuAI's own main model (presets & streaming) while SAGA adjudicates in beforeRequest/afterRequest.
//@arg provider_stream string false | true (default: false). In provider mode, stream SSE prose and forward risu_prompt using protocol v4.
//@arg use_risu_aux_intent string false | true (default: false). In hook mode, ask RisuAI's submodel to propose an action/narrative_turn for SAGA validation.
//@arg vertex_service_account_json string {{name::Vertex 서비스 계정 JSON}}{{textarea}} JSON 전체를 붙여넣으세요. Gemini 3.8 Flash로 자연어 행동·서술; 비우면 명령·기본 서술.

(() => {
  "use strict";
  const PROVIDER = "SAGA SRD Demo";
  const MARKER = "SAGA request ticket: ";
  const HOOK_MARKER = "[SAGA Hook Context]";
  const HOOK_CHATS_KEY = "saga:hook-chats:v1";
  const HELP = "명령은 룰북의 행동 이름과 입력 순서를 따릅니다: attack opponent / check strength 10 / damage opponent 5 / give player torch 1, 또는 overcome investigate 2 처럼요. name=value로 지정하거나 따옴표로 묶을 수 있고, 판정 명령 뒤에 advantage/disadvantage를 붙일 수 있습니다. 잘못된 명령의 오류 메시지에 그 룰북의 전체 명령 목록이 나옵니다. SAGA 룰북 모듈을 켜고 새 채팅을 시작하면 그 룰북으로 플레이합니다. Vertex JSON 설정 시 SRD 데모에서 자연어 단일 행동도 지원합니다.";

  function narrationContext(prompt) {
    const text = prompt.filter(message => ["system", "user", "assistant"].includes(message?.role) &&
      typeof message.content === "string" && message.content.trim() && !message.content.includes(MARKER) && !message.content.includes(HOOK_MARKER));
    return [...text.filter(message => message.role === "system").slice(0, 8),
      ...text.filter(message => message.role !== "system").slice(-24)]
      .map(message => (message.role + ": " + message.content).slice(0, 4000));
  }

  function risuPromptMessages(prompt) {
    if (!Array.isArray(prompt)) return [];
    return prompt
      .filter(message => ["system", "user", "assistant", "developer"].includes(message?.role) &&
        typeof message.content === "string" && !message.content.includes(MARKER) && !message.content.includes(HOOK_MARKER))
      .slice(-256)
      .map(message => ({ role: message.role, content: message.content.slice(0, 32000) }));
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
  function statusFooter(state) {
    const rolls = state.rolls.map(roll => escapeHtml(roll.expression) + " → [" + roll.raw.map(escapeHtml).join(", ") + "] = " + escapeHtml(roll.total));
    const line1 = displayLines(state).join(" · ");
    const line2 = ["🎲 " + (rolls.length ? rolls.join(" · ") : "주사위 없음"), "턴 " + escapeHtml(state.seq) + (state.replayed ? " · 기록 재사용" : ""),
      "<code>" + escapeHtml(state.event_id) + "</code>"].join(" · ");
    return '<div style="margin-top:0.75em;padding-top:0.4em;border-top:1px solid rgba(128,128,128,0.35);font-size:0.78em;line-height:1.6;opacity:0.85">' +
      line1 + "<br>" + line2 + "</div>";
  }

  function validSagaState(state) {
    return state && [3, 4].includes(state.schema_version) && [1, 2, 3].includes(state.identity_version) &&
      typeof state.event_id === "string" && Array.isArray(state.rolls) && state.state?.actors;
  }

  // A rulebook carried by an enabled SAGA module: the lorebook entry tagged
  // saga:rulebook:<id>@<version>:<sha256>. Only its verified content is forwarded; the backend
  // validates it again and pins a new world to it. Existing worlds keep their rulebook.
  const RULEBOOK_TAG = /^saga:rulebook:([a-z0-9][a-z0-9.-]{0,60})@([0-9]+\.[0-9]+\.[0-9]+):([0-9a-f]{64})$/;
  async function sha256Hex(text) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, "0")).join("");
  }
  async function attachedRulebook(api) {
    if (typeof api.getCurrentLorebookEntries !== "function") return null;
    let entries;
    try { entries = await api.getCurrentLorebookEntries(); } catch { return null; }
    if (!Array.isArray(entries)) return null;
    const found = new Map();
    for (const entry of entries) {
      const match = RULEBOOK_TAG.exec(String(entry?.comment ?? ""));
      if (!match || typeof entry.content !== "string") continue;
      if (await sha256Hex(entry.content) !== match[3]) continue;
      found.set(match[1] + "@" + match[2], entry.content);
    }
    if (found.size > 1) {
      throw new Error("SAGA 룰북 모듈이 여러 개 켜져 있습니다 (" + [...found.keys()].join(", ") + "). 하나만 켜고 다시 시도하세요.");
    }
    const [content] = found.values();
    return content ? { content } : null;
  }

  function extractJsonProposal(text) {
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
      if (parsed && typeof parsed === "object" && typeof parsed.kind === "string" && parsed.kind) {
        const payload = parsed.payload && typeof parsed.payload === "object" && !Array.isArray(parsed.payload) ? parsed.payload : {};
        return { kind: parsed.kind, payload };
      }
    } catch {
      return null;
    }
    return null;
  }

  function createClient(api, uuid = randomId) {
    const tickets = new Map();
    const pendingFooters = new Map();
    let identityPromise;
    let busy = false;
    let disposed = false;
    const argument = async name => String((await api.getArgument(name)) ?? "").trim();
    const isTruthy = value => ["1", "true", "yes", "on"].includes(String(value ?? "").trim().toLowerCase());

    async function current() {
      const selection = await api.getCurrentCharacterIndex();
      const character = await api.getCharacter();
      if (selection !== await api.getCurrentCharacterIndex()) throw new Error("선택한 캐릭터가 변경되었습니다. 다시 생성하세요.");
      return snapshotOf(character, selection);
    }

    async function config() {
      const base = await argument("server_url") || "http://saga-runtime:4850";
      const url = new URL(base);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) {
        throw new Error("SAGA 서버의 기본 URL을 확인하세요.");
      }
      let instance = await argument("host_instance_id");
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
      const rawMode = (await argument("integration_mode")).toLowerCase();
      const integrationMode = rawMode === "hook" ? "hook" : "provider";
      const providerStream = isTruthy(await argument("provider_stream"));
      const useAuxIntent = isTruthy(await argument("use_risu_aux_intent"));
      return { base: url.origin, instance, credentials, integrationMode, providerStream, useAuxIntent };
    }

    async function readHookChats() {
      if (!api.pluginStorage || typeof api.pluginStorage.getItem !== "function") return {};
      const saved = await api.pluginStorage.getItem(HOOK_CHATS_KEY);
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

    async function isChatHookEnabled(chatId) {
      const map = await readHookChats();
      return Boolean(map[chatId]);
    }

    async function setChatHookEnabled(chatId, enabled) {
      if (!api.pluginStorage || typeof api.pluginStorage.setItem !== "function") return false;
      const map = await readHookChats();
      if (enabled) map[chatId] = true;
      else delete map[chatId];
      await api.pluginStorage.setItem(HOOK_CHATS_KEY, JSON.stringify(map));
      return Boolean(enabled);
    }

    async function toggleChatHook() {
      const snapshot = await current();
      const next = !(await isChatHookEnabled(snapshot.chatId));
      await setChatHookEnabled(snapshot.chatId, next);
      return { chatId: snapshot.chatId, enabled: next };
    }

    const signature = value => JSON.stringify(value);
    async function assertCurrent(snapshot, settings) {
      if (disposed || signature(await current()) !== signature(snapshot) ||
          signature(await config()) !== signature(settings) || disposed) {
        throw new Error("요청 중 채팅이나 설정이 변경되었습니다. 현재 채팅에서 다시 생성하세요. 이전 결과는 기록에 보존됩니다.");
      }
    }

    async function proposeWithRisuAux(text) {
      if (typeof api.request !== "function") return null;
      try {
        const res = await api.request({
          mode: "submodel",
          allowPlugins: false,
          messages: [
            {
              role: "system",
              content: 'Classify the player message into strict JSON {"kind": "<action_kind>", "payload": {...}}. If it is ordinary roleplay/dialogue without a rule action, return {"kind": "narrative_turn", "payload": {}}. Output JSON only.',
            },
            { role: "user", content: text },
          ],
        });
        const content = typeof res === "string" ? res : (res?.content ?? res?.result ?? res?.message);
        return extractJsonProposal(content);
      } catch {
        return null;
      }
    }

    async function adjudicateHookTurn(snapshot, settings, rulebook) {
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
      if (settings.useAuxIntent) {
        const proposal = await proposeWithRisuAux(actions.at(-1).text);
        if (proposal) body.client_proposal = proposal;
      }
      const headers = { "content-type": "application/json" };
      if (settings.credentials) {
        headers["x-saga-vertex-secret"] = btoa(String.fromCharCode(...new TextEncoder().encode(settings.credentials)));
      }
      const timeout = settings.credentials ? 65000 : 20000;
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
        // Hook mode activates automatically when a SAGA rulebook module is enabled,
        // or when this chat was manually toggled ON (default OFF).
        let snapshot;
        let rulebook;
        let active = false;
        try {
          snapshot = await current();
          rulebook = await attachedRulebook(api);
          active = Boolean(rulebook) || await isChatHookEnabled(snapshot.chatId);
        } catch (error) {
          // Multiple conflicting rulebooks in an active chat must fail closed.
          if (error instanceof Error && error.message.includes("SAGA 룰북 모듈이 여러 개")) {
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
          const committed = await adjudicateHookTurn(snapshot, settings, rulebook);
          pendingFooters.set(snapshot.chatId, { messageId: latest.id, state: committed });
          return [...clean, { role: "system", content: HOOK_MARKER + "\n" + committed.prompt_block }];
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
        const legacy = state.legacy_read_only ? "\n\n기존 v1 기록은 읽기 전용입니다. 새 행동은 새 채팅에서 시작하세요." : "";
        return { success: true, content: prose + "\n\n" + statusFooter(state) + legacy };
      } catch (error) {
        return { success: false, content: error instanceof Error ? error.message : "SAGA 요청 실패" };
      } finally {
        if (acquired) busy = false;
      }
    }

    function dispose() {
      disposed = true;
      tickets.clear();
      pendingFooters.clear();
    }

    return { current, beforeRequest, afterRequest, provider, toggleChatHook, isChatHookEnabled, setChatHookEnabled, dispose };
  }

  globalThis.SagaRisu = { snapshotOf, createClient, PROVIDER, HOOK_MARKER };
  if (typeof risuai === "undefined") return;
  const client = createClient(risuai);
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
    if (typeof risuai.registerButton === "function") {
      await risuai.registerButton(
        { name: "SAGA Hook 토글", icon: "🎲", iconType: "none", location: "hamburger", id: "saga-hook-toggle" },
        () => { void client.toggleChatHook(); },
      );
    }
  }
  void install().catch(() => console.error("[SAGA] Plugin installation failed."));
})();
