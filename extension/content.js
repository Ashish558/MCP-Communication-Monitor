// Content script for claude.ai.
//
// claude.ai shows "Claude wants to use <tool> from <connector>" with Decline / Always allow /
// Allow once. The connector tool runs only after the browser sends the approval. This script
// intercepts the *allowing* actions (click or Ctrl+Enter), asks the MCP Monitor popup on the
// PC, and then clicks "Allow once" (user said Allow) or "Decline" (Block, timeout, any error).
// "Always allow" is deliberately turned into a single "Allow once", so every call is gated.
// Declining (button or Esc) is always safe and is never intercepted.

(() => {
  "use strict";
  if (window.__mcpMonitorLoaded) return;
  window.__mcpMonitorLoaded = true;

  // No trailing \b: claude.ai prints the shortcut hint straight after the label with no space,
  // so a button's text is e.g. "Allow onceCtrl⏎" and "once" is not followed by a word boundary.
  const LABEL = {
    decline: /^\s*decline/i,
    once: /^\s*allow once/i,
    always: /^\s*always allow/i,
  };
  const BUTTONS = 'button, [role="button"]';
  console.debug("[MCP Monitor] content script active on", location.hostname);
  const bypass = new WeakSet(); // buttons we click ourselves
  let waiting = false; // a request is currently waiting for the user's decision

  // ---- finding the prompt ------------------------------------------------------------
  function findPrompt() {
    const buttons = Array.from(document.querySelectorAll(BUTTONS));
    const once = buttons.find((b) => LABEL.once.test(b.textContent || ""));
    if (!once) return null;
    // climb to the smallest ancestor that holds the sentence and the Decline button
    let root = once;
    for (let i = 0; i < 12 && root; i++, root = root.parentElement) {
      const text = root.innerText || "";
      if (/wants to use/i.test(text) && Array.from(root.querySelectorAll(BUTTONS)).some((b) => LABEL.decline.test(b.textContent || ""))) {
        const within = Array.from(root.querySelectorAll(BUTTONS));
        return {
          root,
          once: within.find((b) => LABEL.once.test(b.textContent || "")),
          always: within.find((b) => LABEL.always.test(b.textContent || "")),
          decline: within.find((b) => LABEL.decline.test(b.textContent || "")),
        };
      }
    }
    return null;
  }

  const KEYCAP = /^(esc|ctrl|shift|alt|cmd|enter|return|⏎|↵|⌘|⇧|⌥|\+)$/i;

  function describe(prompt) {
    const lines = (prompt.root.innerText || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    const headerIdx = lines.findIndex((l) => /wants to use/i.test(l));
    const header = headerIdx >= 0 ? lines[headerIdx] : lines[0] || "";
    const m = header.match(/wants to use\s+(.+?)\s+from\s+(.+)$/i);
    // a chip may be rendered on its own line, so fall back to the joined text of the first lines
    const joined = lines.slice(Math.max(0, headerIdx), Math.max(0, headerIdx) + 4).join(" ");
    const m2 = joined.match(/wants to use\s+(.+?)\s+from\s+(.+?)(?:\s{2,}|$)/i);
    const tool = (m && m[1]) || (m2 && m2[1]) || "unknown tool";
    const server = (m && m[2]) || (m2 && m2[2]) || "unknown connector";
    // The parameter rows come first; claude.ai then repeats the tool title and adds a long tool
    // description ("Show more", "Takes N inputs", "Raw schema"). Keep only the parameter rows.
    // The header may span several lines ("Claude wants to use" / tool chip / "from" / server chip),
    // so skip to the line that holds the server name before looking for the end of the parameters.
    let headerEnd = Math.max(0, headerIdx);
    for (let i = headerEnd; i < Math.min(lines.length, headerEnd + 6); i++) {
      if (lines[i].toLowerCase().includes(server.trim().toLowerCase())) {
        headerEnd = i;
        break;
      }
    }
    const body = lines.slice(headerEnd + 1);
    const stop = body.findIndex(
      (l) => l.toLowerCase() === tool.trim().toLowerCase() || /^show more$/i.test(l) || /^takes \d+ input/i.test(l)
    );
    const notButton = (l) =>
      !LABEL.decline.test(l) && !LABEL.once.test(l) && !LABEL.always.test(l) && !l.split(/\s+/).every((w) => KEYCAP.test(w));
    let details = (stop >= 0 ? body.slice(0, stop) : body).filter(notButton).join("\n");
    if (!details) details = body.filter(notButton).slice(0, 15).join("\n"); // never show an empty box
    return { tool: tool.trim(), server: server.trim(), details };
  }

  // ---- small status banner -----------------------------------------------------------
  function banner(text, bad) {
    let el = document.getElementById("mcp-monitor-banner");
    if (!text) {
      if (el) el.remove();
      return;
    }
    if (!el) {
      el = document.createElement("div");
      el.id = "mcp-monitor-banner";
      el.style.cssText =
        "position:fixed;right:16px;bottom:16px;z-index:2147483647;padding:10px 14px;border-radius:8px;" +
        "font:13px system-ui,sans-serif;color:#fff;box-shadow:0 4px 14px rgba(0,0,0,.4);max-width:360px";
      (document.body || document.documentElement).appendChild(el);
    }
    el.style.background = bad ? "#c0392b" : "#d68910";
    el.textContent = text;
  }

  // ---- the gate ----------------------------------------------------------------------
  async function gate() {
    if (waiting) return; // ignore repeated clicks while the popup is open
    const prompt = findPrompt();
    if (!prompt) return;
    waiting = true;
    banner("MCP Monitor: waiting for your decision in the popup on your PC…", false);
    const info = describe(prompt);
    console.debug("[MCP Monitor] holding approval for", info.tool, "from", info.server);
    let verdict = "block";
    let note = "";
    try {
      const answer = await chrome.runtime.sendMessage({
        type: "decide",
        payload: { client: "claude.ai in browser", server: info.server, tool: info.tool, details: info.details },
      });
      if (answer && answer.decision === "allow") verdict = "allow";
      note = (answer && (answer.error || answer.reason)) || "";
    } catch (err) {
      note = `extension error: ${err && err.message}`; // fail closed
    }
    waiting = false;

    const fresh = findPrompt(); // claude.ai may have re-rendered while we waited
    if (!fresh) {
      banner("");
      return; // the prompt is gone (e.g. declined with Esc): nothing left to answer
    }
    const target = verdict === "allow" ? fresh.once : fresh.decline;
    if (verdict === "allow") {
      banner("");
    } else {
      banner(`MCP Monitor declined this request. ${note}`.trim(), true);
      setTimeout(() => banner(""), 6000);
    }
    if (target) {
      bypass.add(target);
      target.click();
    }
  }

  function swallow(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
  }

  window.addEventListener(
    "click",
    (e) => {
      const btn = e.target instanceof Element ? e.target.closest(BUTTONS) : null;
      if (!btn || bypass.has(btn)) {
        if (btn) bypass.delete(btn);
        return;
      }
      const text = btn.textContent || "";
      if (!(LABEL.once.test(text) || LABEL.always.test(text))) return; // Decline etc. pass through
      if (!findPrompt()) return;
      swallow(e);
      gate();
    },
    true
  );

  window.addEventListener(
    "keydown",
    (e) => {
      // claude.ai shortcuts: Ctrl+Enter = Allow once, Ctrl+Shift+Enter = Always allow
      if (e.key !== "Enter" || !(e.ctrlKey || e.metaKey)) return;
      if (!findPrompt()) return;
      swallow(e);
      gate();
    },
    true
  );
})();
