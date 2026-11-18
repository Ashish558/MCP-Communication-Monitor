# Test results

Results of the automated test suite, captured from a real run. For what the tests cover and what was checked by hand on the real Claude apps, see [testing.md](testing.md); for the behaviour each test proves, see [edge-cases.md](edge-cases.md).

## The run

| | |
|---|---|
| Date | 2026-11-18 |
| Machine | Windows 11 Home (10.0.26200) |
| Python | 3.13.1 |
| Libraries | pytest 9.2.0, aiohttp 3.14.5, pystray 0.19.5, Pillow 13.0.0 |
| Command | `python -m pytest -v` |
| Result | **96 passed in 42.24s** (0 failed, 0 skipped) |

## Results by file

| File | Passed | What it checks |
|---|---|---|
| `tests/test_audit.py` | 2 | Audit log contents and the parameter-privacy switch |
| `tests/test_bridge.py` | 9 | Browser bridge: token, origins (Chrome and Edge), size limits, masking, fail closed |
| `tests/test_broker.py` | 1 | Cap on simultaneous requests |
| `tests/test_gateway.py` | 20 | Real HTTP gateway: sessions, tokens, origins, concurrency, hang-ups, cancel, crash, HTTP upstream and streamed replies |
| `tests/test_hook.py` | 23 | Claude Code hook: allow/block answers, popup details, every failure blocks, the real hook process, settings-file installer |
| `tests/test_installer.py` | 12 | Config wrap/unwrap, backups, dry run, undo, corrupt files, Store-install path |
| `tests/test_integration.py` | 17 | Real shim process + fake MCP server + daemon: allow, block, concurrency, timeout, cancel, daemon down/stopped, crash, batches, garbage input |
| `tests/test_protocol.py` | 12 | Message classification, denial replies, id handling, secret masking, text sanitising, previews |
| **Total** | **96** | |

The tests start the real shim, gateway and hook as separate processes against a fake MCP server (`tests/fake_mcp_server.py`), and use a rule-driven stand-in for the popup, so they run without a person. They use a temporary data folder and never touch real Claude config. A passing "block" test means the request really was blocked and never reached the server.

## Live checks of the Claude Code hook (Figma connector)

A real `claude` process was run in a throwaway project with the hook registered, and asked to call the Figma `whoami` tool (claude.ai connector, tool name `mcp__claude_ai_Figma__whoami`). Claude Code 2.1.302.

| Setup | Outcome |
|---|---|
| Without the hook | Figma ran; the monitor saw nothing |
| Hook + daemon set to block everything | Not run: "Blocked by MCP Monitor" |
| Hook + daemon set to allow everything | Ran and returned the account handle |
| Hook + no daemon, autostart off | Not run: "The monitor is not running, so the request was blocked for safety" |
| Hook + real popup, user clicked Allow | Ran (decision recorded as "Allowed by the user") |
| Hook + real popup, user clicked Block | Not run: "Blocked by the user" |

## Real use recorded by the app's own audit log

Besides the automated tests, the app was used with the real Claude apps during development and while recording the demo. These counts come from its audit log (`audit.jsonl`); parameters are not shown, and decisions made by the headless automatic daemon during the live hook checks above are excluded.

| Interception path | Decisions | Allowed | Blocked by the user | Blocked by timeout |
|---|---|---|---|---|
| Shim: Claude Desktop | 11 | 7 | 3 | 1 |
| Shim: Claude Code | 5 | 3 | 1 | 1 |
| Gateway: Claude Web | 2 | 2 | 0 | 0 |
| Browser extension: claude.ai connectors (Figma and test connectors) | 12 | 11 | 1 | 0 |
| Claude Code hook: claude.ai Figma connector | 3 | 2 | 1 | 0 |
| **Total** | **33** | **25** | **6** | **2** |

Notes: Desktop entries come from Claude Desktop's agent-mode sessions. The gateway's block path in real use was not recorded in this log; it is covered by the automated tests (for example `test_blocked_call_never_reaches_server`).

## Every test, as reported by pytest

```
PASSED  tests/test_audit.py::test_every_request_and_decision_is_logged_with_parameters_by_default
PASSED  tests/test_audit.py::test_log_arguments_off_keeps_decisions_but_never_writes_parameters
PASSED  tests/test_bridge.py::test_allow_and_block_come_from_the_popup_and_details_are_shown
PASSED  tests/test_bridge.py::test_unanswered_request_times_out_to_block
PASSED  tests/test_bridge.py::test_requests_from_web_pages_are_refused
PASSED  tests/test_bridge.py::test_edge_and_chrome_extension_origins_are_accepted
PASSED  tests/test_bridge.py::test_request_without_origin_still_needs_the_token
PASSED  tests/test_bridge.py::test_missing_or_wrong_token_is_refused
PASSED  tests/test_bridge.py::test_bad_and_oversized_bodies_fail_closed
PASSED  tests/test_bridge.py::test_secrets_in_details_are_masked_in_the_popup
PASSED  tests/test_bridge.py::test_fails_closed_when_daemon_is_down
PASSED  tests/test_broker.py::test_requests_beyond_max_pending_are_blocked_immediately
PASSED  tests/test_gateway.py::test_initialize_returns_session_and_server_info
PASSED  tests/test_gateway.py::test_allowed_call_reaches_server_and_popup_has_details
PASSED  tests/test_gateway.py::test_blocked_call_never_reaches_server
PASSED  tests/test_gateway.py::test_tools_list_and_notifications_do_not_prompt
PASSED  tests/test_gateway.py::test_wrong_token_is_404_and_reveals_nothing
PASSED  tests/test_gateway.py::test_browser_origins_are_rejected_claude_origin_accepted
PASSED  tests/test_gateway.py::test_get_is_405_and_unknown_session_is_404
PASSED  tests/test_gateway.py::test_batches_are_refused_not_silently_allowed
PASSED  tests/test_gateway.py::test_bad_json_and_oversized_body
PASSED  tests/test_gateway.py::test_concurrent_calls_each_get_their_own_decision
PASSED  tests/test_gateway.py::test_two_sessions_are_isolated
PASSED  tests/test_gateway.py::test_unanswered_request_times_out_to_block
PASSED  tests/test_gateway.py::test_client_hanging_up_withdraws_the_pending_request
PASSED  tests/test_gateway.py::test_explicit_cancel_notification_withdraws_request
PASSED  tests/test_gateway.py::test_delete_closes_session
PASSED  tests/test_gateway.py::test_server_crash_returns_error_not_hang
PASSED  tests/test_gateway.py::test_fails_closed_when_daemon_is_down
PASSED  tests/test_gateway.py::test_http_upstream_allow_block_and_header_passthrough
PASSED  tests/test_gateway.py::test_http_upstream_sse_reply_is_streamed_through
PASSED  tests/test_gateway.py::test_http_upstream_down_gives_clean_error
PASSED  tests/test_hook.py::test_non_mcp_tools_are_not_touched
PASSED  tests/test_hook.py::test_allowed_mcp_call_returns_the_documented_allow_json
PASSED  tests/test_hook.py::test_blocked_mcp_call_exits_2_with_a_reason_for_claude
PASSED  tests/test_hook.py::test_popup_shows_server_tool_parameters_and_the_hook_client
PASSED  tests/test_hook.py::test_server_name_falls_back_to_the_tool_prefix
PASSED  tests/test_hook.py::test_secrets_in_tool_input_are_masked_in_the_popup
PASSED  tests/test_hook.py::test_skip_servers_setting_leaves_a_covered_server_alone
PASSED  tests/test_hook.py::test_disable_switch_makes_the_hook_a_no_op
PASSED  tests/test_hook.py::test_unanswered_popup_blocks_with_exit_2
PASSED  tests/test_hook.py::test_daemon_down_blocks_with_exit_2
PASSED  tests/test_hook.py::test_unreadable_input_blocks[None]
PASSED  tests/test_hook.py::test_unreadable_input_blocks[text]
PASSED  tests/test_hook.py::test_unreadable_input_blocks[42]
PASSED  tests/test_hook.py::test_unreadable_input_blocks[bad3]
PASSED  tests/test_hook.py::test_unexpected_error_inside_the_hook_blocks
PASSED  tests/test_hook.py::test_tool_name_parsing
PASSED  tests/test_hook.py::test_real_hook_process_allow_block_and_garbage
PASSED  tests/test_hook.py::test_hook_install_adds_ours_and_keeps_everything_else
PASSED  tests/test_hook.py::test_hook_install_is_idempotent_and_can_change_the_matcher
PASSED  tests/test_hook.py::test_hook_dry_run_writes_nothing
PASSED  tests/test_hook.py::test_hook_uninstall_removes_only_ours
PASSED  tests/test_hook.py::test_hook_install_creates_a_missing_settings_file_and_cleans_up_after
PASSED  tests/test_hook.py::test_hook_install_refuses_a_corrupt_settings_file
PASSED  tests/test_installer.py::test_wrap_and_unwrap_are_exact_inverses
PASSED  tests/test_installer.py::test_install_wraps_only_chosen_servers_and_keeps_everything_else
PASSED  tests/test_installer.py::test_install_all_skips_remote_servers
PASSED  tests/test_installer.py::test_dry_run_writes_nothing_and_makes_no_backup
PASSED  tests/test_installer.py::test_backup_is_created_before_change_and_original_is_never_overwritten
PASSED  tests/test_installer.py::test_install_twice_does_not_double_wrap
PASSED  tests/test_installer.py::test_uninstall_restores_original_entries
PASSED  tests/test_installer.py::test_restore_puts_back_the_original_file
PASSED  tests/test_installer.py::test_bad_inputs_raise_clear_errors
PASSED  tests/test_installer.py::test_corrupt_config_is_left_untouched
PASSED  tests/test_installer.py::test_list_and_doctor
PASSED  tests/test_installer.py::test_desktop_config_path_finds_the_microsoft_store_package
PASSED  tests/test_integration.py::test_allowed_call_reaches_server
PASSED  tests/test_integration.py::test_blocked_call_never_reaches_server
PASSED  tests/test_integration.py::test_non_intercepted_methods_pass_through_without_popup
PASSED  tests/test_integration.py::test_many_concurrent_calls_get_the_right_answer_each
PASSED  tests/test_integration.py::test_concurrent_shims_do_not_mix_up
PASSED  tests/test_integration.py::test_unanswered_request_is_blocked_after_timeout
PASSED  tests/test_integration.py::test_daemon_down_fails_closed
PASSED  tests/test_integration.py::test_daemon_down_fail_open_when_configured
PASSED  tests/test_integration.py::test_disable_switch_makes_shim_a_pass_through
PASSED  tests/test_integration.py::test_daemon_stopping_mid_request_blocks_it
PASSED  tests/test_integration.py::test_client_cancel_withdraws_pending_request
PASSED  tests/test_integration.py::test_server_crash_returns_errors_for_inflight_and_exits
PASSED  tests/test_integration.py::test_batch_requests_are_decided_individually
PASSED  tests/test_integration.py::test_large_output_and_unicode_pass_through_intact
PASSED  tests/test_integration.py::test_string_and_number_ids_are_preserved_in_denials
PASSED  tests/test_integration.py::test_garbage_lines_do_not_break_the_shim
PASSED  tests/test_integration.py::test_missing_server_command_exits_cleanly
PASSED  tests/test_protocol.py::test_request_vs_notification_vs_response
PASSED  tests/test_protocol.py::test_id_key_keeps_int_and_string_apart
PASSED  tests/test_protocol.py::test_describe_tools_call
PASSED  tests/test_protocol.py::test_describe_survives_garbage_params
PASSED  tests/test_protocol.py::test_denial_for_tool_call_is_an_is_error_result
PASSED  tests/test_protocol.py::test_denial_for_other_methods_is_a_json_rpc_error
PASSED  tests/test_protocol.py::test_redact_masks_secret_keys_and_value_shapes
PASSED  tests/test_protocol.py::test_sanitize_escapes_bidi_and_control_characters
PASSED  tests/test_protocol.py::test_preview_truncates_and_reports_size
PASSED  tests/test_protocol.py::test_preview_handles_unserialisable_values
PASSED  tests/test_protocol.py::test_client_name_from_initialize
PASSED  tests/test_protocol.py::test_sanitize_escapes_private_use_icon_glyphs
```

## Reproduce

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[tray,gateway]" pytest
.venv\Scripts\python -m pytest -v
```
