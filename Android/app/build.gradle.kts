import java.security.MessageDigest

plugins { id("com.android.application"); id("org.jetbrains.kotlin.android"); id("org.jetbrains.kotlin.plugin.compose") }
android {
    namespace = "org.lighttouch.android"
    compileSdk = 35
    buildToolsVersion = "35.0.0"
    ndkVersion = "28.2.13676358"
    defaultConfig {
        applicationId = "org.lighttouch.android"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "0.1-port"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        ndk { abiFilters += providers.gradleProperty("targetAbis").orElse("arm64-v8a,x86_64").get().split(",") }
        externalNativeBuild { cmake { arguments += listOf("-DLT_NATIVE_JOBS=" + providers.gradleProperty("nativeJobs").orElse("2").get(), "-DANDROID_STL=c++_shared", "-DLT_FIRMWARE_DIR=" + providers.gradleProperty("firmwareDir").orElse(file("../../Portable/firmware").absolutePath).get(), "-DXPWN_SOURCE=" + providers.gradleProperty("xpwnSource").orElse("").get(), "-DLT_NATIVE_DEPS_ROOT=" + providers.gradleProperty("nativeDepsRoot").orElse("").get(), "-DLT_PORTABLE_DIR=" + providers.gradleProperty("portableDir").orElse(file("../../Portable").absolutePath).get(), "-DLT_ENGINE_ROOT=" + providers.gradleProperty("engineRoot").orElse("").get(), "-DLIGHTTOUCH_QEMU_SOURCE=" + providers.gradleProperty("qemuSource").orElse("").get()) } }
    }
    buildFeatures { compose = true; aidl = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    externalNativeBuild { cmake { path = file("src/main/cpp/CMakeLists.txt"); version = "3.22.1" } }
    packaging { jniLibs { useLegacyPackaging = false } }
}
dependencies {
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.ui:ui:1.7.8")
    implementation("androidx.compose.foundation:foundation:1.7.8")
    implementation("androidx.compose.material3:material3:1.3.2")
    implementation("androidx.documentfile:documentfile:1.0.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}

providers.gradleProperty("buildRoot").orNull?.let { output ->
    layout.buildDirectory.set(file("$output/app"))
    android.externalNativeBuild.cmake.buildStagingDirectory = file("$output/native")
}

val catalogAssets = layout.buildDirectory.dir("generated/catalogAssets")
val prepareCatalog = tasks.register<Copy>("prepareFirmwareCatalog") {
    from(file("../../LightTouchMac/Resources/firmware-catalog.json"))
    into(catalogAssets)
}
android.sourceSets["main"].assets.srcDir(catalogAssets)
tasks.named("preBuild").configure { dependsOn(prepareCatalog) }

// The publisher can override these for its complete corresponding-source bundle.
// The local checkout reference is development provenance, not distribution clearance.
val sourceRevision = providers.gradleProperty("sourceRevision").orElse(
    providers.exec { commandLine("git", "-C", rootProject.projectDir.parentFile.absolutePath, "rev-parse", "HEAD") }
        .standardOutput.asText.map { it.trim() }
)
val sourceReference = providers.gradleProperty("sourceReference").orElse(
    sourceRevision.map { "https://github.com/blake-lucas/LightTouch/tree/$it" }
)
val firmwareSource = providers.gradleProperty("firmwareDir").orElse(file("../../Portable/firmware").absolutePath)
val noticeAssets = layout.buildDirectory.dir("generated/noticeAssets")
val prepareNotices = tasks.register<Exec>("prepareFirmwareNotices") {
    inputs.file(firmwareSource.map { file("$it/tools/package-notices.py") })
    inputs.file(firmwareSource.map { file("$it/NOTICE.firmware.txt") })
    inputs.dir(firmwareSource.map { file("$it/vendor") })
    inputs.file(androidComponents.sdkComponents.ndkDirectory.map { it.file("NOTICE.toolchain") })
    inputs.property("sourceRevision", sourceRevision)
    inputs.property("sourceReference", sourceReference)
    outputs.dir(noticeAssets)
    doFirst {
        val destination = noticeAssets.get().dir("licenses/firmware").asFile
        delete(destination)
        commandLine("python3", file("${firmwareSource.get()}/tools/package-notices.py"), destination,
            "--ndk-notice", androidComponents.sdkComponents.ndkDirectory.get().file("NOTICE.toolchain").asFile,
            "--source-reference", sourceReference.get(), "--source-revision", sourceRevision.get())
    }
}
android.sourceSets["main"].assets.srcDir(noticeAssets)
tasks.named("preBuild").configure { dependsOn(prepareNotices) }

// Consume backend-owned, audited bundles; do not regenerate dependency notices here.
val engineNoticeInputs = mapOf(
    "arm64-v8a" to providers.gradleProperty("engineNoticeArm64"),
    "x86_64" to providers.gradleProperty("engineNoticeX86")
)
val engineNoticeAssets = layout.buildDirectory.dir("generated/engineNoticeAssets")
val prepareEngineNotices = tasks.register<Exec>("prepareEngineNotices") {
    val selectedAbis = providers.gradleProperty("targetAbis").orElse("arm64-v8a,x86_64").get().split(",")
    val supplied = selectedAbis.filter { engineNoticeInputs.getValue(it).isPresent }
    require(supplied.isEmpty() || supplied.size == selectedAbis.size) {
        "Provide an engine notice bundle for every packaged ABI"
    }
    val engineRoot = providers.gradleProperty("engineRoot").orElse("")
    inputs.file(rootProject.file("scripts/stage-engine-notices.py"))
    inputs.property("selectedAbis", selectedAbis)
    supplied.forEach { abi ->
        inputs.dir(engineNoticeInputs.getValue(abi).map { file(it) })
        inputs.file(engineRoot.map { file("$it/$abi/libqemu-arm.so") })
        inputs.file(engineRoot.map { file("$it/$abi/libc++_shared.so") })
    }
    outputs.dir(engineNoticeAssets)
    doFirst {
        val arguments = mutableListOf("python3", rootProject.file("scripts/stage-engine-notices.py").absolutePath,
            engineNoticeAssets.get().dir("licenses").asFile.absolutePath, "--engine-root", engineRoot.get())
        supplied.forEach { abi -> arguments += listOf("--bundle", abi, engineNoticeInputs.getValue(abi).get()) }
        commandLine(arguments)
    }
}
android.sourceSets["main"].assets.srcDir(engineNoticeAssets)
tasks.named("preBuild").configure { dependsOn(prepareEngineNotices) }

// Explicit audit only: resolve the selected runtime graph without compiling or
// confusing build-tool/androidTest dependencies with the conveyed main APK.
tasks.register("recordApkRuntimeArtifacts") {
    val configurationName = providers.gradleProperty("auditConfiguration").orElse("debugRuntimeClasspath")
    val report = layout.buildDirectory.file(configurationName.map { "reports/$it-artifacts.json" })
    outputs.file(report)
    outputs.upToDateWhen { false }
    doLast {
        require(configurationName.get() in listOf("debugRuntimeClasspath", "releaseRuntimeClasspath"))
        val artifacts = configurations.getByName(configurationName.get()).resolvedConfiguration.resolvedArtifacts
            .sortedBy { "${it.moduleVersion.id}:${it.name}:${it.classifier ?: ""}" }
            .map { artifact ->
                val digest = MessageDigest.getInstance("SHA-256")
                artifact.file.inputStream().use { input ->
                    val block = ByteArray(1024 * 1024)
                    while (true) { val size = input.read(block); if (size < 0) break; digest.update(block, 0, size) }
                }
                mapOf("coordinate" to artifact.moduleVersion.id.toString(), "file" to artifact.file.name,
                    "sha256" to digest.digest().joinToString("") { "%02x".format(it) },
                    "artifact_path" to artifact.file.absolutePath)
            }
        val document = mapOf("schema" to 1, "configuration" to configurationName.get(), "artifacts" to artifacts,
            "scope" to "selected main APK runtime inputs; private audit paths are not offline assets or source distribution clearance")
        val output = report.get().asFile
        output.parentFile.mkdirs()
        output.writeText(groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(document)) + "\n")
        println("Recorded ${artifacts.size} selected runtime artifacts: ${output.name}")
    }
}

// The optional provider is staged only in debug APKs, never linked directly or
// included in release. Its selection remains a closed worker-only debug enum.
val angleLibraries = providers.gradleProperty("angleLibraries")
val angleManifest = providers.gradleProperty("angleManifest")
val angleNotices = providers.gradleProperty("angleNotices")
val angleManifestSha = providers.gradleProperty("angleManifestSha256")
val angleNoticesSha = providers.gradleProperty("angleNoticesSha256")
val angleStage = layout.buildDirectory.dir("generated/angleRuntime")
val prepareAngleRuntime = tasks.register<Exec>("prepareAngleRuntime") {
    val selected = listOf(angleLibraries, angleManifest, angleNotices, angleManifestSha, angleNoticesSha)
    require(selected.all { it.isPresent } || selected.none { it.isPresent }) {
        "Supply angleLibraries, angleManifest, angleNotices and their two manifest SHA256 pins together"
    }
    inputs.file(rootProject.file("scripts/stage-angle-runtime.py"))
    inputs.property("angleEnabled", angleLibraries.isPresent)
    if (angleLibraries.isPresent) {
        require("x86_64" in providers.gradleProperty("targetAbis").orElse("arm64-v8a,x86_64").get().split(","))
        inputs.dir(angleLibraries.map { file(it) }); inputs.file(angleManifest.map { file(it) }); inputs.dir(angleNotices.map { file(it) })
        inputs.property("angleManifestSha", angleManifestSha); inputs.property("angleNoticesSha", angleNoticesSha)
    }
    outputs.dir(angleStage)
    doFirst {
        val arguments = mutableListOf("python3", rootProject.file("scripts/stage-angle-runtime.py").absolutePath, angleStage.get().asFile.absolutePath)
        if (angleLibraries.isPresent) {
            arguments += listOf("--libraries", angleLibraries.get(), "--manifest", angleManifest.get(), "--notices", angleNotices.get(),
                "--manifest-sha256", angleManifestSha.get(), "--notices-sha256", angleNoticesSha.get(),
                "--readelf", androidComponents.sdkComponents.ndkDirectory.get().file("toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf").asFile.absolutePath)
        }
        commandLine(arguments)
    }
}
android.sourceSets["debug"].jniLibs.srcDir(angleStage.map { it.dir("jniLibs") })
android.sourceSets["debug"].assets.srcDir(angleStage.map { it.dir("assets") })
android.packaging.jniLibs.keepDebugSymbols += setOf("**/libEGL_lighttouch_angle.so", "**/libGLESv1_CM_lighttouch_angle.so", "**/libGLESv2_lighttouch_angle.so")
tasks.named("preDebugBuild").configure { dependsOn(prepareAngleRuntime) }

// Firmware owns extraction; this consumer verifies the complete unchanged bundle
// against the actual main variant graph before copying public notice assets.
val frontendNotices = providers.gradleProperty("frontendNotices")
val frontendNoticesSha = providers.gradleProperty("frontendNoticesSha256")
val frontendNoticeAssets = layout.buildDirectory.dir("generated/frontendNoticeAssets")
val prepareFrontendNotices = tasks.register<Exec>("prepareFrontendNotices") {
    require(frontendNotices.isPresent == frontendNoticesSha.isPresent) {
        "Supply frontendNotices and its frontendNoticesSha256 pin together"
    }
    inputs.file(rootProject.file("scripts/stage-frontend-notices.py"))
    inputs.property("frontendEnabled", frontendNotices.isPresent)
    if (frontendNotices.isPresent) {
        dependsOn("recordApkRuntimeArtifacts")
        inputs.dir(frontendNotices.map { file(it) }); inputs.property("frontendManifestPin", frontendNoticesSha)
        inputs.file(layout.buildDirectory.file(providers.gradleProperty("auditConfiguration").orElse("debugRuntimeClasspath")
            .map { "reports/$it-artifacts.json" }))
    }
    outputs.dir(frontendNoticeAssets)
    doFirst {
        val arguments = mutableListOf("python3", rootProject.file("scripts/stage-frontend-notices.py").absolutePath,
            frontendNoticeAssets.get().dir("licenses/frontend").asFile.absolutePath)
        if (frontendNotices.isPresent) {
            arguments += listOf("--bundle", frontendNotices.get(), "--manifest-sha256", frontendNoticesSha.get(),
                "--inventory", layout.buildDirectory.file("reports/" + providers.gradleProperty("auditConfiguration").orElse("debugRuntimeClasspath").get() + "-artifacts.json").get().asFile.absolutePath)
        }
        commandLine(arguments)
    }
}
android.sourceSets["main"].assets.srcDir(frontendNoticeAssets)
tasks.named("preBuild").configure { dependsOn(prepareFrontendNotices) }
