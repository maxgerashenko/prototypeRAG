# LLM-driven booking scenarios

A local model (`google/gemma-4-12b`, the caller) phones the real bot (`POST /chat` through FastAPI's
TestClient: real `qwen/qwen3.6-35b-a3b`, prompt, tools, Postgres). The verdict comes only from database
and transcript assertions, never from an LLM grader. Voice, Twilio and TTS are out of scope.

## Run

```
uv run pytest -q tests/scenarios -m scenario          # all (about 15 s per scenario)
uv run pytest -q tests/scenarios -m P5                # one problem id (P2 P3 P5 P6 P8 P12 P13 P16 P19 P22)
uv run python -m tests.scenarios.harness <scenario-id>   # prints the live transcript + DB state
SCENARIO_RETRIES=0 ...                                 # default 1: a failed scenario is retried once
```

Skipped unless selected with `-m scenario` (or `RUN_SCENARIOS=1`) and LM Studio answers and lists both models,
so the default `uv run pytest -q` skips them.
Do not run two pytest sessions at once: `tests/conftest.py` deletes every domain-less business at session
end, which kills the other run's businesses.

A fresh business is created per scenario (internal booking backend, no Google, tools list_sessions,
join_session, create_booking, check_availability, cancel_booking) and deleted afterwards. Dates are relative
to now (day 1 = tomorrow). The harness limits `list_open` (tool and prompt) to the scenario's own sessions,
because the dev DB holds the real calendar's sessions and every business sees them (demo rule).
On failure the whole transcript and the DB state (bookings, seats, action_log) are printed; a scenario that
passes only on a retry raises a warning ("flaky").

## Universal invariants (every scenario)

- a: if a bot message says booked/confirmed/reserved, a confirmed booking exists for that conversation
- b: never more confirmed seats than `max_attendees`
- c: no booking without `guest_id`
- d: bot reply <= 40 words and no new list of several session times, unless the caller asked for a list/details (or the scenario sets `relist_ok: true`)
- e: a year the bot names is the current (or next) year
- f: every time the bot says has AM or PM (never `10:00` or `18:00`)
- g: the bot never creates a session: the business has exactly the sessions the setup made

Per-scenario check `session_times_only: true`: every clock time in every bot message (`10 AM`, `2:00 PM`,
`14:00`, ranges, `a.m.`) must be the local start or end of a setup session, or a time the caller said (the bot may repeat it when refusing), so an invented time fails.

## Scenarios (`scenarios.yaml`, format described at its top)

| id | problem | checks |
|---|---|---|
| session_happy_path | P2 P8 | first+last name -> booking, guest "Anna Kowalska", 2 seats, tomorrow named, one read-back |
| session_first_name_only | | bot asks the last name, then books "Marta Nowak" |
| session_declined_at_readback | P2 | caller says no -> no booking, one read-back |
| session_same_name_several_days | | "the one tomorrow" -> seat in tomorrow's session only |
| session_full | | full session -> no booking, bot says full |
| table_booking_party_and_phone | | create_booking: table, party 4, tomorrow 19:00, phone |
| cancel_by_reference_and_phone | | seeded booking cancelled, another untouched |
| changes_mind_about_time | P2 | 18:00 -> 20:00 before confirming; booking at 20:00 |
| stt_garbled_session_name | P3 | "sound of Polish session": no list/spelling demand, <= 1 yes/no, right (next) session |
| name_given_once | P6 | name given once, never asked again, <= 1 confirmation |
| context_memory_session_then_yes | P6 | chosen session + name remembered, "yes" books it |
| brevity_no_relist | P5 | after the pick, no other session names, short replies |
| session_name_with_and_without_suffix | P12 | "Sauna Polish session" tomorrow 10 AM vs "Sauna Polish" the day after 6 PM: the seat is in the morning one |
| nonexistent_session_not_offered | P13 P16 P19 | caller asks for a "Steam Bath" session that is not on the schedule: it is never offered or read back, no seats claimed, the bot says it is not there and names the real ones (if the model refuses without naming any, `offer_open_sessions` appends them in code), only real session times are said, no booking (not even an appointment for the made-up service) and no new session |
| empty_session_not_called_full | P22 | sessions with free seats (one empty, one partly booked) and one really full: no bot sentence calls a free one full / fully booked / sold out (`no_false_full`) |
