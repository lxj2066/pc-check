# PC Check

**Read-only Windows hardware audit: inventory, fault evidence, bottlenecks, and upgrade advice.**

PC Check is a Windows skill for Claude Code (and any agent that can run PowerShell). It collects hardware and crash evidence without changing a single setting, then turns it into a prioritised report.

## What it does

| Step | What happens |
|---|---|
| **Collect** | CPU, board, BIOS, RAM modules, GPU, monitors, disks, volumes, USB devices, network, audio, battery, thermals, drivers, vendor tools, event-log fault signatures, dumps, memory-diagnostic results |
| **Analyse** | ~25 deterministic rules for faults, mismatches and degradation |
| **Score** | Heuristic bottleneck model weighted by workload profile (`video`, `quant`, `office`, `mixed`) |
| **Report** | Self-contained HTML + JSON baseline, plus a short chat summary |

## What it catches

- **Hard hangs without a BSOD** — Kernel-Power 41 with `BugcheckCode=0`, no minidump
- **Failing or disappearing disks** — SMART wear, uncorrectable errors, `disk` events 51/153/157
- **Relabelled RAM** — a part number that contradicts its own capacity/rank
- **Mixed modules** — different part numbers, ranks or speeds
- **Overclocking signals** — actual clock vs rated clock, voltage above JEDEC
- **Low disk space** — P0 below 5%, P2 below 15%
- **Stale drivers / BIOS** — age-based checks
- **Broken devices** — real PnP problems, with `CM_PROB_PHANTOM` filtered out
- **Thermal pressure** — ACPI zones and `nvidia-smi`

## Requirements

- Windows 10/11, PowerShell 5.1+
- Python 3.9+ (only for the analyser; the collector runs without it)
- Administrator **only** for `-Deep` (SMART counters)

## Install

```bash
git clone https://github.com/lxj2066/pc-check.git
cp -r pc-check/skills/windows-hardware-audit ~/.claude/skills/
```

## Usage

```powershell
# 1. Collect (read-only, no admin needed)
powershell -NoProfile -ExecutionPolicy RemoteSigned -File scripts\collect.ps1

# 2. Optional: deep collect (SMART, asks for UAC)
powershell -NoProfile -ExecutionPolicy RemoteSigned -File scripts\collect.ps1 -Deep

# 3. Analyse
python scripts\analyze.py out\hardware_audit_HOST_TS.json --profile mixed
```

Outputs:

- `out/hardware_audit_<host>_<timestamp>.json` — raw baseline
- `out/hardware_audit_<host>_<timestamp>.findings.json` — findings + scores
- `out/hardware_audit_<host>_<timestamp>.html` — self-contained report

Profiles: `video` (rendering/editing), `quant` (data processing), `office` (browsers + AI tools), `mixed`.

## Severity model

| Level | Meaning | Examples |
|---|---|---|
| P0 | Data risk — act first | Disk almost full, SMART uncorrectable errors, bad memory pages |
| P1 | Stability / hardware suspicion | Hard hangs, disk I/O errors, WHEA, relabelled RAM |
| P2 | Performance / configuration | Single channel, low free space, stale drivers, high temps |
| P3 | Optimisation | Free RAM slots, OC tools, unused devices |

## Safety

- **Read-only by default.** No registry edits, no installs, no driver changes, no cleanup.
- `-Deep` only reads SMART counters and requires UAC; the skill explains why before prompting.
- Every recommendation is advisory. Executing it is a separate, explicitly confirmed step.
- Missing data is reported as `UNKNOWN` with the reason — never guessed.

## Limitations

- PSU model and wattage are not readable from software → always `UNKNOWN`.
- Consumer SATA/NVMe SMART attributes differ by vendor.
- Memory rank/organisation may need SPD access (v0.2 will add optional CPU-Z parsing).
- The bottleneck score is a relative heuristic for ordering, not an absolute benchmark.
- No prices, no shops, no "this upgrade will definitely fix it" promises.

## Repository layout

```
skills/windows-hardware-audit/
├── SKILL.md
├── references/
│   ├── probe-matrix.md
│   ├── fault-signatures.md
│   ├── matching-rules.md
│   └── upgrade-playbook.md
└── scripts/
    ├── collect.ps1
    ├── analyze.py
    └── report_template.html
```

## License

[PolyForm Noncommercial License 1.0.0](LICENSE). Commercial use requires a separate licence — see [COMMERCIAL.md](COMMERCIAL.md).

Author: 李献杰 Lyon
