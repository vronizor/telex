# Telex Wire (RSS feeds) — design draft

Status: proposal, 26 Oct 2026; decisions below. Nothing here is built yet.

The Pi pulls outlets' RSS feeds every hour, keeps what matches each topic, and commits that to the data repo once a day, just before the run; the dispatcher reads it before it searches. The point is to make outlets the run cannot see or read (`reach` = `headline` or `none`: Le Monde, Libération, RTS, El País, La Vanguardia, Tagesschau, the BBC…) at least visible as headlines and summaries, and to give every outlet a fresher signal than the search index.

## Why the Pi

The dispatcher runs in a cloud sandbox whose shell can only reach package registries and GitHub: a `curl` to any feed is refused at the proxy (checked 26 Oct on rts.ch, lemonde.fr, liberation.fr). Its WebFetch can sometimes read a feed, but it is refused for exactly the outlets that matter here. The Pi has ordinary internet access and already pulls and pushes the data repo, so it fetches the feeds and the dispatcher reads the result from the checkout, as it already does for topics and reports.

## How it works

```
app on the Pi, every hour ──▶ fetch feeds listed in sources.toml (conditional GET)
  ──▶ $DATA_DIR/wire/items/<date>.jsonl   every item, 45 days, local only
  ──▶ match against each topic's `watch` terms
  ──▶ data repo: wire/<topic-id>.jsonl    matched items only, committed at dispatch − 15 min
dispatcher run ──▶ dispatch.py wire <id> ──▶ leads + feed summaries for the report
```

- **Feeds per outlet.** `sources.toml` gains `feeds = [...]` on an outlet (a list, like `languages`). Usually the outlet's "latest" or front-page feed plus its international section. Added to `OUTLET_KEYS`; `load_sources` stays the only parser.
- **Puller.** `telex/feeds.py`, stdlib only (`urllib`, `xml.etree`): RSS 2.0, Atom and RSS 1.0/RDF. A third loop in the app's lifespan, next to sync, so no new container. One request per feed, `If-None-Match`/`If-Modified-Since` from the saved ETag and Last-Modified, a 15 s timeout, a `User-Agent` naming Telex as a personal feed reader, and a pause between requests to the same host. Python 3.12 bundles an expat that defuses entity-expansion attacks, so stdlib parsing is safe; if too many real-world feeds turn out to be malformed, `feedparser` is the one dependency worth adding.
- **What an item keeps.** `id` (guid, else link), `domain`, `feed`, `published`, `seen` (first pull that saw it), `title`, `summary` (the feed's description, HTML stripped, cut at 600 characters), `url`, `lang` (from the outlet). Never `content:encoded` and never the article page (see "Out of scope").
- **Local store.** `$DATA_DIR/wire/items/<date>.jsonl` holds every item, deduplicated by `(domain, id)`, kept 45 days; `$DATA_DIR/wire/state.json` keeps per feed the ETag, Last-Modified, the last ids seen, last success, last error and items per day. Not in git: a few dozen feeds produce on the order of a thousand items a day, which would bloat the repo's history for no benefit to the dispatcher.
- **Matching.** Topics gain `watch = [...]`: terms in any language (names, places, local-language words for the issue). Case- and accent-insensitive, whole words; a term with spaces is a phrase; `a & b` requires both, for noisy words ("strike & rail"). An item matches a topic when its title or summary matches one term.
- **What goes in git.** `wire/<topic-id>.jsonl`: the matched items, newest first, pruned to the topic's look-back (the time since its previous report, at least 7 days, at most 45) and capped at 200 items. Written through `Repo.commit_and_push` as `wire: <n> new items` once a day, 15 minutes before the dispatch time, and only when something changed: one commit a day at most, whatever the fetch rate.
- **Who writes `watch`.** The dispatcher, after each report: it knows the vocabulary of the story, in every language it searched. A new command, `dispatch.py watch <id> term…`, replaces the list (topic files are only written through `dispatch.py`). The topic form gets one plain text field so the owner can edit it too. A new topic has no terms until its baseline run, and a baseline doesn't need the wire.

## Schedule

Fetch every hour, at a minute offset so the run doesn't land on the hour; `TELEX_FEED_MINUTES` (default 60) sets the interval. Commit to the data repo once a day (see "What goes in git").

A feed only shows its last 20 to 50 items, and a busy front-page feed can roll over in a few hours, so items published between pulls can drop out before anyone sees them. Hourly closes that for every feed in the seed list. The cost is small: about 50 feeds × 24 = 1,200 requests a day, most answered `304 Not Modified` with no body. Polling well-behaved feeds hourly with conditional requests is what ordinary feed readers do.

The puller still logs a warning when none of the ids from the previous fetch are left in a feed (a probable gap). It's a few lines of code, and the only way to know that hourly isn't enough for some feed. It goes to the container log and the checker's output, not to the Sources page.

## How the dispatcher uses it

A new step in research, before the searches: `python3 scripts/dispatch.py wire <id>` prints the topic's items since the previous report, grouped by outlet, each with the outlet's `role`, `tier` and `reach`.

1. **Leads.** An item from an outlet with `reach` `full` or `partial` is a page to fetch and read, like a search result.
2. **Feed summaries.** For an outlet with `reach` `headline` or `none`, the title and summary are all the run gets. It may cite them once, with a new label `(feed summary)`, the same way `(headline only)` works now: never the only basis for a key claim, never a number. The Sources line gets the item's link. Spoken form in the show: "according to a summary we could not read in full". `check-show` needs no change; DISPATCHER.md needs the label in both places.
3. **Corroboration.** Several independent outlets carrying the same story is a reason to look harder, not proof; the run still needs a readable source for the claim.
4. **Not a quiet signal.** An empty wire file does not mean nothing happened; the terms may simply have missed it. A quiet report still needs the searches.

`dispatch.py plan` adds `wire_items` per topic (the count), so the run can see at a glance where the wire has something.

## Web app

- **Sources page.** Per outlet: its feeds, with last success and items a day, and a field to add or remove a feed URL. A "Find feeds" button fetches the outlet's homepage once from the Pi and lists the feeds it advertises (`<link rel="alternate" type="application/rss+xml">`); this covers the outlets whose feed address is not in the seed list below.
- **Checker.** `docker compose exec app python -m telex.feeds check` fetches every listed feed once and prints status, item count, newest item date and whether summaries are present. Run it after seeding and after adding feeds.
- **Topic page (open).** A collapsed "On the wire since <date>" list under the latest report: titles and links, nothing else. Useful when a story moves between runs.

## Decisions

Taken 26 Oct 2026:

1. `(feed summary)` citations are allowed, under the rules above.
2. Default feeds per outlet: front page (or "latest") plus international.
3. Fetch hourly, commit daily.

Open: the topic-page list. On each topic's page in the app, under the latest report, a folded list of the headlines that matched the topic since that report, read from the Pi's local store. Between two runs of a weekly topic, it shows that something is moving without waiting for the next report.

## Out of scope, and why

- **Fetching article text on the Pi.** Several outlets refuse the dispatcher's fetcher on purpose (that is what `reach: headline` records), and `REACH.md` forbids routing around a refusal. Fetching the article on the Pi and handing the text to the run would do exactly that. A feed is what the publisher chose to syndicate, so the title and summary are fair to use; for the same reason the puller drops `content:encoded` even where a feed carries the full text, so every outlet gets the same treatment.
- **Paywalled text** with the owner's subscriptions: against the publishers' terms, and brittle (sessions expire, bot detection).
- **Aggregator feeds** (Google News searches and the like) to fill the gaps left by Reuters and AP: unofficial, the links go through the aggregator, and the result is the aggregator's selection, not the outlet's.
- **Notifications.** Telex stays pull-only.

## Limits

- Summaries vary a lot: Le Monde gives a real standfirst; some feeds give only the title.
- Keyword matching misses stories told in other words and can be noisy for big names; the dispatcher rewrites the terms after every run, which should correct both over time.
- Reuters (removed its feeds in 2020) and AP have no official RSS, so the two big wires stay invisible here. Much wire copy reaches the run through outlets that carry it (CBS, ABC, PBS).
- Feed URLs change without notice; the checker and the Sources page are how you find out.

## Build order

1. `dispatch.py`: `feeds` on outlets, `watch` on topics, the `watch` and `wire` commands, `wire_items` in `plan`, tests.
2. `telex/feeds.py`: fetch, parse, state, match, write; the `check` and `discover` commands; the hourly loop, the daily commit and `TELEX_FEED_MINUTES`; tests with fixture feeds (RSS 2.0, Atom, RDF, a malformed one).
3. Seed `feeds` in `sources.toml` from the list below, run `check` on the Pi, drop what fails.
4. `DISPATCHER.md`: the wire step, the `(feed summary)` label and its spoken form, writing `watch` after each report.
5. Sources page: feed health, add/remove, "Find feeds". Then the optional topic-page list.

## Seed list

Checked 26 Oct 2026. ✓ = listed as live in a public feed directory (Feeder, Atlas des flux) that day. ? = a long-standing address, not checked here. "find" = use "Find feeds" on the Pi. Everything gets verified by `check` before use. Ordered by how much the wire adds: outlets the run can't read first.

### Switzerland

| outlet | reach | feeds | |
|---|---|---|---|
| rts.ch | headline | `https://www.rts.ch/info/toute-info/?format=rss/news`, `…/info/suisse?format=rss/news`, `…/info/monde/?format=rss/news` | ✓ |
| nzz.ch | headline | `https://www.nzz.ch/recent.rss`, `…/international.rss`, `…/schweiz.rss` | ✓ |
| letemps.ch | headline | `https://www.letemps.ch/articles.rss` | ✓ |
| tagesanzeiger.ch | headline | `https://partner-feeds.publishing.tamedia.ch/rss/tagesanzeiger/ticker` | ✓ |
| 24heures.ch | headline | `https://partner-feeds.publishing.tamedia.ch/rss/24heures/ticker` | ? same pattern |
| tdg.ch | headline | `https://partner-feeds.publishing.tamedia.ch/rss/tdg/ticker` | ? same pattern |
| srf.ch | full | `https://www.srf.ch/news/bnf/rss/19032223` (latest), `…/1922` (international), `…/1890` (Switzerland) | ✓ |
| swissinfo.ch | full | `https://cdn.prod.swi-services.ch/rss/eng/rssxml/latest-news/rss` (English) | ✓ |
| rsi.ch, heidi.news, lecourrier.ch, watson.ch, blick.ch | | | find |

### France

| outlet | reach | feeds | |
|---|---|---|---|
| lemonde.fr | none | `https://www.lemonde.fr/rss/en_continu.xml`, `…/international/rss_full.xml`, `…/politique/rss_full.xml` | ✓ |
| liberation.fr | none | `https://www.liberation.fr/arc/outboundfeeds/rss-all/?outputType=xml` | ? |
| francetvinfo.fr | none | `https://www.francetvinfo.fr/titres.rss`, `…/monde.rss`, `…/politique.rss` | ✓ |
| rfi.fr | none | `https://www.rfi.fr/fr/rss`, `…/fr/europe/rss`, `…/fr/moyen-orient/rss` | ✓ |
| lefigaro.fr | none | `https://www.lefigaro.fr/rss/figaro_actualites.xml`, `…/figaro_international.xml` | ✓ |
| la-croix.com | none | `https://www.la-croix.com/feeds/rss/site.xml` | ✓ |
| courrierinternational.com | none | `https://www.courrierinternational.com/feed/all/rss.xml` | ✓ |
| monde-diplomatique.fr | none | `https://www.monde-diplomatique.fr/recents.xml` | ✓ |
| alternatives-economiques.fr | none | `https://www.alternatives-economiques.fr/rss.xml` | ✓ |
| france24.com | headline | `https://www.france24.com/fr/rss` | ? |
| lesechos.fr, ouest-france.fr, mediapart.fr | none | | find |

### Spain and Catalonia

| outlet | reach | feeds | |
|---|---|---|---|
| elpais.com | none | `https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada`, `https://elpais.com/rss/internacional/portada.xml` | ✓ |
| lavanguardia.com | none | `https://www.lavanguardia.com/rss/home.xml`, `…/rss/local/catalunya.xml`, `…/rss/local/barcelona.xml` | ✓ |
| rtve.es | none | `https://api2.rtve.es/rss/temas_noticias.xml` | ✓ |
| ara.cat | full | `https://www.ara.cat/rss/` | ✓ |
| 3cat.cat | full | `https://api.3cat.cat/noticies?_format=rss&origen=frontal&frontal=n324-portada-noticia&version=2.0` | ✓ |
| elperiodico.com, elconfidencial.com, eldiario.es, vilaweb.cat, civio.es | | | find |

### Germany and Austria

| outlet | reach | feeds | |
|---|---|---|---|
| tagesschau.de | none | `https://www.tagesschau.de/index~rss2.xml`, `…/ausland/index~rss2.xml` | ✓ |
| spiegel.de | none | `https://www.spiegel.de/schlagzeilen/index.rss`, `…/ausland/index.rss` | ✓ |
| zeit.de | none | `https://newsfeed.zeit.de/index` | ✓ |
| sueddeutsche.de | none | `https://rss.sueddeutsche.de/rss/Topthemen` | ✓ |
| faz.net | none | `https://www.faz.net/rss/aktuell/` | ✓ |
| dw.com | none | `https://rss.dw.com/rdf/rss-en-all`, `https://rss.dw.com/xml/rss-de-all` | ✓ |
| derstandard.at | partial | | find |

### Italy

| outlet | reach | feeds | |
|---|---|---|---|
| repubblica.it | none | `https://www.repubblica.it/rss/homepage/rss2.0.xml`, `…/rss/esteri/rss2.0.xml` | ✓ |
| corriere.it | none | none working: its feeds stopped publishing in March 2026 | ✗ |
| ansa.it, ilpost.it | full | | find |

### International (English)

| outlet | reach | feeds | |
|---|---|---|---|
| theguardian.com | none | `https://www.theguardian.com/world/rss`, `…/international/rss` | ✓ |
| nytimes.com | none | `https://rss.nytimes.com/services/xml/rss/nyt/World.xml`, `…/Europe.xml`, `…/MiddleEast.xml`, `…/Politics.xml` | ✓ |
| politico.eu | none | `https://www.politico.eu/feed/` | ✓ |
| bbc.com | none | `https://feeds.bbci.co.uk/news/world/rss.xml` | ? |
| haaretz.com | headline | `https://www.haaretz.com/srv/haaretz-latest-headlines`, `…/srv/israel-news-rss` | ✓ |
| npr.org, washingtonpost.com, timesofisrael.com, aljazeera.com | | | find |
| reuters.com, apnews.com | none | no official feeds | ✗ |
