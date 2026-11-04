# 🔧 Bosch ME7.5 MultiMap — Pops & Bangs ECU Fix History

**🐞 Known Issues | 🛠️ Implemented Fixes | ✅ Resolved Bugs | 🧪 Verification Status**

Technical documentation of the ECU-specific problems I encountered during the development of my Bosch ME7.5 MultiMap patcher, including their causes, the changes I implemented, and the current verification status.

> [!IMPORTANT]
> 🔥 **Pops & Bangs is supported only for the 150 / 180 / 210 / 225 HP engine families.**
>
> The actual ECU software must also contain compatible maps and code references. Engine power alone does not guarantee compatibility.

---

## 📊 ECU Compatibility Overview

| ECU Part Number | Engine Family | Status |
|---|---|---|
| `06A906032HJ` | 150 HP | ✅ Build PASS |
| `06A906032KP` | 150/180 HP | ✅ PASS |
| `06A906032RN` | 180 HP | ✅ Build PASS |
| `06A906032SJ` | 150/180 HP | ✅ Build PASS |

> [!NOTE]
> ✍️ **Manual Map Address Override**
>
> If automatic map detection cannot find a required calibration, manually entering the correct Bank 1 address is the intended solution.
>
> I will not force additional automatic detection for every ECU software version. The supplied addresses and their code references must still pass validation.

---

# 🟢 1. 06A906032HJ — 150 HP

**✅ Status: RESOLVED — BUILD PASS**

### 🔍 ECU Identification

| Parameter | Value |
|---|---|
| Part Number | `06A906032HJ` |
| Bosch HW | `0261207436` |
| Bosch SW | `1037362287` |
| Software Version | `0002` |

### 🐞 Problem 1 — Cyclic Hook and RAM Detection

**🚨 Original errors**

```text
Cyclic hook: NOT FOUND
RAM: NOT FOUND
PRE-FLIGHT INCOMPLETE
```

**❓ What caused the problem?**

The patcher could not reliably locate a suitable cyclic execution point and RAM area for the additional MultiMap runtime code.

The original validation was too restrictive for certain C167 instruction layouts.

### 🛠️ How I Fixed It

- Improved structural detection of the cyclic runtime hook.
- Added validation of the original C167 instructions.
- Improved RAM allocation and reserved-memory handling.
- Added protection against overlapping injected code and calibration blocks.
- Verified that overwritten instructions were preserved correctly.

**📍 Verified locations**

| Component | Address |
|---|---|
| Cyclic Hook | `0x0A1CCC` |
| Runtime RAM | `0x3843C0` |
| Runtime Code Cave | `0x0A5850` |
| Tach CAN Hook | `0x040388` |
| Tach Wrapper | `0x0A5A10` |

### 🐞 Problem 2 — Map Copy and Selector Validation

Earlier builds raised concerns about incorrect calibration copy offsets and selector references.

### 🛠️ How I Fixed It

I strengthened the validation process and performed a byte-level comparison between the original and patched firmware.

**🧪 Verification Results**

| Test | Result |
|---|---|
| Four-bank generation | ✅ PASS |
| 42 copied calibration blocks | ✅ MATCH |
| 14 selector hooks | ✅ VERIFIED |
| Unexpected memory changes | ✅ NONE DETECTED |

### ✅ Final Result

The identified cyclic hook, RAM allocation, and calibration-copy issues were resolved at the patch-generation level.

No further build errors are currently documented for this ECU.

---

# 🟢 2. 06A906032KP — 150/180 HP

**✅ Status: RESOLVED — PASS**

### 🔍 ECU Identification

| Parameter | Value |
|---|---|
| Part Number | `06A906032KP` |
| Bosch HW | `0261207789` |
| Bosch SW | `1037363625` |
| Software Version | `0203` |

### 🐞 Problem — KFZWMN Memory Allocation Failure

**🚨 Original error**

```text
KFZWMN: no 576-byte unreserved FF area
in stock DPP window 0x014000-0x017FFF
```

### ❓ What Caused the Problem?

The original allocation logic required a single continuous **576-byte free memory block** for three additional KFZWMN calibration copies.

However, this firmware did not contain a sufficiently large uninterrupted free region in the preferred memory window.

The ECU still had available memory, but it was distributed across smaller regions.

### 🛠️ How I Fixed It

I completely changed the KFZWMN allocation strategy.

**❌ Previous allocation**

```text
BANK 2 + BANK 3 + BANK 4
          |
          v
Single 576-byte memory block
          |
          v
ALLOCATION FAILED
```

**✅ Improved allocation**

```text
BANK 2 -> Independent 192-byte block
BANK 3 -> Independent 192-byte block
BANK 4 -> Independent 192-byte block
```

### 🔧 Additional Improvements

- Expanded available-memory searching across supported DPP regions.
- Improved individual map-copy allocation.
- Updated KFZWMN selectors to use the actual memory location of each bank.
- Added validation of DPP page boundaries.
- Prevented map blocks from crossing 16 KiB page boundaries.
- Preserved memory-overlap protection.

### ✅ Final Result

**The memory allocation issue has been resolved.**

The patcher now completes the previously failing KP patching process successfully.

No further build blocker is currently reported for this ECU.

---

# 🟢 3. 06A906032RN — 180 HP

**✅ Status: RESOLVED — BUILD PASS**

### 🔍 ECU Identification

| Parameter | Value |
|---|---|
| Part Number | `06A906032RN` |
| Bosch HW | `0261208450` |
| Bosch SW | `1037368613` |
| Software Version | `0001` |

### 🐞 Problem — Tachometer CAN Hook Not Found

**🚨 Original error**

```text
Tach CAN hook: NOT FOUND
nmot_w loads: 51
validated CAN candidates: 0
```

### ❓ What Caused the Problem?

The patcher successfully detected the required calibration maps but failed while locating the Tach CAN hook used for active-bank indication through the instrument-cluster tachometer.

The RN firmware used a different engine-RPM buffer layout.

| Buffer Layout | Address |
|---|---|
| Previous reference | `0xF824 / 0xF825` |
| RN firmware | `0xF82A / 0xF82B` |

The previous detector depended too heavily on a specific buffer structure.

### 🛠️ How I Fixed It

I redesigned the Tach CAN detection logic to recognize the structure of the RPM-to-CAN instructions instead of depending on one fixed buffer address.

**Changes included:**

- Improved detection of `nmot_w` loads.
- Analysis of C167 RPM data-transfer instructions.
- Structural verification of the CAN RPM packing sequence.
- Support for alternative buffer locations.
- Validation of the hook before modifying the firmware.
- Regression checks against previously working firmware.

### 📍 Successfully Identified Hook

```text
Tach CAN Hook: 0x04036E
```

### 🧪 Verification Results

| Test | Result |
|---|---|
| Automatic Tach CAN detection | ✅ PASS |
| 2-bank generation | ✅ PASS |
| 3-bank generation | ✅ PASS |
| 4-bank generation | ✅ PASS |
| Previous firmware regression | ✅ PASS |

### ✅ Final Result

**The Tach CAN detection issue has been resolved.**

The patcher successfully identifies the RPM-to-CAN path and completes generation for the tested bank configurations.

Actual tachometer behavior still requires hardware verification.

---

# 🟢 4. 06A906032SJ — 150/180 HP

**✅ Status: RESOLVED — BUILD PASS**

### 🔍 ECU Identification

| Parameter | Value |
|---|---|
| Part Number | `06A906032SJ` |
| Bosch HW | `0261208531` |
| Bosch SW | `1037369040` |
| Software Version | `0010` |

This ECU had three separate technical issues.

## 🐞 Problem 1 — LDRXN and LDRXNZK Not Found

**🚨 Original symptoms**

```text
LDRXN   AUTO=NOT FOUND
LDRXNZK AUTO=NOT FOUND
```

### ❓ What Caused the Problem?

The two LDR maps contained nearly flat calibration values.

The first value represented approximately 90%, while the remaining fifteen values represented approximately 210%.

The previous map-validation logic incorrectly rejected these valid calibration structures.

### 🛠️ How I Fixed It

I added structural validation for the 16-point WORD LDR maps.

The updated validation checks:

- Map structure and size.
- Correct RPM axis organization.
- Expected placement of paired LDR maps.
- Valid map payload without requiring many different byte values.

**📍 Map Addresses**

| Map | Address |
|---|---|
| LDRXN | `0x01F3CE` |
| LDRXNZK | `0x01F410` |

**✅ Result:** Both maps were successfully recognized.

---

## 🐞 Problem 2 — Cyclic Hook Not Found

### 🚨 Original Error

```text
Cyclic hook: NOT FOUND
```

### ❓ What Caused the Problem?

The firmware contained a different instruction sequence at the cyclic execution point.

The original detector did not recognize this alternative C167 instruction pattern.

### 🛠️ How I Fixed It

I added support for the alternative cyclic hook signature and ensured the original displaced instruction was preserved inside the injected runtime wrapper.

**📍 Verified Hook**

```text
Address: 0x0A6A26
Bytes:   C2 F4 9C F8
```

**✅ Result:** Cyclic hook discovery and runtime installation passed validation.

---

## 🐞 Problem 3 — LDR Map Addresses Found, but Code References Missing

### 🚨 Original Error

```text
LDRXN code   = 0x000000
LDRXNZK code = 0x000000
```

### ❓ What Caused the Problem?

Knowing the map address alone was not sufficient.

The patcher also needed to locate the C167 instructions responsible for reading those maps.

Without the correct lookup points, it could not safely install the MultiMap selectors.

### 🛠️ How I Fixed It

I improved the recovery and validation of LDR lookup sites within the firmware.

**📍 Verified Locations**

| Component | Address |
|---|---|
| LDRXNZK Lookup | `0x07CB9E` |
| LDRXN Lookup | `0x07CBB2` |
| Cyclic Hook | `0x0A6A26` |
| Tach CAN Hook | `0x04036E` |

**✅ Result:** The LDR code references were successfully resolved.

---

## 📐 Additional XDF Verification

I also investigated a small difference in LDR scaling values between XDF definitions.

| XDF Definition | Scaling |
|---|---|
| Rounded | `0.023438` |
| More precise | `0.0234375` |

The original calibration bytes were identical.

The difference was caused by numeric rounding, not corrupted map data.

No unnecessary XDF generator changes were retained.

### 🧪 SJ Verification Results

| Test | Result |
|---|---|
| LDRXN detection | ✅ PASS |
| LDRXNZK detection | ✅ PASS |
| Cyclic hook | ✅ PASS |
| LDR lookup references | ✅ PASS |
| 2-bank generation | ✅ PASS |
| 3-bank generation | ✅ PASS |
| 4-bank generation | ✅ PASS |
| 5× brake setup | ✅ Build PASS |
| Copied map data comparison | ✅ MATCH |

### ✅ Final Result

The previously reported SJ patch-generation problems have been resolved.

---

# 🧩 Summary of Implemented Improvements

| Component | Fix | Status |
|---|---|---|
| 🔧 Cyclic Hook | Improved alternative C167 instruction detection | ✅ Fixed |
| 🧠 RAM Allocation | Improved memory validation and reservation | ✅ Fixed |
| 🗺️ Map Copying | Improved calibration-copy validation | ✅ Fixed |
| 💾 KFZWMN | Independent memory allocation for each bank | ✅ Fixed |
| 📡 Tach CAN | Structural RPM-to-CAN detection | ✅ Fixed for RN |
| 📈 LDR Maps | Support for valid, nearly flat WORD maps | ✅ Fixed |
| 🔗 LDR Lookups | Improved C167 code-reference detection | ✅ Fixed |
| ✍️ Manual Addresses | Manual entry when automatic detection fails | ✅ Implemented |
| 📄 XDF | Verified calibration scaling and data integrity | ✅ Checked |

---

# 🧪 Remaining Verification

There are no currently documented patch-generation blockers for the four investigated ECU variants.

However, successful patch generation does not automatically guarantee correct firmware execution.

The following verification steps are still required:

- 🔍 Disassemble the original and modified firmware.
- 🧩 Verify all C167 hooks and runtime wrappers.
- 🧠 Check register handling and original-instruction preservation.
- 🔗 Verify selector references and bank-specific map addressing.
- 💾 Inspect injected code and memory allocation.
- 📡 Validate Tach CAN behavior.
- ✅ Verify checksums before ECU programming.
- 🚗 Perform controlled bench and vehicle tests.

> [!WARNING]
> ⚠️ **BUILD PASS does not mean vehicle-tested or flash-safe.**
>
> Successful BIN/XDF generation confirms that the patcher completed its documented build checks. Runtime correctness, ECU behavior, and compatibility must be verified separately.

---

# 🔒 Development Policy

**✅ What I will keep:**

- Successfully validated cyclic hooks.
- Working RAM and calibration allocation.
- Corrected Tach CAN detection.
- Verified LDR selectors and map references.
- Manual map-address entry.
- Existing XDF behavior unless a reproducible defect is found.

**🚫 What I will not change unnecessarily:**

- Working ECU-specific fixes.
- Automatic map detection simply to support every unknown firmware.
- Pops & Bangs eligibility rules.
- Validated code sections without evidence of a new problem.

---

# ✅ Final Status

**Four investigated Pops & Bangs-eligible ECU variants have successfully passed their previously failing patch-generation stages:**

✅ `06A906032HJ`

✅ `06A906032KP`

✅ `06A906032RN`

✅ `06A906032SJ`

The documented technical issues have been resolved at the build level or reported as successfully patched.

**🔬 Next development stage:** I will continue checking the generated firmware by disassembling and comparing the original and modified C167 code, including all hook instructions, wrappers, selector logic, memory references, and Tach CAN processing.

**This verification is still pending and will be performed before considering the generated code fully validated.**

---

**Developed by LDRXN**

[GitHub — LDRXN](https://github.com/LDRXN)
