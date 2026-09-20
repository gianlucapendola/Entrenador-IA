# Entrenador-IA

A system that reads biometric data from a WHOOP band and generates the day's training session, adjusted to how the body actually is that morning.

Built to prepare for an amateur football tournament. The goal isn't to train more — it's to arrive without accumulated fatigue and without getting injured.

**Live output:** https://gianlucapendola.github.io/Entrenador-IA/

---

## What it does

Every morning, with one command:

1. Queries the WHOOP API (recovery, cycles, sleep, workouts).
2. Cleans the raw data and computes trends over the full history.
3. Decides the day's load — light, moderate or hard — from recovery, accumulated strain and days until the next match.
4. Picks exercises from the block scheduled for that weekday, dropping any that conflict with an active injury.
5. Publishes a web page that opens on a phone at the gym.

## Architecture

```
whoop_auth.py        OAuth 2.0 with WHOOP. Handles token rotation.
datos.py             Cleaning layer: raw to usable.
metricas.py          Trends, load ratio, estimated VO2 max, insights.
generar.py           Orchestrates everything and publishes.
plantilla.html       Jinja2 template with the design.
planificacion.json   Weekly structure, available equipment, constraints.
ejercicios.json      Library: execution cues, common errors, purpose in football.
```

Configuration lives in JSON, not in code. Changing the training week or adding an injury constraint requires no Python.

---

## Design decisions

### No database

WHOOP already stores the history, and the API returns it in full in four calls. Standing up persistence to duplicate data that's already stored elsewhere would be infrastructure without purpose. Everything is computed in memory on each run.

### Joining on `cycle_id`, not on date

A WHOOP cycle runs wake-to-wake, not midnight-to-midnight. The `recovery` record is scored the morning **after** the cycle it describes, so their dates never line up even though they cover the same period.

Joining on date appeared to work — no errors, plausible numbers — but it compared different days. The `cycle_id` carried by every recovery record points to its actual cycle and removes the problem at the source.

This is the kind of bug that produces wrong recommendations with no signal that anything failed.

### Nap filtering

`/activity/sleep` returns naps mixed in with main sleep. Requesting the most recent record can return a two-hour nap, which reads as a terrible night and ruins the day's recommendation. Filtered on `nap: false`.

### Median instead of mean

HRV series carry spikes from poor sensor contact. Over a short window, a single outlier shifts the mean by several points and contaminates the baseline comparison.

### 95th percentile for maximum heart rate

Poor sensor contact produces spurious one-second spikes. A reading of 203 bpm was recorded for a 23-year-old subject, above the theoretical maximum. Since estimated VO2 max is derived directly from that figure, a single artifact inflated it by seven points.

### Adaptive baseline window

Standard practice in athlete monitoring is a 7-day rolling window for sensitivity and 30 days for underlying trend. This system switches between them automatically, for a specific reason.

On 2026-09-14 the HRV series broke: it jumped from a 50–85 range to 95–156 in a single day, while resting heart rate stayed flat. In real physiology those two variables move together and in comparable proportions. When one shifts and the other doesn't respond, the change is in the measurement, not the body — here, improved signal quality after cleaning the sensor.

Averaging across a structural break mixes two scales and yields inflated comparisons. So until 30 days of post-break data accumulate, the baseline uses a short window of homogeneous data; after that, the standard 30. The switch is automatic rather than a note to self.

### Measurement-shift detection

The same divergence is surfaced to the user. When HRV moves more than 25% and that move is more than four times the movement in resting heart rate, the system flags it as a likely measurement change instead of reporting a 93% improvement.

### Plyometrics is substituted, not scaled down

On low recovery every other block drops in intensity. The power block doesn't: it's the one that transfers most to match play and the one most likely to cause injury when executed fatigued. Below the threshold it's replaced with mobility work, and the output explains why.

### The refresh token rotates

WHOOP invalidates the refresh token on every use and returns a new one. Storing the first and reusing it breaks the chain on the second attempt. Storage always overwrites.

---

## Setup

```bash
pip install requests python-dotenv pandas jinja2
```

Create a `.env` file with credentials from an app registered in the [WHOOP developer dashboard](https://developer.whoop.com):

```
WHOOP_CLIENT_ID=...
WHOOP_CLIENT_SECRET=...
```

The app needs the `offline` scope enabled — without it WHOOP returns no refresh token and re-authorization is required every hour.

One-time authorization:

```bash
python whoop_auth.py
```

## Usage

```bash
python generar.py                 # today's session, published
python generar.py --local         # generate without publishing
python generar.py 2026-09-21      # simulate another date
python generar.py --papel         # light theme for printing
```

---

## Roadmap

- Automate with GitHub Actions so no local machine is needed. Requires solving refresh token persistence — the token rotates on every use — in an environment with no disk between runs.
- Subjective log: how the match felt, any discomfort. WHOOP has nowhere to record this, and it's half the missing context.
- Replace fixed rules with a language model for cases a rule can't cover.

## Stack

Python · pandas · Jinja2 · OAuth 2.0 · GitHub Pages
