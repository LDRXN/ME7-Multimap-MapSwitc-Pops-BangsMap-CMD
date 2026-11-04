Bosch ME7.5 MultiMap — Pops & Bangs ECU Fix History
ECU compatibility • Original errors • Root causes • Implemented fixes • Verification status

A technical record of the issues I encountered while adapting my MultiMap builder to ECU software from the supported Pops & Bangs engine-power families.

Document scope: 06A906032HJ · 06A906032KP · 06A906032RN · 06A906032SJ
Engine-power families supported by the Pops & Bangs feature: 150 / 180 / 210 / 225 HP
Status: Previously reported patch-generation blockers addressed; code-level and vehicle validation are separate steps.
[!IMPORTANT]
Supported power family ≠ guaranteed support for every BIN. The four ECU variants below have been investigated in the context of the project's supported engine families. The actual firmware must still contain valid calibration maps, lookup sites, and sufficient safe free space. A successful patcher run does not prove correct operation in a vehicle.

[!NOTE]
Manual map entry is the intended permanent fallback. When automatic map detection cannot validate a required calibration, I use manually supplied Bank 1 map addresses. I am not extending automatic signatures indefinitely just to recognize every software version. Map addresses, axes and C167 code references must still pass validation.

Compatibility and issue summary
ECU part number	Bosch HW	Bosch SW	SW version	Engine-power family*	Patcher result	Original issue(s)
06A906032HJ	0261207436	1037362287	0002	150 HP	✅ Build PASS	Cyclic hook/RAM detection; copied-map/selector integrity
06A906032KP	0261207789	1037363625	0203	150/180 HP applications	✅ PASS reported	Insufficient contiguous allocation space for KFZWMN
06A906032RN	0261208450	1037368613	0001	180 HP application	✅ Build PASS	Tachometer CAN hook could not be identified
06A906032SJ	0261208531	1037369040	0010	150/180 HP applications	✅ Build PASS	LDR maps rejected; cyclic hook; unresolved LDR lookup calls


Power-family labels describe reported factory applications, not an unconditional horsepower guarantee from a Bosch HW/SW number alone. Check the exact vehicle and calibration before enabling Pops-specific features.
What the status means
- Build PASS: The previously failing patch-generation path completed in the investigated firmware, with the static checks described below.
- PASS reported: A previously failing ECU was reported to complete the patching process successfully; this is not equivalent to a fully independently audited vehicle test.
- No confirmed vehicle test: Runtime bank switching, tachometer indication, engine behavior, and Pops & Bangs operation have not been established solely by generating BIN/XDF files.
1. 06A906032HJ — 150 HP
Current status: ✅ Resolved at build/static-validation level
Pops & Bangs scope: ✅ Engine family eligible; actual BIN must still validate
Problem A — Cyclic runtime hook and RAM not found
What failed

Early builds could stop during pre-flight validation with messages such as:
Cyclic hook: NOT FOUND
RAM: NOT FOUND
PRE-FLIGHT INCOMPLETE
Where the problem occurred

The failure was in runtime code installation, specifically discovering a safe C167 cyclic execution point and usable RAM for the active-bank state. It was not, by itself, evidence of incorrect map contents.

Why it happened

The earlier detector and free-memory checks were too restrictive for this ECU's code and memory layout. A candidate that looked plausible was not enough: I also needed to preserve the displaced original instruction and avoid overlapping another injected block.

How I fixed it
1. Improved structural detection of the cyclic hook and validation of the original instruction bytes.
2. Improved selection and reservation of usable RAM for the MultiMap runtime.
3. Checked safe code-cave placement and protected already reserved regions from overlap.
4. Validated the runtime and selector patch locations before writing any new bytes.
Verified addresses in the analyzed firmware
Component	Address / value
Cyclic hook	0x0A1CCC
Original hook bytes	F3 F8 9D 89
Runtime code cave	0x0A5850
Runtime RAM	0x3843C0
Tach CAN hook	0x040388
Tach wrapper	0x0A5A10


Result: ✅ Fixed for the analyzed build. The cyclic hook and runtime memory checks passed.
Problem B — Confidence in copied map blocks and selector locations
What was questioned

An early build raised concerns about potential incorrect map-copy offsets and selector references, particularly for LDR-related calibration blocks in additional banks.

What I changed and checked
- Strengthened destination-range and copied-block verification.
- Compared the original and modified firmware at the byte level.
- Checked the hook locations and reserved regions against the expected patch plan.
- Confirmed that map copies preserved the original raw calibration bytes.
Static verification results
Check	Finding
Four-bank generation	PASS
Copied calibration blocks	42 checked; content matched the sources
Selector hooks	14 checked
Unexpected changes outside planned regions	None observed in that comparison


Final HJ assessment: The documented build blockers and integrity concerns were addressed in the analyzed four-bank setup, including the applicable Pops-related calibration layout. No physical vehicle validation is claimed.
2. 06A906032KP — 150/180 HP applications
Current status: ✅ Reported PASS — allocation issue resolved
Pops & Bangs scope: ✅ Eligible engine family; validate the individual BIN
Problem — KFZWMN could not be allocated in one contiguous block
Original error
KFZWMN: no 576-byte unreserved FF area
in stock DPP window 0x014000-0x017FFF
Where the problem occurred

While creating additional Pops-related map copies, the allocator attempted to reserve one large contiguous region for KFZWMN. This software did not offer a suitable free stretch in the preferred DPP window.

Why it happened

The old approach grouped three copies into one 576-byte allocation, even though the copies did not logically need to be adjacent. The inability to find that single region did not prove that the ECU lacked enough free space in total.

How I fixed it
1. Replaced the single 576-byte requirement with three independent 192-byte blocks, one for each additional bank in a four-bank configuration.
2. Expanded the free-space search across eligible DPP0–DPP3 regions.
3. Updated the KFZWMN selector to reference each copy at its actual location, including its DPP page.
4. Added safeguards preventing a map block from crossing a 16 KiB DPP page boundary.
5. Kept overlap and reserved-area validation so allocating smaller blocks would not overwrite stock code or other injected data.
Allocation change
BEFORE
  KFZWMN, banks 2–4  -> one contiguous 576 B free region required

AFTER
  KFZWMN, bank 2     -> separate 192 B block
  KFZWMN, bank 3     -> separate 192 B block
  KFZWMN, bank 4     -> separate 192 B block
Result: ✅ Fixed. The KP patcher was subsequently reported to complete successfully. The previously blocking contiguous-allocation error is closed.

What is not claimed: No evidence here establishes independently verified in-vehicle Pops behavior or complete C167 runtime correctness; successful patching and real-world validation remain distinct.
3. 06A906032RN — 180 HP application
Current status: ✅ Build PASS — Tach CAN detection resolved
Pops & Bangs scope: ✅ Eligible engine family; validate the individual BIN
Problem — Tachometer CAN hook not found
Original error
Tach CAN hook: NOT FOUND
nmot_w loads: 51
validated CAN candidates: 0
Where the problem occurred

The maps and runtime pre-flight checks could pass, but the build stopped while trying to find the code path used to prepare engine RPM for the instrument-cluster CAN message. This path is used by the optional active-bank indication via the tachometer.

Why it happened

RN used a different RPM/CAN buffer layout than the previously handled reference arrangement:
Layout	RPM buffer bytes
Earlier reference	0xF824 / 0xF825
RN	0xF82A / 0xF82B


The previous finder assumed too specific a buffer address or opcode pattern and therefore rejected the RN code path, although the firmware still had valid RPM-to-CAN logic.

How I fixed it
1. Analyzed the C167 instructions loading nmot_w and transferring RPM into the CAN output bytes.
2. Changed detection from a rigid buffer-specific match to structural validation of the RPM-to-CAN instruction sequence.
3. Kept the requirement that a hook be unambiguous and instruction-safe; I did not replace the check with an unconditional hard-coded address.
4. Checked the changed detector against an earlier reference firmware to reduce regression risk.
Validated hook for the analyzed RN BIN
Tach CAN hook = 0x04036E
Build results
Configuration	Status
Two banks	✅ PASS
Three banks	✅ PASS
Four banks	✅ PASS
Earlier reference regression	✅ Passed reported checks


Result: ✅ Fixed at patch-generation level. Tach CAN hook discovery no longer blocks RN generation. The actual tachometer movement and bank indication on a vehicle have not been independently confirmed by these tests.
4. 06A906032SJ — 150/180 HP applications
Current status: ✅ Build PASS — three technical blockers resolved
Pops & Bangs scope: ✅ Eligible engine family; validate the individual BIN
Problem A — Valid LDRXN / LDRXNZK maps were rejected
Original symptoms
LDRXN    AUTO=NOT FOUND
LDRXNZK  AUTO=NOT FOUND
Known payload addresses
Map	Payload address
LDRXN	0x01F3CE
LDRXNZK	0x01F410


Why it happened

The two valid 16-point WORD maps contained almost flat data: approximately 90% at the first point and 210% at the remaining fifteen points. The previous nonblank-data filter expected more distinct values and incorrectly rejected them.

How I fixed it

I added a targeted structural validation path for the paired 16-point LDR WORD-map layout. It checks the expected map size, placement, and increasing RPM axis instead of rejecting a valid calibration simply because its data contain few unique byte values.

Result: ✅ Fixed for the analyzed SJ BIN. Both LDR maps were recognized in the tested software.
[!NOTE]
This does not change the project's general policy. If another ECU's maps are not detected, manual address entry remains the intended fallback. I will not keep redesigning automatic detection for every new software variant.

Problem B — Cyclic hook not found
What failed

The runtime hook scanner did not recognize a valid alternative instruction sequence in SJ.

How I fixed it
- Added a tightly validated secondary C167 hook signature.
- Preserved the original displaced operation in the injected wrapper, rather than silently discarding it.
- Kept the existing entry-point safety checks.
Cyclic hook   = 0x0A6A26
Original bytes = C2 F4 9C F8
Result: ✅ Fixed at build level. Runtime hook selection and installation passed the tested validation.
Problem C — Map addresses were known but LDR code references were missing
Original symptom

Even after entering the correct LDR map addresses, the patcher could still report:
LDRXN code   = 0x000000
LDRXNZK code = 0x000000
Why it happened

A map's payload address identifies calibration data. It does not automatically identify the C167 instructions that read those data. MultiMap selectors need both the map locations and the relevant lookup/call sites.

How I fixed it

I added recovery and validation of the C167 LDR lookup sites for this instruction layout.
Component	Verified location
LDRXNZK lookup	0x07CB9E
LDRXN lookup	0x07CBB2
Cyclic hook	0x0A6A26
Tach CAN hook	0x04036E


Result: ✅ Fixed. The required LDR calls were resolved; the copied LDR data matched the original raw bytes.
XDF consistency check (not an unresolved map bug)
I also investigated a minor difference in how LDR values were displayed by different XDF definitions:
Definition	Scaling factor
Rounded value	0.023438
More precise value	0.0234375


The underlying map bytes were identical. The small displayed difference came from numeric rounding, not corrupted LDR data. I kept the accepted XDF behavior and did not include unrelated generator changes in the final fixes.
SJ build verification
Test	Result
LDR map validation	✅ PASS
Cyclic hook discovery	✅ PASS
LDR lookup-site resolution	✅ PASS
Two-bank generation	✅ PASS
Three-bank generation	✅ PASS
Four-bank generation	✅ PASS
Three-bank / 5× brake selector setup	✅ Build PASS
Raw copied LDR data comparison	✅ Matched original data


Final SJ assessment: ✅ Previously identified build blockers resolved. Runtime operation still needs separate code-level and vehicle testing.
Cross-ECU technical changes
Area	Original weakness	What I changed	Current decision
Runtime hook detection	Some valid C167 entry patterns were not recognized	Added structural verification and preserved original displaced instructions	✅ Keep validated fixes
Runtime RAM / code caves	Unsafe or overly restrictive placement could block a build	Improved candidate checking and reservation/overlap protection	✅ Keep validated fixes
Multi-bank map copies	Potential copy-offset or map-reference mismatch	Added byte-level comparisons and selector validation	✅ Keep validated fixes
KFZWMN free-space allocation	Required one contiguous 576 B region	Split allocation into independent 192 B blocks with DPP safeguards	✅ Keep validated fixes
Tach CAN detection	Buffer-specific assumptions missed an alternate RPM packing path	Validated instruction relationships rather than one fixed buffer	✅ Keep validated fixes
LDR map validation	Nearly flat but valid maps rejected	Added a targeted 16-point WORD-structure check	✅ Keep validated fixes
LDR lookup sites	Map payload known, but call sites unresolved	Recovered and validated C167 map-access instructions	✅ Keep validated fixes
Manual map fallback	Automatic search could stop the workflow	Added manual Bank 1 address entry with validation	✅ Closed by design


What remains to be done?
There is no currently documented patch-generation blocker for these four investigated ECU files. However, the following are validation activities, not claimed new bugs:
- Disassemble original and modified BINs and compare C167 branches, hooks, wrappers, register handling and return paths.
- Check each injected selector's original-instruction preservation, bank addressing and DPP usage.
- Validate copied calibration content, available free space and checksum correction before any programming.
- Perform controlled bench testing and, where appropriate, vehicle testing of bank switching, tachometer indications and Pops & Bangs behavior.
[!WARNING]
A Build PASS is not a flash-safety certificate. ECU software changes can cause unexpected engine or communication behavior. Static inspection, correct checksums and controlled validation are still required.

Development policy going forward
- Do not rewrite working ECU-specific fixes without a reproducible regression.
- Do not force automatic map detection: use manually verified map addresses when required.
- Do not infer Pops compatibility from horsepower alone: validate the exact map layout and code references in the BIN.
- Do not claim road-tested functionality from a successful build.
Final status
Four investigated Pops-eligible ECU variants — HJ, KP, RN and SJ — now pass their previously failing patching steps. The original issues, causes and corresponding fixes are documented above. The next phase is to verify the generated C167 code and runtime behavior, not to rework already successful map-detection or allocation paths without evidence of a new fault.
