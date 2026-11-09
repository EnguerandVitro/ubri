# UBRI: Universal Brain-to-Robot Interface

When a person watches a machine make a mistake, their brain produces an error-related potential (ErrP) within about
half a second, involuntarily. UBRI is building open tools to use that signal as human feedback for robots: a
comfortable EEG device, millisecond synchronisation between the brain recording and the robot's state, and an open
dataset of people watching robots make mistakes.

Manifesto: https://getubri.com

## Status (November 2026)

Everything here has been tested on public data or in simulation. **Nothing has run on our own hardware yet**: the
evaluation bench (dry vs gel electrodes, a reference amplifier) is the next step.

| Directory | What it is | State |
|---|---|---|
| `errp-bench/` | ErrP detection benchmark on the public BNCI 2015-013 dataset (6 people watching a cursor, gel electrodes, offline): AUC 0.85 on people never seen in training without calibration, 0.79 with a 7-electrode layout; artefact study (eye blinks) | done, results in `docs/errp-benchmark.md` |
| `nrd/` | UBRI-NRD, an open data format for EEG next to robot data (LeRobot/BIDS conventions), with exact accounting of lost samples; reference reader, writer, validator and a consent-filtered exporter | v0.2, spec in `docs/data-format-v0.2.md` |
| `connect/` | acquisition chain: board-to-host frame protocol with CRC and loss accounting, LSL streaming, end-to-end simulated bench, Bluetooth host receiver | tested in simulation |
| `firmware/coeur/` | portable C99 core of the board firmware (frames, sync edges, loss counters), byte-identical to the Python encoder on a 4,874-frame scenario | tested on PC |
| `firmware/app/` | Zephyr application for the nRF52840 + ADS1299 board, with authenticated and encrypted Bluetooth (EEG is health data) | compiled, never run on hardware |
| `docs/board-v0.md` | design notes for the acquisition board | draft |

Code comments and some documents are in French; issues and pull requests in English or French are welcome.
References such as `KE#289` point to an internal log of lessons learned; each one is explained where it is used.

## Run the tests

```bash
python3 -B connect/tests/test_trame.py          # frame protocol: round trips, every single-bit flip, mutations
python3 -B connect/tests/test_recepteur_ble.py  # Bluetooth receiver against a simulated board
make -C firmware/coeur test parite              # C core: unit tests, then byte parity with Python + mutations
cd nrd && bash bootstrap.sh && .venv/bin/python -B tests/test_nrd.py   # data format
```
The ErrP benchmark needs the public BNCI 2015-013 dataset (Chavarriaga and Millán, 2010; BNCI Horizon 2020 database, about 2 GB) placed in `errp-bench/data/`; `errp-bench/prep.py` turns it into epochs.

## Safety

The board is worn on the body. During any recording, nothing connected to the person may be connected to mains
power: battery supply only, no USB link to a computer. See `docs/board-v0.md` and `firmware/app/README.md`.

## License

Code: Apache 2.0 (`LICENSE`). Documentation, data format and datasets: CC BY 4.0 (`LICENSE-docs.md`).

Contact: hello@getubri.com
