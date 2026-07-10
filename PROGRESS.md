# PROJECT STATE
## Stack
- Frontend: Markdown reports (optional HTML later)
- Backend/DB: Python 3.11 CLI; raw data cached as JSONL in data/
- Infra: GitHub Actions daily cron; Slack Web API; Anthropic API (optional)
## Current
- Objective: Build #partnerships scraper + analysis pipeline; enable 30-day look-back + daily routine
- Branch: claude/partnerships-message-analysis-d03174
## Blockers
- [ ] No live Slack access this session (connector needs OAuth; no SLACK_BOT_TOKEN). 30-day report deferred until token supplied.
## Recent Changes
- [2026-07-10] src/*, workflow, tests: built full scrape+analyze+report pipeline | greenfield build | 13/13 tests pass, offline e2e verified
- [2026-07-10] review fixes: workflow shell-injection, bounded retry, analyze/scrape window align, thread-reply questions | reviewer findings | tests green
- [2026-07-10] src/notion_writer.py + --notion + cron 5pm PST: Notion DB output (auto-create + upsert), GitHub Actions daily | hosting=Actions, view=Notion | 21/21 tests pass
- [2026-07-10] src/dashboard.py + Pages deploy: self-contained SVG dashboard (KPIs, trend, categories, automation scatter+table, askers), artifact-based Pages deploy | view=GitHub Pages (Notion blocked by workspace admin) | 30/30 tests pass, screenshots reviewed
## Known edges
- Notion select rejects commas in option names; LLM Category w/ a comma would raise. Constrain prompt or sanitize if hit.
- Notion path is dormant (workspace admin blocks integration provisioning); code retained as optional output.
- Dashboard "Summary" reads llm_summary/summary from analysis JSON; analyze() does not persist it yet (report computes ad hoc) -> summary blank in prod until wired. Follow-up.
- Pages needs Settings -> Pages -> Source = "GitHub Actions" (one-time, per repo).
## Next Actions
- [ ] User: enable Slack connector or supply SLACK_BOT_TOKEN + SLACK_CHANNEL_ID
- [ ] Run `make install && make run ARGS="--days 30"` for the 30-day look-back
- [ ] Add secrets to repo for the daily GitHub Actions cron
## Last Session
- Status: COMPLETE
- Verified: 2026-07-10 (compileall clean, 13/13 pytest, offline export e2e)
- Exit: clean
- Rollback: d4ad06c
