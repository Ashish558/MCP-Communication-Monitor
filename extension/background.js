// Service worker: the only place that talks to the local MCP Monitor bridge.
// (A page script cannot: claude.ai's security policy blocks calls to localhost, and the
// bridge refuses anything that does not come from this extension.)

const DEFAULT_PORT = 8766;
const REQUEST_TIMEOUT_MS = 125000; // the monitor blocks after 60 s by default; allow for slow settings

async function settings() {
  const s = await chrome.storage.local.get(["bridgeToken", "bridgePort"]);
  return { token: (s.bridgeToken || "").trim(), port: Number(s.bridgePort) || DEFAULT_PORT };
}

async function callBridge(path, method, payload) {
  const { token, port } = await settings();
  if (!token) return { decision: "block", error: "No bridge token set. Open the extension options and paste it." };
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`http://127.0.0.1:${port}${path}`, {
      method,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: payload ? JSON.stringify(payload) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) return { decision: "block", error: `Bridge answered HTTP ${res.status}` };
    return await res.json();
  } catch (err) {
    // Fail closed: if the monitor cannot be reached, the request is declined.
    return { decision: "block", error: `MCP Monitor bridge not reachable (${err.name}). Is "mcp-monitor bridge" running?` };
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "decide") {
    callBridge("/decide", "POST", msg.payload).then(sendResponse);
    return true; // keep the channel open for the async answer
  }
  if (msg && msg.type === "health") {
    callBridge("/health", "GET").then(sendResponse);
    return true;
  }
  return false;
});
