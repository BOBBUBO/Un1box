# Matched Linux QOM cast-check experiment

This is a source proposal, not an executable build allocation. Run the existing
clear-operation diagnostic first. Do not compile this candidate until its result
has been reviewed and a separate finite build allocation exists. Production
uses TCG; this option does not replace TCG or accelerate guest GPU commands.

The production Linux and Android configurations enable `CONFIG_QOM_CAST_DEBUG`.
Production disassembly confirms type-cache scanning in
`object_dynamic_cast_assert`, including ARM CPU/MMU helper callers. Existing
first/repeated Settings recordings each contain one action self sample there.
That is a modest optimization lead with insufficient evidence to predict a
percentage gain. Current upstream Mac builds also inherit the QEMU default;
there is no newly discovered upstream CPU engine behind this proposal.

## One configuration difference

Prepare one clean, committed QEMU source revision containing a narrowly scoped
builder selector. Start from the reviewed canonical portable source, retaining
the compiled-out diagnostic code, rather than the archived losing Dynarmic
worktree. Record the exact parent and final revision in the private build plan.
Both arms must use that **same final revision**, dependencies and tool hashes.

The proposed selector is `--experimental-disable-qom-cast-debug`. It does not
exist in the current portable builder; the command must not be used until its
implementation and focused guard tests have been reviewed. Its only configure
effect should be appending `--disable-qom-cast-debug`. Require Linux, an absent
fresh output, no `--source-update`, and no clear-counter selector. Leave the
default unchanged. Keep the selector and generated configure argv in the cache
recipe; never edit a stored recipe to admit a different configuration.

| Setting | Matched control | Candidate |
| --- | --- | --- |
| QOM cast checking | Default enabled | Explicitly disabled |
| Optimization, symbols, LTO | O2, enabled, off | Identical |
| Trace backend | log | log |
| Jump-cache entries / counter defines | 4096 / absent | Identical |
| TLS, MMU, invalidation, other assertions | Existing defaults | Identical |

Do not combine this with `trace=nop`, cache geometry, scheduling, LTO, alternate
CPU engines, or guest changes. Retain the checked development build: removing
these checks can hide an internal invalid type cast.

## Bounded build using existing tools

Create two separate private schema-1 plans for
`contrib/portable/run-candidate.py`, each with exactly one Linux target, jobs 1,
fresh build/notices/run outputs, identical input pins and configuration except
the selector. Use the existing outer resource operator, not direct unattended
Ninja. Configure through `build-engine.py --configure-only`; the driver then
runs `ninja -j1 -d keeprsp qemu-system-arm` and its existing `--shared-j1` recipe.
The builder's unqualified full-build route uses j2 and is outside this plan.

Per arm, retain the existing bounds: configure 180s, Ninja 900s, shared 180s,
each post-command 300s, outer 1500s, sampled aggregate owned-session RSS 786432 KiB,
at least 5 GiB free disk, and at most 2 GiB fresh allocated output. These are
conservative stop limits, not expected consumption. The RSS polling guard is
not a kernel memory limit and may count shared pages more than once. No local
compiler may overlap a guest or another heavy owner. Preserve each failure and
all owned-session retirement receipts; no silent retry or cache reuse.

The preceding actual fresh Linux diagnostic build provides a planning reference:

| Step | Actual elapsed | Sampled peak session RSS |
| --- | ---: | ---: |
| Configure | 19.443s | 230912KiB |
| Ninja j1 | 324.102s | 356560KiB |
| Shared library | 4.145s | 261624KiB |
| Configuration audit | 0.529s | 11520KiB |
| Selected-source audit | 24.260s | 104988KiB |
| Notices | 8.232s | 47948KiB |

Allow roughly 6–10 minutes per fresh arm, 12–20 minutes sequentially, subject to
the fixed stop limits. This is historical timing, not a promised QOM build time.
Do not duplicate the firmware base. Keep build outputs outside Git.

Retain configure argv, config-host headers, Meson values, compile commands,
selected-source inventory and object/disassembly hashes. Confirm the candidate
alone disables QOM cast validation; otherwise reject the pair. Audit the same
72 exports, dynamic dependencies, SONAME and 16 KiB ELF LOAD alignment. Generate
notices and manifests, hash/freeze both libraries read-only, and independently
verify their actual producer revisions. Building is not guest qualification.

## Later acceptance and stopping rule

Only a separately allocated comparison may run control/candidate/candidate/
control, serially, with identical small writable seed clones and the same
frontend, read-only base, package, renderer, fixed input and endpoint. Retain
Home129, Stop320, fallback360 and 35s shutdown grace; no clock extensions.
Inspect actual Home and complete Settings images, preserve action/source-frame
brackets and raw CPU ticks, and require clean lifecycle/retirement in every arm.
Existing broad profiles need not be rerun to polish historical metadata.

An improvement must exceed accounting and image-endpoint uncertainty in both
matched pairs without a material opposite regression. Overlapping intervals,
sparse ticks or missed scene coverage mean no demonstrated benefit; stop the
candidate instead of promoting it. Linux benefit would justify a separately
allocated ARM64/Android trial, not a faster-APK claim or broad compatibility pass.
