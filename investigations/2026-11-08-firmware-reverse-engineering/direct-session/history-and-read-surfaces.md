# Engineering-result lifetime, persistent history, and additional passive read surfaces

## Scope and authority

Offline audit only: no device, network, HA, production-code, updater, history activation, or engineering-setting actions. Only this report was written. Sources are candidate FlexBOSS record252 (`db8908362a9843fb9610b90dc753878c93526602108cbf023d6eea41993f2aa8`) and GridBOSS record195 (`39266b0b286c3c9e374e94a722280c7664bb8a1e260dcc3a67d1bccedbb4e235`), not installed-image proof. SRAM addresses below are full ARM byte addresses; short suffixes in tables mean `0x2000xxxx`.

Primary native exports: [FlexBOSS C](../static-analysis/engine/flexboss/export/decompiled.c), [FlexBOSS instructions](../static-analysis/engine/flexboss/export/instructions.tsv), [GridBOSS C](../static-analysis/engine/gridboss/export/decompiled.c), [GridBOSS instructions](../static-analysis/engine/gridboss/export/instructions.tsv). Function/PC references below identify the exact portions. Existing contracts: [ARM engineering](../engineering-capture/arm-contract.md), [converter engineering](../engineering-capture/converter-contract.md), [persistent-history proof](../state-logging/firmware/REPORT.md), [GridBOSS observability](../decision-tree/gridboss-inputs/REPORT.md).

**Key results:** FlexBOSS has useful initialized input120..239 beyond the current0..119 profile. Its legitimate holding5000 bank initializes **18 words, not40**. GridBOSS additionally permits local holding2000/108 and4097/41, plus ordinary input40/40 and80/40. A native arithmetic error in the earlier GridBOSS report affects its GPIO bank: the initialized local range is **4500..4509, not4516..4525**. Details and raw-source proofs follow; no speculative1100 readback is admitted.

## 1. Function07 is one retained receive result, not historical samples

FlexBOSS local handler `080430a0` reads only the fixed engineering receive object's result. Request start/count are16-bit **word offsets**, count1..127 and `start+count<=550`; there is no arbitrary RAM base. Virtual words0..4 are ten metadata bytes; payload starts at virtual word5, fixed RAM`200090dc`. Maximum externally readable payload is1090bytes. See ARM contract §§1–2, native `080430a4..080430d0`, `080430d4..08043174`.

Temporal contract, from the receive state machine described in ARM contract §2:

- One incoming header02 starts/replaces the receive result, clears readiness, resets transfer bookkeeping, and supplies the new length. Subsequent chunks assemble this **one response** in the same fixed RAM. It is not an append queue.
- Completion sets ready1 and returns RX state to0. State0 also exists before a transfer; ready distinguishes completed from merely quiescent.
- Completed bytes and ready survive repeated07 reads. There is no read-to-clear or proved expiry timer.07 updates response scratch/cached payload length`ea3e`, not an engineering acquisition trigger.
- A later header02, full RX initialization (including every valid engineering-mode setter), or reboot discards/replaces the old result. Full mode0 initialization does not cancel ARM TX or a converter sampler.
- No07 persistence/flash write exists in this bounded read path. This is volatile retained SRAM, not a nonvolatile log. Chunk reads have no generation nonce or atomic multi-chunk snapshot guarantee; a newly arriving header can replace the buffer between reads.
- The reported mode is current ARM mode, not a timestamp, command echo, generation ID, or guaranteed origin of the retained payload. Old/unsolicited results must not be called newly requested samples merely because metadata matches current mode.

A successful bounded07 read therefore means “this completed result is currently retained,” not “this result is current,” “sampler idle,” or “there is no earlier history.” Failure87/03 likewise does not prove empty history or engineering idle. The exact length/serial/start/CRC/byte-count checks in ARM contract §1 still apply, including its forced17-byte response caveat. Ordinary bounded03/04 reads do not consume the engineering result.

**GridBOSS is different:** audited ordinary/local dispatch `0800e5dc` admits03/04/06/10, not this FlexBOSS07 service. No GridBOSS07 profile is established by these sources.

### Converter sample RAM is a separate mechanism

The compiled C28x converter has a four-lane pretrigger ring at word address`10800+500*lane+index`; final sample windows are`12f40+500*lane+index` (Para075) or`12f00+500*lane+index` (Para375). Q1/Q3 echo RAM`12c40` and QD packet RAM`12240` are different again. Addresses here are **C28x word addresses**, not ARM bytes or Modbus registers. [Converter contract §§1,3–5](../engineering-capture/converter-contract.md).

These rings are acquisition working memory, not demonstrated always-running, timestamped historical logging. Q1 returns15 unsigned decimal status tokens with trailing spaces,30..90bytes, without timestamps or parentheses. QD, if independently authorized, packages one completed lane as BE16 header/count/data/additive-checksum, not the flash-record format. QD may reorder a pretrigger capture during export; no Q3/QD/QS action is proposed here. ARM07 transports whichever one converter response completed; it does not expose all ring addresses or previous responses.

Normal03/04 banks are serializers of current/cached RAM/configuration. A last-written diagnostic, retained receive result, compiled acquisition ring, and periodic persistent flash sample must remain separate evidence classes.

## 2. Exact GridBOSS256-byte persistent sample parser

This format is positively established for **GridBOSS record195**, not automatically for FlexBOSS or another installed variant. Native writer`0801dd40` (C6636..6683), uploader`0801def4` (C6686..6732), input serializer`08021ce0`, and CRC`0800d176` own it. The existing [state-logging report §6](../state-logging/firmware/REPORT.md#6-persistent-periodic-sample-log-what-yesterday-can-contain) provides instruction/literal provenance and flash-driver proof.

| Byte offsets | Exact decoding |
|---|---|
|0..1|Literal marker`a5 a5`|
|2|Unsigned **binary** year offset2000|
|3|Unsigned binary month|
|4|Unsigned binary day of month|
|5|Unsigned binary hour|
|6|Unsigned binary minute|
|7|Unsigned binary second|
|8..249|121 raw unsigned input words: `input[k]=record[8+2*k] + 256*record[9+2*k]`, k0..120|
|250..253|Physical serial-flash byte address, **big-endian**32-bit|
|254..255|CRC16/Modbus of bytes0..253, **low byte first**|

### Timestamp proof and limits

Writer copies clock bytes`2000b10c[5], [4], [3], [2], [1], [0]` into offsets2..7; there is no timestamp BCD conversion. Native clock`08032100` increments seconds and minutes by1 and rolls at integer60 (`>0x3b`), hours at24, months at13. This proves binary fields rather than interpreting raw `0x26` as year26. For example, binary2026 has year byte`0x1a`; raw`0x26` means offset38, not2026. `0801e240` passes clock[5]+2000 to`08032014`, proving the epoch. See [query4 source](../state-logging/firmware/query4/functions.json), function0801e240, and GridBOSS C18581..18632.

No timezone, UTC offset, subsecond, monotonic sample counter, synchronization quality, or clock-valid flag is encoded in these six bytes. Calendar validity must be assessed separately; preserve malformed/raw timestamps rather than inventing dates or silently applying BCD. All six values are separate loads, with no demonstrated atomic clock snapshot. A clock correction or rollover can therefore defeat strict timestamp ordering or coherence. The8-bit offset does not justify assuming an epoch rollover policy beyond what the binary field stores.

### CRC contract, not merely “some checksum”

`0800d176` starts both CRC bytes at`ff`, table-updates once per byte, returns low byte OR high byte shifted8, with no final XOR. Its literal tables are`0800dc30 ->080269b8` and`0800dc34 ->08026ab8`; the first table begins`00 c1 81 40 01 c0 80 41`, second`00 c0 c1 01 c3 03 02 c2`. This is reflected CRC16/Modbus polynomial`0xa001`, initial`0xffff`, xorout0. Native original-instruction execution on ASCII`123456789` returned`0x4b37`. The stored bytes are`37 4b` for that checksum. This is **not** the converter QD additive checksum.

For a complete offline record, require exactly256bytes, marker, matching CRC, and a plausible aligned address within`0x00040000..0x0068ff00`. Address/calendar plausibility are host classification checks, not all checks made by native uploader. Keep CRC-invalid/raw records separately. A matching marker alone is insufficient: startup scans markers without verifying CRC. Native uploader transmits only marker+CRC-valid records, **but advances past a CRC-invalid marked record**; it stops on the first missing marker or its requested-record bound. It does not stop merely because a CRC fails.

### Input mapping and what is absent

Writer calls`08021ce0(0,scratch,0)`, copies exactly`0xf2=242`bytes to offset8, and then overwrites offsets250..255 with address/CRC. Thus these are the native initialized **input0..120**, in serializer LE16 order, not121 arbitrary RAM words. The normal Modbus response swaps each word; the flash record does not.

The complete raw/current input map is in [register-map.json](../state-logging/firmware/register-map.json); context sources120..131 and500..599 are tabulated in [state-logging report §§3–4](../state-logging/firmware/REPORT.md). Important boundary: input120 is`u8[b7c0]` with high zero, but input121 (`b7b7`), aggregate faults122/123 (`b160`), aggregate warnings124/125 (`b15c`), selector126, generator frequency130, and **all500-block words including518/519 are absent**. No meaning of input120 substitutes for those missing diagnostic pairs. A256-byte record is a periodic telemetry sample, not a relay-decision transition record.

### Physical chronology and retention

- Flash area is`[0x00040000,0x00690000)`, length`0x650000`; pages are256bytes. There are25,856 physical positions,1,616 sectors of4KiB,16pages/sector.
- Writer stores its current page address in the record, programs the page, increments RAM write pointer by`0x100`, wraps after`0x68ffff` to`0x40000`, and erases the **next** sector whenever the next pointer is4KiB aligned (`0801dd40`). Consecutive addresses express physical write order modulo this wrap, not universal oldest-first ascending order.
- With next-write pointerP and uploader indexn starting1, uploader reads `P-256*n`, adding`0x650000` if it falls below`0x40000`. Upload is newest-first. It tests against`days*0x120` positions, a288positions/day convention; this is not a timestamp-based date query. A CRC-invalid marked position still consumes an index.
- After RAM-pointer loss, initialization scans upward from`0x40000` to the first missingA5A5 marker, wraps if the scan reaches the end, and erases an aligned discovered sector. This is not a timestamp/CRC sort and need not recover a precise pre-power-loss chronology.
- Erase-ahead leaves25,840..25,855 steady-state valid page positions [INFERENCE from physical geometry/write loop], approximately89.72..89.77 nominal288-sample days. It is not guaranteed retention. Torn writes, marker-only scanning, reset/reconstruction, clock corrections, gates, or explicit erase/upload behavior can reduce evidence.
- Actual configured interval is601 eligible callbacks ×25 task iterations ×20 SysTicks =300,500 SysTicks. At an assumed1ms tick this is300.5seconds [INFERENCE, not calibrated installed clock]. Logging is gated by enable/upload/pause fields; samples do not necessarily continue through all operating states. [State-logging report §6 cadence/gates](../state-logging/firmware/REPORT.md#exact-cadence-and-gates).

No ordinary03/04 historical index/date/flash-pointer reader was established. Holding111 **read** is an upload-days byte, not a record count. Holding111 **write** can erase or activate an asynchronous upload whose initialization can erase a sector; it remains excluded. No historical activation, mode change, arbitrary address probe, or arbitrary-RAM07 suggestion follows from this parser.

**FlexBOSS limit:** this audit proves its fixed07 receive lifetime and converter sample RAM distinction, but does not establish an equivalent FlexBOSS periodic flash-record writer/parser from the currently exported native functions. Its export contains the separate history-area erase routine`0805b5dc`, but the intervening write/upload code is not exported in this native function set. This is an explicit format/coverage limit, not evidence that FlexBOSS lacks history, and not permission to activate or assume the GridBOSS parser for it.

## 3. Concrete additional optional read profiles

Profiles below mean existing authenticated/local request framing and normal host validation; **local03** is the wrapped serializer route, not an invented normal-Modbus equivalent. They are candidate-source-backed, initialized-response profiles without acquisition/history/update activation or read-clear behavior. They are not permission for child/device access, and not installed-image proof. Prefer finite one-time raw baselines during the parent-owned pause; no new poller is proposed.

| Unit | Function and exact start/count | Native initialization / usefulness |
|---|---|---|
|FlexBOSS|04,120/40;160/40;200/40|Adds input120..239; full base serializer length480bytes. Useful raw role/status/received electrical context, detailed below. Both normal/local04 routes select the correct absolute offsets. Alternatively120/120 is below native127-word limit.|
|FlexBOSS|03,5000/18, normal **or local**|Exactly36initializedbytes: words5000..5015 pack bytes`e4e4..e503` in LE pairs;5016=`u16[ea0c]`;5017=`u16[ea0e]`. Preserve raw; no sensor identities proved. **Not5000/40.**|
|FlexBOSS|**local03**,500/112 and612/112|Separate224-byte raw configuration banks copied from`d688..d767` and`d788..d867`. Exact-start local copy uses scratch0 for either bank. Not promoted as physical sensor values. Normal03 at612 is not equivalent: its offset is224 beyond that invocation's224-byte output.|
|FlexBOSS|03,20/90 and235/102|Completes initialized base holding0..336 around existing0/20+110/125. Finite configuration baseline; avoids **exact-start224 and248** side-effect cases. Including their positions in another safe-start response is not the same as starting at them.|
|GridBOSS|04,40/40 and80/40|Adds missing initialized lower-bank context40..119 to existing0/40+120/12. Base input serializer initializes264bytes=0..131.|
|GridBOSS|**local03**,2000/108|216initializedbytes,2000..2107; configuration, masks, raw state bytes. Standard03 has no corresponding2000 copy branch.|
|GridBOSS|**local03**,4097/41|82initializedbytes,4097..4137; starts at`a548`, including voltage RMS gains4097/4098/4099 for grid/EPS/generator as established in the GridBOSS observability report. Standard03 lacks this copy branch.|
|GridBOSS|03,20/90;110/125;235/88|Completes initialized base holding0..322 around existing0/20. No exact-start trigger in this serializer. Useful settings/clock/mask baseline, not event history.|
|GridBOSS|**local03**,4500/10|Corrected GPIO/test-state bank, replacing candidate-invalid4516/10. See arithmetic proof below. Standard03's destination/framing mismatch makes that route unsuitable.|

### Bounds and side effects: why these, not merely serializer existence

**FlexBOSS03** normal`08042510`/local`08042c58`: count1..127. Both reject request ends851..5000; local additionally rejects end>5040. Explicit admitted branches cover start<500,500..723,5000..5039; high normal-only configuration banks exist separately. Thus5000/18 ends5018 and passes, while1100/17 remains rejected before serialization. Serializer`0805e73c`, branch`08060494..080604e6`, copies32source bytes and two u16s, only36bytes, regardless of its broader5000..5039 selector comparison. Its entry clears **four bytes only**, not an entire arbitrary requested response.5000/40 therefore has22uninitialized/stale words even though handler bounds pass. The5000 branch has no calls or source-state stores; only destination serialization. The common frame/parser housekeeping remains, so reads are not literally zero CPU/RAM work.

**FlexBOSS04** normal`08042e10`/local`08042f80`: count1..127; base ends<=500 are admitted, with correct `2*start` copy offset. Serializer`0805c61c` base branch initializes480bytes through`0805e28e`; extension requests end160/200/240 and stay within these bytes. It recomputes existing derived status bits in`e984` at`0805cff4..0805d046`, the **same base-serialization work already performed by0/40,40/40,80/40**. This is not read-clear, engineering initialization, or acquisition/history activation. The calls in this branch are memory fill and numeric-conversion helpers, not mode/flash routines. In contrast, its input5000 branch clears absent per-peer source records (`0805e324`) and advances paging cursor`ebac` (`0805e640/652`); **input5000 is not admitted as a side-effect-free extension**.

**GridBOSS04** normal`0800eb30`/local`0800ec80`: count1..150, end<=600; initialized base0..131 and500..599 only. Keep each response<=127words because byte-count is one byte. The04serializer writes destination and reads current sources; no history/read-clear path. **132..499 are undefined scratch, not an extra readable status bank.** No extra04bank beyond the two defined ranges is admitted.

**GridBOSS03** normal`0800e850`/local`0800e9b0`: count1..127 and end<=4999. Local explicitly routes start<500,2000..2499,4097..4199,4500..4999, but the serializer initializes only646/216/82/20bytes respectively. Recommended counts stop at actual initialized output, not broad dispatch bounds. Whole`0801ebd0` has only four-byte destination clear and GPIO-read helper calls, no flash/control/mode/read-clear calls or direct state mutations. `03holding5000/40` is **rejected** on GridBOSS; do not infer FlexBOSS reachability from this different dispatcher.

### Critical correction: GridBOSS GPIO bank is4500, not4516

Earlier [GridBOSS observability report §6](../decision-tree/gridboss-inputs/REPORT.md) says4516..4525 and claims offset`2*(start-4516)`. Native bytes contradict that arithmetic:

1. Local03 `0800e9b0` branch compares `start-0x1194<500`; **0x1194=4500**, not4516.
2. Its source pointer is `scratch + 2*start + literal[0800edb4]`. The mapped little-endian literal is signed **-9000**, hence exactly`scratch+2*(start-4500)`.
3. Serializer branch`08021bce..08021c82` emits only20bytes starting at scratch0. Executing original unmodified candidate bytes with selector4500 returns20bytes; the exact same branch selected by4516 does not reposition output to offset32.
4. Therefore local4500/10 copies the initialized20bytes. Local4516/10 copies scratch offsets32..51, **outside that output**. A plausible old4516 response is not source-backed GPIO evidence for this candidate. This does not establish installed-image identity or behavior; preserve live raw data and its uncertainty.

Correct local raw meanings:4500=`u8[b77a]` with high0;4501..4506=GPIO`40011800` ODR bits11,13,8,7,15,14 respectively;4507=literal0;4508=same ODR bit1;4509=GPIO`40011400` ODR bit6. Helper`0801ea2a` reads base+`0x0c` and returns a bit predicate; this is output-latch/command echo, **not contact-position feedback**. Standard03's4500 branch copies into the wrapper destination while its standard response CRC/count use a different payload offset; recommend local only.

### Selected newly available FlexBOSS inputs: raw meanings only

These indices were resolved by the original serializer's exact destination stores and source loads, not public register-name guesses. Integer division/truncation in the native code matters; values are not asserted to be watts/volts/relay positions without the existing producer proof.

| Input | Raw native value / source |
|---|---|
|120|`u16[eae6]`, load`0805d8a2`|
|121|`u16[eaec]`, load`0805da30`|
|122|`u16[eb00]`, load`0805da46`|
|123|Low16 of signed`i32[e898]/10`, load`0805da5c`; source power is used by rating guard, but no new physical scale claimed|
|124|`u16[d08a]`|
|125/126|Low/high16 of`u32[d134]`|
|127/128|`u16[eafa]` /`u16[eafc]`|
|129..132|Low16 of signed`i32[e884/e888/e890/e894]/10` respectively|
|133/134|`u16[cf8a]` /`u16[d030]`|
|135/136;137/138|Low/high16 of`u32[d032]`; low/high16 of`u32[d03a]`|
|139|Low16 of signed`i32[e87c]/10`|
|140/141|`u16[eaee]` /`u16[eaf0]`|
|142|`u8[ec78] | u8[ec79]<<8`|
|143|`u8[ec76] | u8[ec77]<<8`|
|144|`u8[ec7a] | u8[ec7b]<<8`|
|145|`u8[ec44] | u8[ec46]<<8`|
|146|`u16[eb34]`|
|147|Native combination involving`u16[e552]` and`u8[ec52]`; preserve raw pending producer interpretation|
|148|Native combination involving`u16[e554]` and`u8[ec73/ec74]`; preserve raw|
|149/150/151/152|`u16[e838/e83c/eb30/e562]` respectively|
|153|Low16 of signed`i32[e950]/10`|
|154..169|Contiguous u16 values`d232..d250`|
|170|Role/count-dependent derived current power value; source branch begins`0805dee0`, preserve raw|
|171|`u16[d140]`|
|172/173|Low/high16 of`u32[d13c]`|
|174..184|`u16[e564],u16[eb4a],u16[e568],u16[e56a],u16[e56c],u16[e56e],u16[e570],u16[e572],u16[e574],u16[e576],u16[e578]`|
|185|`u8[ec5e] | u8[ec5d]<<8`|
|186..239|Remaining initialized fixed base-serializer tail; contains explicit reserved/zero words and additional values, **not** an event/sample ring. Preserve raw unless independently mapped.|

The role/request limitation remains: this extension does not suddenly expose every upstream RAM flag. Existing [FlexBOSS decision proof §9](../decision-tree/flexboss/REPORT.md) explicitly finds no direct serializer exposure for`e834`, `ec6c`, `e969`, R/H,`ec03`, or`ec05`; lossy projections are not exact replacements.

GridBOSS local2000/108 begins2000=`u16[a6a0]`,2001=`u16[a6a2]`,2002=`u8[a59a]`,2003=`u8[a61e]`,2004=`u8[b7b4]`,2005=`u16[a622]`,2006=`u16[a624]`. Warning mask2102 is`u8[a6a6]` as independently mapped in the observability report. These are live masks/configuration/state, not retained history; no unlabeled raw word is promoted to a physical sensor.

## 4. Offline analysis evidence and residual limits

In addition to static source inspection, this slice used the **already installed** `/home/greg/.cache/eg4-rea/cortex-emulator/bin/python` and existing mapped payloads to execute original native serializer/CRC instructions with synthetic SRAM/MMIO. No instructions patched, no calls stubbed, no evidence reseeded or changed. In-memory snippets only; no new tooling/code files. Observed:

- Flex input serializer`0805c61c(0,dst,0)`: return480bytes; every byte240..479 written, final store479 at`0805e28e`. Source/store mappings above were observed at their native PCs and reconciled with static formulas.
- Flex holding`0805e73c`: starts0/500/612/5000 return674/224/224/36bytes respectively. Literal pointers`08060e78/7c` resolve`2000d688/2000d788`.
- Existing GridBOSS machine/serializer setup from [exercise_state_history.py](../state-logging/firmware/exercise_state_history.py) executed holding0/2000/4097/4500:646/216/82/20bytes; no non-destination SRAM writes below stack. Local offset literals`0800edb4=-9000`, `0800edbc=-8194` directly resolve4500 and4097 bank origins.
- Native GridBOSS CRC`0800d176` returned`4b37` on`123456789`; table pointers/contents above came from the mapped original bytes.

An attempted import of Capstone in the existing emulator environment failed (`ModuleNotFoundError`); nothing was installed, and no claim about the omitted FlexBOSS flash-write code was manufactured. No builds, repository tests, linters, formatters, live probes, or changes to existing tooling/evidence were performed.

Remaining limits: installed-image/variant identity; cross-field snapshot coherence/cache age; full physical labeling of newly captured raw fields; FlexBOSS omitted flash-writer format; actual dates/valid count retained in flash; native lack of a passive history selector and07generation nonce. None is solved by querying rejected1100, increasing a bank count into scratch, treating5000 input paging as passive, or writing history111. Parent owns any finite admitted live reads and their acquisition timing/restoration evidence.
