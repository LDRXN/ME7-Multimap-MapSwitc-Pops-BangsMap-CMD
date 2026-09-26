# 🔀 Bosch ME7.5 MultiMap / MapSwitch + Pops & Bangs CMD

### Automatic Map Detection • 2–4 Banks • Cruise / Brake Switching • Pops & Bangs Maps • MIL Confirmation • Automatic XDF Generation

Open-source command-line tool for creating **MultiMap / MapSwitch firmware for Bosch ME7.5 1MB ECUs**, extended with **runtime-switchable Pops & Bangs calibration maps**.

The application automatically analyzes the ECU binary, detects the required calibration maps, creates additional calibration banks, installs the MultiMap runtime and map selectors, and generates an **XDF definition** for editing all created banks in TunerPro.

**Build MultiMap. Configure Pops & Bangs separately for every bank. Switch maps directly from the vehicle controls.**

---

## ✨ Features

* 🔎 Automatic detection of required calibration maps
* 🧠 Automatic MultiMap structure generation
* 🗺️ Supports **2–4 calibration banks**
* 🔀 Runtime map switching
* 💥 **Pops & Bangs maps independently switchable for every bank**
* 🎛️ Two selectable switching methods
* 💡 MIL / Check Engine confirmation of selected bank
* 📋 Automatic bank layout generation
* 🧩 Automatic selector installation
* 💾 Automatic output BIN creation
* 📄 Automatic CSV build report
* 🗺️ Automatic XDF generation for all MultiMap banks
* 🛡️ Original BIN is never overwritten
* ⚙️ Designed for **Bosch ME7.5 1MB**
* 🖥️ Simple interactive command-line interface
* 🔓 Open Source

---

# 🗺️ MultiMap Calibration Maps

The builder automatically searches for the main calibrations required by the MultiMap system:

| Map         | Description                            |
| ----------- | -------------------------------------- |
| **LAMFA**   | Lambda enrichment                      |
| **KFZW**    | Ignition timing                        |
| **KFZW2**   | Secondary ignition timing              |
| **LDRXN**   | Maximum specified load                 |
| **LDRXNZK** | Maximum specified load – knock related |
| **KRKTE**   | Injector constant                      |

Required axes and associated calibration data are handled automatically.

---

# 💥 Pops & Bangs Maps

This version additionally supports **runtime switching of Pops & Bangs calibration maps**.

Each MultiMap bank receives its own independent set of:

| Map           | Description                                        |
| ------------- | -------------------------------------------------- |
| **KFZWMN**    | Minimum ignition angle                             |
| **KFNWEGM**   | Overrun / fuel-cut related calibration             |
| **KFTVSA**    | Overrun delay / exhaust-related timing calibration |
| **KFTVSAKAT** | Catalyst-related overrun timing calibration        |

This means Pops & Bangs behavior can be configured independently for every bank.

Example:

```text
Bank 1 → Stock / Pops OFF
Bank 2 → Stage 1 / Mild Pops
Bank 3 → Stage 2 / Aggressive Pops
Bank 4 → Custom calibration
```

Changing the active MultiMap bank also changes the corresponding Pops & Bangs maps at runtime.

---

# 🏦 Calibration Banks

The user can create:

```text
2 Banks
3 Banks
4 Banks
```

**Bank 1 always contains the original / stock ECU calibration.**

Additional calibration banks are automatically created in available firmware space.

Each additional bank can contain:

```text
LAMFA
KFZW
KFZW2
LDRXN
LDRXNZK
KRKTE

KFZWMN
KFNWEGM
KFTVSA
KFTVSAKAT
```

Example:

```text
Bank 1 → Stock
Bank 2 → Stage 1 + Mild Pops
Bank 3 → Stage 2 + Aggressive Pops
Bank 4 → Custom / Race
```

---

# 🎛️ Map Switching

Two switching methods are available.

## 1️⃣ Clutch + Cruise Control SET / RES

Hold the **clutch pedal** and use the cruise-control buttons:

```text
Clutch + SET → Next Bank
Clutch + RES → Previous Bank
```

Example:

```text
SET:

Bank 1 → Bank 2 → Bank 3 → Bank 4 → Bank 1

RES:

Bank 4 → Bank 3 → Bank 2 → Bank 1 → Bank 4
```

---

## 2️⃣ Clutch + 5x Brake

Hold the **clutch pedal** and press/release the brake pedal **5 times**.

```text
Clutch + 5x Brake → Next Bank
```

There is no time limit between brake presses.

Example:

```text
Bank 1 → Bank 2 → Bank 3 → Bank 4 → Bank 1
```

---

# 💡 MIL Bank Confirmation

After a successful bank change, the **MIL / Check Engine light** confirms the currently selected calibration bank.

```text
Bank 1 → 1 blink
Bank 2 → 2 blinks
Bank 3 → 3 blinks
Bank 4 → 4 blinks
```

---

# 📄 ECU Definition File — `.ecu`

A matching `.ecu` file is required.

It can be generated from the original BIN using:

```text
ME7Info.exe
```

Example:

```bash
ME7Info.exe dump.bin
```

This generates:

```text
dump.ecu
```

The BIN and ECU files must use the same base filename:

```text
dump.bin
dump.ecu
```

---

# 📦 Required Files

Example working directory:

```text
ME7-Multimap-MapSwitch-Pops-BangsMap-CMD/
│
├── MapSwitch_ME7_Pops.exe
├── ME7Info.exe
├── dump.bin
└── dump.ecu
```

---

# 🚀 Usage

Generate the `.ecu` definition if necessary:

```bash
ME7Info.exe dump.bin
```

Then start the MultiMap builder:

```bash
MapSwitch_ME7_Pops.exe dump.bin
```

The application automatically finds:

```text
dump.ecu
```

You do **not** pass the `.ecu` filename as a second argument.

---

# 🗺️ Automatic XDF Generation

After a successful build, the program automatically generates an **XDF definition** containing the maps for every created bank.

This includes both the normal MultiMap calibrations and the Pops & Bangs calibrations.

Example:

```text
LAMFA
LAMFA map_2
LAMFA map_3

KFZW
KFZW map_2
KFZW map_3

KFZW2
KFZW2 map_2
KFZW2 map_3

LDRXN
LDRXN map_2
LDRXN map_3

LDRXNZK
LDRXNZK map_2
LDRXNZK map_3

KRKTE
KRKTE map_2
KRKTE map_3

KFZWMN
KFZWMN map_2
KFZWMN map_3

KFNWEGM
KFNWEGM map_2
KFNWEGM map_3

KFTVSA
KFTVSA map_2
KFTVSA map_3

KFTVSAKAT
KFTVSAKAT map_2
KFTVSAKAT map_3
```

This allows every calibration bank to be edited independently in **TunerPro**.

---

# 📦 Output Files

For example:

```text
dump_MULTIMAP_3BANK.bin
dump_MULTIMAP_3BANK.xdf
dump_MAPSWITCH_3BANK_log.csv
```

The original:

```text
dump.bin
```

is never overwritten.

---

# 🔄 Workflow

```text
Original ME7.5 BIN
        │
        ▼
    ME7Info.exe
        │
        ▼
      .ecu
        │
        ▼
MapSwitch_ME7_Pops.exe
        │
        ├── Map detection
        ├── Pops map detection
        ├── Bank creation
        ├── Runtime installation
        ├── Selector installation
        ├── Pops selectors
        ├── XDF generation
        └── CSV report
        │
        ▼
 MultiMap + Pops BIN
```

---

# ⚠️ Important

This software modifies ECU firmware.

It is intended for:

```text
ECU research
Development
Calibration work
Bench testing
Experienced ECU tuners
```

Always keep an untouched backup of the original firmware.

A successfully generated BIN does **not** automatically guarantee compatibility with every Bosch ME7.5 software version.

Always verify the generated firmware and ECU checksum before flashing.

**Bench testing is strongly recommended before use in a vehicle.**

Use this software at your own risk.

---

# 🎯 Supported ECU

Current target:

```text
Bosch ME7.5
1 MB Flash
C167 architecture
```

---

# 🛠️ Project Status

The project is under active development.

Current development focuses on:

```text
Automatic map detection
Compatibility between ME7.5 software versions
MultiMap selector generation
Pops & Bangs selector generation
Runtime validation
Automatic XDF generation
Safety checks before firmware creation
```

Testing on additional Bosch ME7.5 binaries is welcome.

---

# 🤝 Contributions

Testing, bug reports and improvements are welcome.

If you test the builder on another Bosch ME7.5 software version, please include the relevant ECU hardware/software information when reporting an issue.

---

# 📜 License

Open Source.

See the repository license for details.

---

Software License

Copyright © 2026 LDRXN

This software is free and open-source software distributed under the terms of the GNU General Public License, version 3 (GNU GPL v3).

Every user is permitted to:

- use the software free of charge for private and commercial purposes;
- copy and redistribute the original software;
- access the source code;
- study and modify the source code;
- create modified versions of the software;
- distribute modified versions of the software.

When publicly distributing a modified version of the software, the applicable copyright and license notices must be preserved and the corresponding source code must be made available under the terms of the GNU GPL v3.

The software is provided “AS IS”, without warranty, to the extent permitted by applicable law. LDRXN provides no warranty regarding the software's fitness for a particular purpose or error-free operation.

This notice is a summary of the licensing terms. The complete GNU General Public License version 3 supplied with the software constitutes the applicable license terms.

---

<h2 align="center">❤️ Want to say thank you?</h2>

<p align="center">
If this project helped you and you would like to show your appreciation,<br>
<strong>there is no need to support me financially.</strong>
</p>

<p align="center">
Instead, if you are willing and able, please consider supporting organizations<br>
that help children and people fighting serious illnesses.
</p>

<table align="center">
<tr>
<td align="center">

<a href="https://www.siepomaga.pl/">
<img src="https://img.shields.io/badge/SUPPORT-SIEPOMAGA.PL-ff4f81?style=for-the-badge" alt="Support Siepomaga.pl">
</a>

</td>
<td align="center">

<a href="https://cancerfighters.pl/">
<img src="https://img.shields.io/badge/SUPPORT-CANCER%20FIGHTERS-e30613?style=for-the-badge" alt="Support Cancer Fighters">
</a>

</td>
</tr>
</table>

<p align="center">
<strong>Thank you for using my project. ❤️</strong><br><br>
If it helped you, consider helping someone who truly needs it.<br>
Even a small contribution can make a meaningful difference.
</p>

---
