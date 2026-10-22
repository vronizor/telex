# telex-data

Private data for the Telex app: topics, reports and the instructions for the daily dispatcher run.

```
topics/<id>.toml           one file per topic (title, cadence, slot, languages, active, created, brief)
reports/<id>/<date>.md     one report per run, TOML front matter between +++ lines
queue/<id>                 an empty file means "refresh at the next run"
scripts/dispatch.py        scheduling and file format, stdlib only; also imported by the web app
DISPATCHER.md              what the scheduled run does, house rules, report format
```

`slot` is the weekday (0 = Monday) for weekly topics and the day of month (1–28) for monthly ones. Leave it out when adding a topic by hand: the next run assigns the least busy one. A new topic gets its first report at the next run, whatever its slot.

```sh
python3 scripts/dispatch.py plan --today 2026-10-23   # what a run on that day would do (assigns missing slots)
python3 -m pytest scripts                                # tests
```
