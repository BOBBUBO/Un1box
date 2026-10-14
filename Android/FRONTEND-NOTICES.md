# Selected frontend runtime notice assets

`scripts/package-frontend-notices.py` consumes the private schema-1 report from
`:app:recordApkRuntimeArtifacts`. Use the configuration and source/build properties
for the actual main APK. The verification metadata file, download cache and
androidTest/build-tool dependencies are not substitutes for that selected graph.

```sh
python3 Android/scripts/package-frontend-notices.py \
  /private/build/app/reports/debugRuntimeClasspath-artifacts.json \
  /private/build/generated/assets/licenses/frontend \
  --metadata-cache /private/gradle/caches/modules-2/files-2.1 \
  --source-reference https://publisher.example/corresponding-source \
  --source-revision FULL_40_HEX_SOURCE_REVISION \
  --supplements /private/checked-upstream-notice-supplements.json
```

The generator is offline. It verifies each selected original artifact SHA256,
reads outer archive notices and nested JAR notices (including classes.jar), and
copies their bytes unchanged. It retains full Maven coordinates, versions,
artifact filenames/hashes, original member paths and notice hashes. It records
original selected-version POM bytes and metadata provenance; Gradle module
name/hash mappings preserve canonical upstream URLs for platform artifact aliases
such as `ui-release.aar`. Kotlin multiplatform parent redirects remain explicit.
Duplicate identical selected rows retain their selection count without duplicating
the notice material. No local cache/artifact paths are copied into public assets.

The generated `manifest.json` has schema 1, `files[{path,sha256}]` and an HTTPS
`source_reference`, compatible with the existing offline LicenseDialog. All output
path components use its safe ASCII path alphabet. Original ZIP paths are metadata
only and are never extracted to the filesystem. The manifest additionally records
the selected configuration, portable graph hash, components, checked source
supplements and missing original notice material. Byte-identical reruns verify
the existing bundle; differing assets need a fresh generated-assets directory.

No license is assigned from a group-name prefix or guessed when an archive lacks
notices. Missing primary notice bytes fail before output by default. For an audit
only, `--allow-missing` emits the retained material and an explicitly INCOMPLETE
coverage field/list; this is not a finished release notice bundle. A POM declaration
is preserved as original publisher metadata, not silently substituted for a full
license/NOTICE or a grant for additional native/third-party material.

Checked supplemental original texts use this separate private schema:

```json
{
  "schema": 1,
  "components": [{
    "coordinate": "publisher.group:module:exact-version",
    "artifact_sha256": "FULL_ARTIFACT_SHA256",
    "notices": [{
      "name": "LICENSE.txt",
      "role": "license",
      "notice_path": "/private/checked-public-source/LICENSE.txt",
      "sha256": "FULL_ORIGINAL_TEXT_SHA256",
      "source_url": "https://upstream.example/exact-version/LICENSE.txt",
      "upstream_reference": "exact-version-or-source-commit"
    }]
  }]
}
```

Roles are `license`, `notice` or `attribution`. Supplements must match a selected
coordinate AND artifact hash, have safe names, bounded original bytes and matching
text hashes, and identify HTTPS primary provenance without embedded credentials.
Unknown/stale components, changed original bytes or ambiguous metadata fail.
The local paths in this input stay private; only checked texts and public provenance
are emitted. A license text must actually apply to that exact source/component;
the script does not authenticate or invent the publisher's grant.

The measured debug inventory at the first checkpoint has 58 selected rows and
57 unique artifacts. Five original artifacts embed LICENSE files; none of this
graph's nested classes.jar files contains additional notice entries. The nested
route is exercised by source tests rather than claimed from an absent real entry.
Most selected archives contain no notice entries, so their exact declared/source
license material needs supplemental retention. The graphics-path AAR ships a
native DSO; its Java POM alone is not a native-code attribution audit. Kotlin's
versioned license summary identifies additional stdlib source attributions, which
must be checked against the selected JVM runtime rather than copying all compiler,
test, JS or native licenses into the APK's runtime inventory.

Run source-only correctness checks without Gradle, a compiler or device:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s Android/scripts/tests -p test_frontend_notices.py -v
```

These assets retain checked notice bytes for selected inputs. They do not establish
DEX shrinking coverage, complete corresponding source/relink delivery, source URL
availability, combined-work classification, distribution clearance or any runtime
acceptance gate. Generated inputs, receipts and bundles remain outside public Git.
