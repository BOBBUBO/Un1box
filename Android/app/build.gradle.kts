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
