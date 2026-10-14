# Build review for the lazy frame cache

Use the integrated Android/Portable source freeze supplied by Root; a product
worktree containing only the JNI change lacks Runtime's metadata implementation.
Export the full Android and Portable trees, CActivation source/include, tracked
firmware catalog and source grant README/LICENSE. Record the archive and every
file hash. A reviewed instrumentation overlay has its own source revision,
original path, destination path and hash; it is not a change to production main.

The candidate24 preparation compares the actual native source scope to23:
75 files,67 unchanged,5 changed,3 added,0 removed. This includes docs/tools/tests;
actual compile sources and dependency headers must be collected independently.
Changed JNI, Runtime, public header and CMake require fresh native compilation.
Do not reuse candidate23's native producer or assert old72 source equality.

The sealed259e engine, NDK runtime, bridge files and matching notices can be
reused after hash verification. Use fresh isolated Gradle/CMake outputs and
maxworkers1/nativeJobs1. Build main, instrumentation and all26 JVM cases, retaining
their actual XML counts. Host cache sanitizer tests do not substitute for this
two-ABI JNI/runtime build or Android/guest execution.

Inspect effective JNI/Runtime/Storage/GuestPackage -O2, -g and assertions, JNI
frame_cache.h and Runtime FrameStorage.hpp dependency hashes, and the linked
lt_runtime_frame_metadata symbol in both native outputs. Existing Native method
count33, status15 and observations65 must remain coherent with the matching
Kotlin/AIDL sources. Retain actual CMake origin and generated worker flags.

Verify main/test signatures, stored16KiB native ZIP and ELF LOAD alignment,
SONAME/DT_NEEDED closure, exact producer-input or pinned NDK-strip mapping for
each DSO, catalog and every notice payload. Seal only actual terminal results;
expected17 DSOs/247 notices are not evidence until checked. Preserve previous
APKs, source archives and failure receipts. Runtime/visible guest/performance and
complete publisher licensing eligibility remain separate.

GuestSessionSetupTest accepts the actual newly sealed main APK checksum and an
identified unused worker PID. Never pass an earlier APK's hash to a later build.
The Pixel diagnostic overlay must similarly accept a validated expected main
checksum instead of a fixed APK22 identity before it can qualify a later build;
overlay compilation alone is not a virtual or physical Pixel pass.
