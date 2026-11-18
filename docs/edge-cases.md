# Edge cases

How MCP Monitor behaves when things are unusual or go wrong, and the evidence for each claim.

**Governing rule: if the monitor cannot be sure the user said yes, the request is blocked** (`fail_mode: "closed"`, the default).

How to read the Evidence column: a test name means an automated test proves it (see [testing](testing.md)); *manual* means it was checked on the real apps; *by design* means the code does it but no automated test targets it.

## 1. Many requests at once

| Case | What happens | Evidence |
|---|---|---|
| Twenty or more calls at once on one connection | Each gets its own decision; allowed and blocked ones are never mixed up | `test_many_concurrent_calls_get_the_right_answer_each`, `test_concurrent_calls_each_get_their_own_decision` |
| Two clients use the same JSON-RPC id | Kept apart; the daemon assigns its own ids | `test_concurrent_shims_do_not_mix_up`, `test_two_sessions_are_isolated` |
| Id `1` versus id `"1"` | Treated as different; the type is preserved in a denial | `test_id_key_keeps_int_and_string_apart`, `test_string_and_number_ids_are_preserved_in_denials` |
| More requests than the popup can show | At most 3 popups are visible, the rest queue and open as earlier ones are answered | *manual*, by design (`max_visible_popups`) |
| A flood beyond `max_pending` (100) | Extra requests are blocked immediately, not queued | `test_requests_beyond_max_pending_are_blocked_immediately` |
| Held request while other traffic continues | `tools/list`, pings and notifications keep flowing and never open a popup | `test_non_intercepted_methods_pass_through_without_popup`, `test_tools_list_and_notifications_do_not_prompt` |

## 2. The user's decision

| Case | What happens | Evidence |
|---|---|---|
| No answer | Blocked after `decision_timeout` (60 s), with a clear reason | `test_unanswered_request_is_blocked_after_timeout` (shim), `test_unanswered_request_times_out_to_block` (gateway, bridge), *manual* with a real popup |
| Claude cancels while the popup is open | Popup closes, request is withdrawn, nothing runs | `test_client_cancel_withdraws_pending_request`, `test_explicit_cancel_notification_withdraws_request` |
| Claude hangs up (closes the connection) while waiting | Same: popup dismissed, request withdrawn | `test_client_hanging_up_withdraws_the_pending_request` |
| Two answers for one request | The first answer wins; later ones are ignored | by design (decision is set once) |
| Stray keypress or click when the popup appears | Buttons ignore input for 0.6 s; Enter does nothing; Esc or closing the window means Block | *manual*, by design |
| Block returns what to Claude | A normal tool result with `isError: true` and a readable message (other methods get a JSON-RPC error), so the model can explain it | `test_denial_for_tool_call_is_an_is_error_result`, `test_denial_for_other_methods_is_a_json_rpc_error` |

## 3. Failures

| Case | What happens | Evidence |
|---|---|---|
| Daemon not running | The shim tries to start it; if that fails the call is blocked with an explanation, and other traffic still works | `test_daemon_down_fails_closed` (also gateway and bridge), *manual* (autostart) |
| `fail_mode: "open"` configured and daemon down | The call is allowed (explicit opt-in only) | `test_daemon_down_fail_open_when_configured` |
| Daemon stops while a popup is open | Every waiting request is blocked | `test_daemon_stopping_mid_request_blocks_it` |
| Daemon was killed (for example when Claude Desktop restarted) | The next shim starts a new one by itself | *manual* |
| The real MCP server crashes | Every in-flight request gets an error reply; the shim exits with the server's exit code; later calls fail cleanly instead of hanging | `test_server_crash_returns_errors_for_inflight_and_exits`, `test_server_crash_returns_error_not_hang` |
| The real server exits immediately | The shim exits cleanly | `test_missing_server_command_exits_cleanly` |
| Upstream HTTP server unreachable (gateway) | A clear JSON-RPC error and no half-open session | `test_http_upstream_down_gives_clean_error` |
| Claude closes the shim | The real server is shut down; on Windows a job object also kills it if the shim is killed | by design, *manual* |
| An unexpected error inside the monitor | Caught, logged, and treated as Block | by design (every `ask` path returns a Block on error) |
| Emergency off switch | `MCP_MONITOR_DISABLE=1` makes shims plain pass-through | `test_disable_switch_makes_shim_a_pass_through` |

## 4. Unusual data

| Case | What happens | Evidence |
|---|---|---|
| Lines that are not valid JSON | They cannot be a recognised tool call, so they are passed on (the real server rejects them), logged, and never crash the shim | `test_garbage_lines_do_not_break_the_shim` |
| Very large output (500 KB) and unicode (accents, emoji, Japanese) | Passed through intact | `test_large_output_and_unicode_pass_through_intact` |
| Huge parameters | The popup and log show a truncated, masked preview (4000 characters by default) with the full size noted | `test_preview_truncates_and_reports_size` |
| Parameters that cannot be serialised | Shown as plain text, no crash | `test_preview_handles_unserialisable_values` |
| JSON-RPC batch | Shim: each item decided separately. Gateway: refused with an error, never silently allowed | `test_batch_requests_are_decided_individually`, `test_batches_are_refused_not_silently_allowed` |
| Notifications (no id) | Cannot be answered or blocked, so they pass through unchanged | `test_non_intercepted_methods_pass_through_without_popup` |
| Malformed parameters in a call | The popup still opens with what could be read | `test_describe_survives_garbage_params` |

## 5. Claude Web gateway and browser extension

| Case | What happens | Evidence |
|---|---|---|
| Wrong or missing secret in the address | Bare 404 that reveals nothing | `test_wrong_token_is_404_and_reveals_nothing` |
| Request from a browser page (wrong `Origin`) | Refused with 403 | `test_browser_origins_are_rejected_claude_origin_accepted`, `test_requests_from_web_pages_are_refused` |
| Edge's `extension://` origin | Accepted by the bridge (found when the extension first got HTTP 403 in Edge) | `test_edge_and_chrome_extension_origins_are_accepted` |
| Unknown or expired session, or no session id | 404 or a clear 400 | `test_get_is_405_and_unknown_session_is_404` |
| Session closed by the client | Later calls are refused | `test_delete_closes_session` |
| Bad JSON or an oversized body | 400 or 413, never an allow | `test_bad_json_and_oversized_body`, `test_bad_and_oversized_bodies_fail_closed` |
| Upstream replies with a stream | The stream is passed through | `test_http_upstream_sse_reply_is_streamed_through` |
| Bridge or daemon unreachable from the extension | The extension clicks Decline (fails closed) | `test_fails_closed_when_daemon_is_down` (bridge), mock page checks |
| Tool set to "Always allow" in claude.ai | claude.ai runs it without a prompt, so the extension sees nothing. Set tools to "Needs approval" | *manual* (found during testing); documented |
| "Always allow" clicked while the extension is on | Converted to a single "Allow once", so every call stays gated | mock page checks |
| claude.ai shows the tool name in separate lines | Parsed correctly; never sends an empty parameter box | mock page checks (found during testing) |

## 6. Config files and installer

| Case | What happens | Evidence |
|---|---|---|
| Config file missing, empty or invalid JSON | Clear error, nothing written | `test_bad_inputs_raise_clear_errors`, `test_corrupt_config_is_left_untouched` |
| Server already wrapped | Detected; never double-wrapped | `test_install_twice_does_not_double_wrap` |
| Remote (URL) servers | Skipped with a pointer to the gateway | `test_install_all_skips_remote_servers` |
| Preview before changing | `--dry-run` writes nothing and makes no backup | `test_dry_run_writes_nothing_and_makes_no_backup` |
| Backups | One before every change; the first (`*.original.json`) is never overwritten | `test_backup_is_created_before_change_and_original_is_never_overwritten` |
| Undo | Unwrapping is the exact inverse of wrapping, and `restore` puts the pristine file back | `test_wrap_and_unwrap_are_exact_inverses`, `test_uninstall_restores_original_entries`, `test_restore_puts_back_the_original_file` |
| Only the chosen servers change; everything else is kept | | `test_install_wraps_only_chosen_servers_and_keeps_everything_else` |
| Microsoft Store build of Claude Desktop | Config found in the Store locations | `test_desktop_config_path_finds_the_microsoft_store_package`, *manual* |
| Claude Desktop must restart | The installer says so after every change | by design |

## 7. Safety of what is shown and stored

| Case | What happens | Evidence |
|---|---|---|
| Secrets in parameters | Masked by key name and by value shape in the popup and the log | `test_redact_masks_secret_keys_and_value_shapes`, `test_secrets_in_details_are_masked_in_the_popup` |
| A tool tries to spoof the popup (invisible or right-to-left characters, icon glyphs) | Shown as visible escapes | `test_sanitize_escapes_bidi_and_control_characters`, `test_sanitize_escapes_private_use_icon_glyphs` |
| Parameters should not be stored | `log_arguments: false` keeps them out of the audit log | `test_log_arguments_off_keeps_decisions_but_never_writes_parameters` |

## 8. Known gaps and limits (honest list)

| Gap | Why it exists / what to do |
|---|---|
| Connectors that Anthropic runs inside Claude Desktop or Claude Code cannot be intercepted | Anthropic's cloud calls them; no program on the PC is in the path |
| Website connectors depend on claude.ai's own prompt | Best effort. It breaks if a tool is on "Always allow" or the prompt layout changes (it then fails safe) |
| Server-initiated requests (for example sampling) and notifications are not gated | They are not tool calls from Claude; documented |
| `resources/read` and `prompts/get` are not intercepted by default | Add them to `intercept_methods` |
| Gateway: JSON-RPC batches and server-initiated streams unsupported | Refused with a clear error |
| A late Allow after a client gave up without cancelling | A client that follows the MCP cancel rule is handled; one that does not could still see the call run after its own timeout |
| A hostile server could still open many popups | Capped at `max_pending`; there is no per-server rate limit |
| Popup over fullscreen apps, a second monitor, a locked or sleeping PC | Not tested. Popups open on the primary monitor |
| Windows only | Developed and tested on Windows 11. Other systems are untested |
| The monitor does not start at Windows login | Start the daemon yourself; the shim also starts it on first use |
| Tkinter UI and tray icon have no automated tests | Verified by hand |
| Two prompts per call on Claude Desktop/Web | Claude's own prompt, then ours. Choose "Allow once" on Claude's prompt so ours always appears |
