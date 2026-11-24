# Roadmap

Planned follow-ups, written for whoever (person or agent) picks them up next. Each item says why it's wanted, how it fits the current design, and how to tell whether it worked. They're roughly in priority order.

Ground rules that apply to all of them:

- `model.py` stays pure Python with no Home Assistant imports (`scripts/influx_backfill.py` loads it by file path).
- Never trade away the fallbacks: with little data the model must still degrade to the simple fit, then the seed rate.
- Validate model changes against the **input archive** (`archive.py`), not intuition. It holds once-a-minute inputs for a year or more, so changes can be replayed and compared on real history. A good harness is: replay the archive with `events_from_rows` and `detect`, hold out a time block (for example each month in turn), fit on the rest, and compare the predicted and actual time to heat each held-out session.
- Keep the maintainer's own equipment and setup out of the repo; use generic examples.

## 1. Track prediction accuracy

**Why:** confidence in the model is currently an estimate from the fit (R²). Measuring the real error is what makes the other items judgeable, and lets the safety buffer be set from evidence.

**Done so far:** each run's outcome is recorded at its ready-by time (`ReadyByData.last_run`, shown by the Last run sensor): the predicted ready time made at the start of heating, the actual time the water reached the target, and the error in minutes. Only the latest run is kept.

**What's left:** keep a rolling log of those results in the store (bounded, small) and expose:

- a sensor for the mean absolute error of recent runs, in minutes, with a state class so it gets long-term statistics
- the signed mean error as an attribute, to show bias (late versus early)

**Later:** optionally size the safety buffer automatically from a high percentile of recent errors, behind an option.

**Done when:** tests cover logging an on-time, early and late run, and the sensor appears with statistics.

## 2. Non-linear outside-temperature model

**Why:** the heating rate is fitted as linear in outside temperature and water temperature. Air-source heat pumps lose capacity non-linearly as the air gets colder (and defrost more below a few °C), so a straight line fitted to a mild season extrapolates badly into a cold one.

**What:** once there are several months of sessions spanning a wide outside range, try candidate forms and keep one only if it beats the linear fit on held-out data:

- quadratic in outside temperature
- piecewise linear with a knot (a fitted or fixed knee, typically between 0 and 7 °C)
- a physically motivated term, such as a lift-based term in `(T_water - T_outside)`

Keep the ridge regularisation and the sign checks. Add a minimum number of sessions and a minimum outside-temperature spread before a non-linear form is even considered, and fall back to the current full or simple fit otherwise. `HeatingModel.kind` should say which form is in use. Mode factors (`mode_factors`) apply on top of whatever base form is chosen.

**Done when:** a cross-validation helper (pure, in `model.py` or a sibling module) selects the form, tests use synthetic data with a known curve to show the curve is chosen when it should be and the linear fit when it shouldn't, and the change is evaluated on a real archive before release.

## 3. Correct the forecast to the outside sensor

**Why:** plans use the outside sensor for "now" and the weather entity's hourly forecast after that. If the two disagree (sensor placement, a forecast grid point some distance away), later hours are biased.

**What:** keep a rolling estimate of `sensor - forecast` (for example the median over recent hours where both exist, from the forecast's own current-hour value) and shift the forecast by it before interpolating in `outside_at`. Fade the correction back to zero over the forecast horizon if that validates better.

**Done when:** tests show a constant offset is removed, and item 1's accuracy sensor doesn't get worse after the change on real data.

## 4. Electricity price and cheap-window scheduling

**Why:** the daily schedule plans by the clock. A user on a time-of-use tariff wants heating to happen inside the cheap window, even if that means starting earlier and holding.

**What, in two steps:**

1. **Earliest start (done).** `plan_heating(earliest=...)` and the Daily earliest start entity: a daily run never starts before it, and is reported late when that leaves too little time.
2. Price-aware planning from a price entity or a list of (time, price) periods. `plan_heating` already takes `outside_at` as a function; add a price function and choose the cheapest set of heating periods that still reaches the target by the deadline, accounting for cooling between periods. Start with a single contiguous block (search over start times, scoring cost), and only move to non-contiguous blocks if it's clearly worth the added state.

**Done when:** tests cover a window that ends before the ready-by time, an earliest start that makes the run late, and a cheaper block chosen over the latest possible one.

## 5. Heating rate as a function of water temperature from detailed runs

**Why:** the model treats each session as one average rate. The archive has minute-by-minute water temperature within each run, which shows how the rate falls as the water gets hotter (and where an inverter starts modulating near the setpoint).

**What:** fit the rate against water temperature within runs (using the archive), instead of one rate per session at the session's midpoint. That gives the simulation in `simulate_heating` a better slope near the target. Combine with item 2 rather than doing both separately.

**Done when:** held-out prediction error on real archives improves, especially for runs that finish close to the setpoint.

## 6. Smaller items

- **°F support.** Temperatures are assumed to be °C throughout (rate units, thresholds, the 1 °C minimum rise). Converting on input with Home Assistant's unit converters would make it safe for °F setups.
- **Relearn after a source entity change from older entities.** The archive only replays rows recorded from the current entities. If a user swaps a sensor, a mapping from the old entity to the new one (with an offset) could let older rows be reused. Only worth it if users ask.
- **Per-schedule session length.** The session length is shared by one-off runs, and the daily schedule only has "turn off at ready-by". A per-schedule length would be more general.
