# Telex Wire (RSS feeds)

The Pi reads outlets' RSS feeds every hour, keeps what matches each topic's `watch` terms, and commits that to the data repo once a day, just before the run; the dispatcher reads it before it searches. The point is to make outlets the run cannot see or read (`reach` = `headline` or `none`: Le Monde, Libération, RTS, El País, La Vanguardia, Tagesschau, the BBC…) at least visible as headlines and summaries, and to give every outlet a fresher signal than the search index.

## How it works

```
app on the Pi, hourly at :07 ──▶ fetch the feeds listed in sources.toml (conditional GET)
  ──▶ $TELEX_DATA_DIR/wire/items/<date>.jsonl   every item, 45 days
  ──▶ match each active topic's `watch` terms
  ──▶ $TELEX_DATA_DIR/wire/<topic-id>.jsonl     matches (topic page reads these)
dispatch time − 15 min: fetch, then copy the matches to the data repo's wire/ and commit once
dispatcher run ──▶ dispatch.py wire <id> ──▶ leads + feed summaries for the report
             └──▶ dispatch.py watch <id> … ──▶ refreshed terms in the topic file
```

- **Why the Pi.** The dispatcher runs in a cloud sandbox whose shell can only reach package registries and GitHub: a `curl` to any feed is refused at its proxy (checked 26 Oct on rts.ch, lemonde.fr, liberation.fr), and its WebFetch is refused by exactly the outlets that matter here. The Pi has ordinary internet access and already pulls and pushes the data repo.
- **Feeds per outlet.** `feeds = [...]` on an outlet in `sources.toml`, usually the front page (or "latest") plus the international section. Edited on the Sources page; `dispatch.set_feeds` writes them, `load_sources` parses them.
- **Puller.** `telex/wire.py`, stdlib only (`urllib`, `xml.etree`, `html.parser`), as a loop in the app's lifespan next to sync. RSS 2.0, RSS 1.0/RDF and Atom. One request per feed with `If-None-Match`/`If-Modified-Since`, 15 s timeout, 5 MB cap, a `User-Agent` naming Telex as a personal feed reader, one second between requests to the same host. Python's bundled expat defuses entity-expansion attacks. If too many real feeds turn out malformed, `feedparser` is the one dependency worth adding.
- **What an item keeps.** `id` (guid, else link), `domain`, `feed`, `lang` (the outlet's first language), `seen` (the fetch that first saw it), `published`, `title`, `summary` (the feed's description, HTML stripped, cut at 600 characters), `url`; matches add `matched` (the terms that hit). Never `content:encoded`, Atom `content`, or the article page (see "Out of scope").
- **State.** `$TELEX_DATA_DIR/wire/state.json`, per feed: ETag, Last-Modified, the ids of the last fetch, last check, last success, last error and how many in a row, new items per day for a week, and the time of the last probable gap.
- **Matching** (`dispatch.watch_matcher`). Case- and accent-insensitive, typographic apostrophes straightened, whole words; a term with spaces is a phrase; `a & b` needs both in the same item; `word*` matches the beginning of a word. Title and summary are searched. Up to 30 terms per topic.
- **Look-back** (`dispatch.wire_start`). From the topic's previous report, but at least 7 and at most 45 days (14 for a topic with no report). The data repo keeps at most 200 items per topic.
- **Who writes `watch`.** The dispatcher after each report, through `dispatch.py watch <id> term…` (topic files are only written through `dispatch.py`), and the owner on the topic form ("Wire terms", one per line). Changing them on the form rematches at once. A topic gets no terms until its first run.
- **Commit.** At dispatch time − 15 min the job fetches once more, then copies `$TELEX_DATA_DIR/wire/*.jsonl` into the checkout's `wire/` (deleting files of topics that no longer have terms) and commits `wire: <n> items for <k> topics`. At most one commit a day, however often it fetches.

## Schedule

`TELEX_FEED_MINUTES` (default 60; 0 turns the wire off). Fetches run at 7 minutes past the hour, so they don't land with everything else on the hour.

A feed only shows its last 20 to 50 items, and a busy front-page feed can roll over in a few hours, so items published between fetches can drop out unseen. Hourly closes that for every feed in the seed list, at about 50 × 24 = 1,200 requests a day, most answered `304 Not Modified` with no body: what ordinary feed readers do. When none of the previous fetch's ids are left in a feed, the puller logs a warning and the Sources page shows "gaps this week" for it; that is the only way to see that hourly isn't enough for a feed, and the fix is then a narrower section feed.

## How the dispatcher uses it

`dispatch.py plan` gives each due topic a `wire_items` count; `dispatch.py wire <id>` prints the items since the previous report (`--since DATE`, or `--all`), grouped by outlet with its `role`, `tier` and `reach`. The rules are in `data-template/DISPATCHER.md`, "Wire":

1. **Leads.** An item from an outlet with `reach` `full` or `partial` is a page to fetch and read.
2. **Feed summaries.** For `headline` or `none` outlets the title and summary can be cited with `(feed summary)`, like `(headline only)`: never the only basis for a key claim or a number. Spoken in the show as "according to a summary we could not read in full".
3. **Corroboration.** Many outlets carrying a story is a reason to look harder, not proof.
4. **Not a quiet signal.** An empty wire means the terms matched nothing.
5. **Terms.** After each report the run refines the terms in every language it searched, keeping the owner's.

## Web app

- **Topic page.** A folded "On the wire since <previous report> · n" list under the actions: the newest 30 matching headlines, with outlet and time, linking to the article. Read from the Pi's local matches, so it moves every hour.
- **Topic form.** "Wire terms", one per line.
- **Sources page.** A "Feeds" filter; per outlet its feeds with health ("ok 09:07 · ~29/day", "failing ×3: HTTP 403", "gaps this week"), remove, an "Add feed" field, and "Find feeds", which reads the outlet's homepage once from the Pi and lists the feeds it advertises (`<link rel="alternate">`) with an add button each.

## Decisions (26 Oct 2026)

1. `(feed summary)` citations are allowed, under the rules above.
2. Default feeds per outlet: front page (or "latest") plus international.
3. Fetch hourly, commit daily.
4. The topic page shows the wire since the previous report.

## Out of scope, and why

- **Fetching article text on the Pi.** Several outlets refuse the dispatcher's fetcher on purpose (that is what `reach: headline` records), and `REACH.md` forbids routing around a refusal. Fetching the article on the Pi and handing the text to the run would do exactly that. A feed is what the publisher chose to syndicate, so its title and summary are fair to use; for the same reason the puller drops full-text fields even where a feed carries them, so every outlet gets the same treatment.
- **Paywalled text** with the owner's subscriptions: against the publishers' terms, and brittle.
- **Aggregator feeds** (Google News searches and the like) to fill the gaps left by Reuters and AP: unofficial, the links go through the aggregator, and the selection is the aggregator's.
- **Notifications.** Telex stays pull-only.

## Limits

- Summaries vary: Le Monde gives a real standfirst; some feeds give only the title.
- Keyword matching misses stories told in other words and can be noisy for big names. The run rewrites the terms after every report, which should correct both over time.
- Reuters (removed its feeds in 2020) and AP have no official RSS, so the two big wires stay invisible here; much wire copy reaches the run through outlets that carry it (CBS, ABC, PBS).
- Feed URLs change without notice; the Sources page and `check` are how you find out.

## Operating it

```sh
docker compose exec app python -m telex.wire check            # fetch every listed feed once, report on each
docker compose exec app python -m telex.wire discover rts.ch  # feeds a site advertises
docker compose exec app python -m telex.wire pull             # fetch now and rematch (no commit)
docker compose logs -f app | grep wire                        # "wire: 52 feeds, 31 new items, 0 failed"
python3 scripts/dispatch.py wire <id>                         # in the data repo: what the run will see
```

## Seed list

Checked 26 Oct 2026. ✓ = listed as live in a public feed directory (Feeder, Atlas des flux) that day. ? = a long-standing address, not checked here. "find" = use "Find feeds" on the Pi. Everything gets verified by `check` before use; the data repo's `feeds` branch seeds `sources.toml` with the ✓ and ? entries. Ordered by how much the wire adds: outlets the run can't read first.

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
