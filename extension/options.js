const $ = (id) => document.getElementById(id);

chrome.storage.local.get(["bridgeToken", "bridgePort"]).then((s) => {
  $("token").value = s.bridgeToken || "";
  $("port").value = s.bridgePort || 8766;
});

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ bridgeToken: $("token").value.trim(), bridgePort: Number($("port").value) || 8766 });
  const status = $("status");
  status.textContent = "Testing…";
  const answer = await chrome.runtime.sendMessage({ type: "health" });
  if (answer && answer.ok) {
    status.className = "ok";
    status.textContent = "Connected to the MCP Monitor bridge.";
  } else {
    status.className = "bad";
    status.textContent = (answer && answer.error) || "Could not connect. Is the bridge running?";
  }
});
