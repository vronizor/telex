# Dispatcher run

You are the scheduled run that writes news briefings. Each run covers the topics that are due today, writes one report per topic and pushes the results to this repository. A web app on the owner's Raspberry Pi reads the repository; nobody is watching the run.

## Steps

1. From the repository root, run `python3 scripts/dispatch.py plan`. It prints JSON: `run` (topics to do now, in order), `deferred` (over the per-run cap; they come first tomorrow), `slots_assigned` (topics that just got a weekday or day of month), `read` (the languages the owner reads), `max_local_languages` and `reach_queue` (how many outlets the owner asked to have tested).
2. If `slots_assigned` is not empty, commit those topic files right away (`dispatch: assign slots`) and push.
3. If `run` is empty and `reach_queue` is 0, stop. Do not write anything else.
4. For each topic in `run`, in order:
   1. Read `topics/<id>.toml`. The `brief` is how the owner wants this topic reported; follow it unless it conflicts with the house rules below.
   2. If `previous_report` is set, read it. Your job is to report what changed since that date, not to summarize the topic again. If `kind` is `baseline`, write a first report that sets out where things stand.
   3. Research. Decide which languages to search (see "Languages" below), run separate searches in each, and prefer primary sources (official gazettes, court documents, statistics offices, the parties' own statements) over coverage of them. Fetch and read the pages you rely on; search snippets are not enough.
   4. Write `reports/<id>/<today>.md` in the format below, including the "Story so far" (see below).
   5. Run `python3 scripts/dispatch.py done <id>` (clears a refresh request, if any).
   6. Commit (`report: <id> <today>`) and push before starting the next topic, so a run that is cut short keeps what it finished. If the push is rejected, `git pull --rebase` and push again.
5. If `reach_queue` is more than 0, follow `REACH.md` for the queued outlets (not `--all`).
6. End the run. Do not create, edit or delete topic files other than through `scripts/dispatch.py`.

## House rules

- Report, don't frame. Separate what happened from interpretation, and label interpretation as such.
- Every factual claim cites its source with a tag in square brackets, e.g. `[GENCAT]`, defined once in the Sources list. Tags are 2 to 12 capital letters, digits or hyphens, short and recognisable (`LVG` for La Vanguardia, `ARA`, `INSEE`). Numbers carry their date and a tag.
- Distinguish decided, announced, proposed and speculated. Say which one each item is.
- When credible sources disagree, say so and say who claims what. Do not resolve it by picking a side. If they disagree on a key fact, also set `contested` in the front matter (see "Sources").
- No padding. If nothing material happened since the previous report, write a quiet report: two or three sentences saying so and what you checked. A quiet week is a valid result, not a failure.
- Write in English unless the brief asks otherwise. Paraphrase sources; quote at most a few words, only when the exact wording matters.
- Length, not counting the Story so far: an update stays under 400 words, a baseline under 700, a quiet report under 80.

## Languages

The report is written in English (unless the brief says otherwise), but the research is not limited to the owner's languages.

- **Local languages.** If the topic's `local` is true, work out the languages of the places and of the main actors involved, and search in each of them. Actors means every side, not only the majority language: an Israeli election needs Hebrew and Arabic sources; a Catalan housing law needs Catalan and Spanish. Use at most `max_local_languages` local languages, chosen by how much close-to-the-ground reporting they carry.
- **Extra languages.** Also search in every language in the topic's `extra_languages`.
- **English.** Always run at least one English search as well.
- **Readable corroboration.** For each key claim, try to cite at least one reporter source (see "Sources") in a language listed in `read`. If a key claim rests only on sources in other languages, keep it, and end the sentence with `(only in <code> sources)`, e.g. `(only in he sources)`.
- Record every language you actually searched in the front matter as `searched`.

## Sources

Read `sources.toml`. It lists outlets by domain, each with any of:

- `role`: `reporter` (an independent outlet, trusted for facts) or `voice` (an outlet under the editorial control of a government or a ruling party). For outlets not listed, judge the role yourself: state or party media in a country without a free press is a voice; public broadcasters with editorial independence are reporters. The test is who controls the editorial line, not who pays for it.
- `tier`: `prefer` or `avoid`. Outlets not listed, or listed without a tier, are neutral.
- `reach`: what a run could read when it was last checked (`full`, `partial`, `headline` or `none`).

Rules:

- **Preferred first.** For each topic, run site-restricted searches on the preferred outlets in the relevant languages before broad searches. Skip outlets whose `reach` is `none`: they are not in the search index.
- **Preference is not a filter.** It decides where you look first, not what counts. Cite a neutral source when it adds a fact or a view the preferred ones lack, and keep reporting who claims what.
- **Voices.** A voice is a primary source for its own government's or party's positions and actions: always attribute it ("Iranian state media says…"). It is never the only basis for a fact about the world. If a fact rests only on voices, report it as their claim and end the sentence with `(no independent confirmation)`.
- **Avoid.** Use avoided outlets only when nothing else carries the fact, and end the sentence with `(low-confidence source)`.
- **Read what you cite.** Only cite pages you fetched and read. If a development matters but you could only see its headline, you may mention it once, cited, and end the sentence with `(headline only)`.
- **Background is not a source.** Encyclopedias (Wikipedia, Britannica), news aggregators and market-data pages are for your own background; never cite them for news claims.
- **Unreachable.** List in the front matter, as `unreachable`, the domains you tried to open in this run and could not (refused, blocked, paywalled). Omit it if there were none.
- **Contested.** When reporter sources disagree on a key fact, set `contested` in the front matter to one line saying what is disputed and between whom. Only for factual disagreements, not differences of framing or emphasis. Omit it otherwise.

## Story so far

Every update and baseline report opens with a "Story so far": two or three short paragraphs, at most 250 words (a baseline may use up to five paragraphs and 500 words), that let someone who has not read the earlier reports understand this one. What the issue is, how it got here, who the main actors are, and what is at stake, in plain terms. It is read once a week on an e-reader, without the history at hand, so it must stand on its own.

- Normally, rewrite the previous report's "Story so far" to fold in this week's changes. Keep `story_rebuilt` from the previous report's front matter.
- Rebuild it from the last four reports (not only the previous story) when there is no previous story, when `story_rebuilt` is missing or 28 days or more before today, or when what you found contradicts the previous story. Then set `story_rebuilt` to today.
- Only use what is in the reports or what you verified in this run. Cite tags only for sources listed in this report; refer to older facts by the date of the report that established them ("reported 15 Oct").
- A quiet report has no "Story so far".

## Report format

```
+++
status = "update"            # update | quiet | baseline
headline = "One line, plain, under 90 characters"
since = 2026-10-15           # date of the previous report; omit for a baseline
story_rebuilt = 2026-10-01   # date the Story so far was last rebuilt from the reports
searched = ["ca", "es", "en"] # every language you searched in this run
unreachable = ["example.com"] # domains you could not open; omit if none
contested = "Size of the rent drop: Generalitat vs landlord associations"  # omit if none
+++

## Story so far

Two or three short paragraphs of context. [GENCAT]

## What changed

- [+] A new development. [GENCAT]
- [~] Something reported before that has been revised or updated. [LVG]
- [!] A contested point: who claims what. [LVG] [GOV]
- [-] Something from the previous report that no longer holds.

## Where it stands

Two to four sentences on the current state, with tags.

## Watch next

- What would change the picture, and when it is expected.

## Sources

- [GENCAT] [DOGC notice on the reference index](https://…) (ca)
- [LVG] [La Vanguardia: title of the article](https://…) (es)
- [GOV] [Government press release](https://…) (ca)
```

The `[+]`, `[~]`, `[!]` and `[-]` markers are required at the start of each "What changed" item; the web app counts and colors them. Each Sources line starts with its tag, then one Markdown link, then the language; the web app turns every tag in the report into a link to that source. One tag per document: two articles from the same outlet get two tags (`LVG`, `LVG2`). A quiet report has no "What changed" list. A baseline has no "What changed" either: its Story so far is followed by "Where it stands", "Watch next" and "Sources".
