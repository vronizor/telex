# Reachability check

Finds out which outlets a research run can actually read with its own tools (WebSearch and WebFetch). The daily dispatcher runs it for outlets the owner queued from the Sources page; a monthly run checks every outlet in `sources.toml`. Nobody is watching: never ask questions.

## Steps

1. From the repository root, run `python3 scripts/dispatch.py reach-plan` (queued outlets only) or `python3 scripts/dispatch.py reach-plan --all` (every outlet, for the monthly run). It prints JSON: `check` (domains to test, with their languages if known) and `deferred` (queued beyond this run's cap; they go next time).
2. If `check` is empty, stop.
3. Test each domain:
   1. **Search.** Run a WebSearch for a current news story with `allowed_domains` set to the domain, in one of its languages. If the search errors or returns nothing from the outlet, run one unrestricted search naming the outlet and a current topic. If the outlet still does not appear, its reach is `none`; go to the next domain.
   2. **Fetch.** WebFetch one recent ordinary news article from the results: not a live blog, video or audio page, section front or sitemap. In the WebFetch prompt ask only for the approximate word count of the article body, whether it looks complete or cut off, and any paywall, login, consent-wall or access-denied signs. Never ask for or record the article's text. If the result is ambiguous (a teaser that might be a paywall), fetch one second article.
   3. **Classify:** `full` (complete article text), `partial` (lede or teaser, then a paywall or cut-off), `headline` (visible in search, but the fetch fails or is refused), `none` (not visible in search at all).
   4. Never route around a refusal: no curl or Python requests, no caches, mirrors or archive sites.
4. Write the results to a file outside the repository, tab-separated, with this header and one row per domain:

   ```
   domain	search	reach	note
   rts.ch	y	headline	fetch refused (client_error)
   ```

   `search` is `y` or `n`. `note` is short (under 80 characters), no tabs, and says what happened, never what the article said.
5. Run `python3 scripts/dispatch.py reach-record <file>`. It appends the rows to `reach/history/<today>.tsv`, updates `reach` and `checked` in `sources.toml`, and clears the queue entries it covered.
6. Commit (`reach: <n> outlets`) and push. If the push is rejected, `git pull --rebase` and push again. For a long monthly run, record, commit and push every 25 domains or so, so a run that is cut short keeps what it finished.

Do not change anything else: roles, tiers, notes and topics are the owner's.
