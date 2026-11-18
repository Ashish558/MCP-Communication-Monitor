# MCP Monitor browser extension (claude.ai connectors)

Covers the connectors you added **directly in claude.ai** (Figma, Gmail, Slack...). Those are run by Anthropic's cloud, so the shim and gateway cannot see them. But claude.ai asks you in the browser before running a connector tool ("Claude wants to use X from Y: Decline / Always allow / Allow once"), and the tool only runs after the browser sends that approval.

The extension sits on that approval:

1. You click **Allow once** (or press Ctrl+Enter) in claude.ai.
2. The extension **holds the click** and sends the tool name, connector and shown parameters to the local bridge.
3. The normal MCP Monitor popup opens on your PC.
4. **Allow** in our popup: the extension clicks "Allow once" for you. **Block, timeout, or any error**: it clicks "Decline".
5. "Always allow" is turned into a single "Allow once", so every call keeps going through the monitor.

Declining (button or Esc) is never delayed.

## Install (Chrome, Edge or Brave)

1. Start the daemon and the bridge, and note the token the bridge prints:

   ```bash
   .venv\Scripts\pythonw -m mcp_monitor daemon
   .venv\Scripts\python -m mcp_monitor bridge
   ```
2. Open `chrome://extensions` (or `edge://extensions`, `brave://extensions`), switch on **Developer mode**, click **Load unpacked**, and choose this `extension` folder.
3. Open the extension's **Options**, paste the token, click **Save and test** (it should say "Connected").
4. Use claude.ai with a connector. Each tool call now needs your answer in the MCP Monitor popup, after claude.ai's own prompt is approved.

## Limits (be aware)

* It works **only when claude.ai shows its approval prompt**. If a tool was set to "Always allow" earlier, claude.ai runs it without asking and the extension sees nothing. Reset such tools to "Needs approval" in the connector's settings.
* It reads the tool name and parameters **as displayed in claude.ai's prompt**, not the raw request. It depends on that prompt's wording and layout, so a claude.ai redesign can break it; it then fails safe (the click is simply not intercepted, nothing is allowed that you did not click).
* Browser only. It does not cover Claude Desktop's remote connectors.
* Fail closed: if the bridge or daemon is unreachable, the request is declined.

## Security

* Only the extension can call the bridge: it needs the secret token, binds to 127.0.0.1, and refuses normal web pages (http/https origins). Chrome/Brave send a `chrome-extension://` origin and Edge an `extension://` one.
* The page script never talks to localhost (claude.ai's security policy would block it); only the extension's background worker does.
* The extension only runs on `claude.ai` and `claude.com`.

## Testing

`test/mock-claude.html` reproduces the approval prompt. Serve the `extension` folder (`python -m http.server 8801`) and open `http://127.0.0.1:8801/test/mock-claude.html?v=allow` (or `v=block`, `v=error`) to exercise the content script without claude.ai.
