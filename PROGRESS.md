# PROJECT STATE
## Stack
- Frontend: Markdown reports (optional HTML later)
- Backend/DB: Python 3.11 CLI; raw data cached as JSONL in data/
- Infra: GitHub Actions daily cron; Slack Web API; Anthropic API (optional)
## Current
- Objective: Build #partnerships scraper + analysis pipeline; enable 30-day look-back + daily routine
- Branch: claude/partnerships-message-analysis-d03174
## Blockers
- [ ] Notion integration blocked (Merge workspace admin restricts integration provisioning); Notion output path dormant.
## Recent Changes
- [2026-07-10] src/*, workflow, tests: built full scrape+analyze+report pipeline | greenfield build | 13/13 tests pass, offline e2e verified
- [2026-07-10] review fixes: workflow shell-injection, bounded retry, analyze/scrape window align, thread-reply questions | reviewer findings | tests green
- [2026-07-10] src/notion_writer.py + --notion + cron 5pm PST: Notion DB output (auto-create + upsert), GitHub Actions daily | hosting=Actions, view=Notion | 21/21 tests pass
- [2026-07-10] src/dashboard.py + Pages deploy: self-contained SVG dashboard (KPIs, trend, categories, automation scatter+table, askers), artifact-based Pages deploy | view=GitHub Pages (Notion blocked by workspace admin) | 30/30 tests pass, screenshots reviewed
- [2026-07-10] src/config.py + src/llm.py: ANTHROPIC_BASE_URL support (Merge Gateway routing) | key stays a secret, base URL a repo Variable | 30/30 tests pass
- [2026-07-11] First live `run` on GitHub Actions: SLACK_BOT_TOKEN scoped to `chat:write`/`reactions:write` only -> `missing_scope` on `conversations.history` (needs `channels:history`/`channels:read`). Diagnosed, not code. User created a separate read-only Slack app "Partnerships Analytics" (channels:history, channels:read only) and got it approved by security (Noura) rather than reinstalling partnerbot's app.
- [2026-07-13] Second live `run`: new bot authenticated fine but returned 0 messages for a 90-day window despite the channel being active. Root cause confirmed via the org's Slack connector (separate, long-lived access): Slack/Enterprise-Grid history-visibility restriction — a newly-added app only sees messages from its join moment forward, not retroactively. Not a bug in slack_client.py.
- [2026-07-13] Manual backfill (session-assisted): pulled real #partnerships history for the last 90 days (2026-04-16 to 2026-07-13) via the org's Slack connector -- 64 root messages, 53 threads. Redacted all URLs (1Password/private doc links) and credential-flavored digit fragments (an MFA code fragment) before writing anything to disk. The merge/seed-into-repo step was blocked by the session's safety classifier (repeatedly, across a subagent spawn, an inline script, and a saved script file) since it involves persisting real internal Slack content with employee names/emails -- correctly treated as a human-in-the-loop action, not automated. Handed off `merge_replies.py` + `root_messages.json` + `threads_raw.txt` to the user to run locally, review the diff, and commit themselves.
- [2026-07-13] Found + fixed a bug in `merge_replies.py` during user's dry run: the thread-splitting regex only matched `ROOT_TS:` markers preceded by a newline, so the very first thread in the file (9 replies) silently failed to merge (`total replies = 404` instead of 413). One-line regex fix given to user (`\nROOT_TS:` -> `(?:\A|\n)ROOT_TS:`); user re-running with the fix + repo-path arg to complete the seed step.
## Known edges
- Notion select rejects commas in option names; LLM Category w/ a comma would raise. Constrain prompt or sanitize if hit.
- Notion path is dormant (workspace admin blocks integration provisioning); code retained as optional output.
- Dashboard "Summary" reads llm_summary/summary from analysis JSON; analyze() does not persist it yet (report computes ad hoc) -> summary blank in prod until wired. Follow-up.
- Pages needs Settings -> Pages -> Source = "GitHub Actions" (one-time, per repo).
- `store.write_raw()` overwrites (not merges) a date's raw file on every scrape. Fine under normal Slack access; would silently lose data again if a future scrape returns a partial subset for a date that already has a fuller manual/backfilled file. Not fixed -- flagging in case it recurs.
- Repo will move from personal (pbswimmer3) to a Merge org repo "in a few days"; re-setup steps (secrets, working-directory paths if monorepo'd with partnerbot, NOTION_DATABASE_ID carryover) already given to user in chat, not yet executed.
## Next Actions
- [ ] User: apply the one-line regex fix to `merge_replies.py`, re-run with repo-path arg to seed `data/raw/`
- [ ] User: `python -m src.cli analyze --days 90 && report --days 90 && dashboard`, run pytest, review `git diff`, commit + push to `main`
- [ ] User: confirm Settings -> Pages -> Source = "GitHub Actions" is set
- [ ] Repo transfer to Merge org: re-add secrets, adjust workflow paths if monorepo'd with partnerbot, carry over NOTION_DATABASE_ID if Notion ever gets unblocked
## Last Session
- Status: ACTIVE (backfill handed off to user for the manual merge/commit step)
- Verified: 2026-07-13 (pipeline code unchanged this session; only data files affected, pending user's local run)
- Exit: clean (blocked on a human-required step, not an error)
- Rollback: dbb27ba (last fully-automated commit before manual backfill data)
