from mcp_monitor import protocol as p


def test_request_vs_notification_vs_response():
    assert p.is_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
    assert not p.is_request({"jsonrpc": "2.0", "method": "notifications/cancelled"})
    assert not p.is_request({"jsonrpc": "2.0", "id": None, "method": "x"})
    assert p.is_response({"jsonrpc": "2.0", "id": 1, "result": {}})
    assert not p.is_response({"jsonrpc": "2.0", "id": 1, "method": "x"})


def test_id_key_keeps_int_and_string_apart():
    assert p.id_key(1) != p.id_key("1")


def test_describe_tools_call():
    info = p.describe({"method": "tools/call", "params": {"name": "read", "arguments": {"path": "a"}}})
    assert (info.method, info.name, info.arguments) == ("tools/call", "read", {"path": "a"})


def test_describe_survives_garbage_params():
    assert p.describe({"method": "tools/call", "params": "nope"}).name == "<unknown tool>"
    assert p.describe({"method": "tools/call"}).arguments == {}


def test_denial_for_tool_call_is_an_is_error_result():
    msg = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {}}
    reply = p.denial_response(msg, "Nope.")
    assert reply["id"] == 7 and reply["result"]["isError"] is True


def test_denial_for_other_methods_is_a_json_rpc_error():
    reply = p.denial_response({"id": "x", "method": "resources/read"}, "Nope.")
    assert reply["error"]["code"] == p.BLOCKED_ERROR_CODE and reply["id"] == "x"


def test_redact_masks_secret_keys_and_value_shapes():
    out = p.redact({"api_key": "abc", "nested": {"Password": "x", "ok": 1},
                    "note": "use Bearer abcdefghijkl1234 now", "max_tokens": 50,
                    "list": [{"token": "t"}], "gh": "ghp_" + "a" * 30})
    assert out["api_key"] == "***" and out["nested"]["Password"] == "***"
    assert out["nested"]["ok"] == 1 and out["max_tokens"] == 50
    assert "abcdefghijkl1234" not in out["note"] and out["list"][0]["token"] == "***"
    assert out["gh"] == "***"


def test_sanitize_escapes_bidi_and_control_characters():
    shown = p.sanitize_text("safe‮txt.exe\x00\r")
    assert "‮" not in shown and "\\u202e" in shown and "\\u0000" in shown


def test_preview_truncates_and_reports_size():
    text, size = p.preview({"data": "x" * 10_000}, 200)
    assert size > 10_000 and "truncated" in text and len(text) < 400


def test_preview_handles_unserialisable_values():
    text, _ = p.preview({"v": {1, 2}}, 100)
    assert text


def test_client_name_from_initialize():
    msg = {"params": {"clientInfo": {"name": "claude-ai", "version": "0.1"}}}
    assert p.client_name_from_initialize(msg) == "claude-ai 0.1"
    assert p.client_name_from_initialize({}) is None


def test_sanitize_escapes_private_use_icon_glyphs():
    assert "\ue123" not in p.sanitize_text("icon \ue123 text")
