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
        ndk { abiFilters += providers.gradleProperty("targetAbis").orElse("arm64-v8a,x86_64").get().split(",") }
        externalNativeBuild { cmake { arguments += listOf("-DANDROID_STL=c++_shared", "-DLT_PORTABLE_DIR=" + providers.gradleProperty("portableDir").orElse(file("../../Portable").absolutePath).get(), "-DLT_ENGINE_ROOT=" + providers.gradleProperty("engineRoot").orElse("").get(), "-DLIGHTTOUCH_QEMU_SOURCE=" + providers.gradleProperty("qemuSource").orElse("").get()) } }
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
}

providers.gradleProperty("buildRoot").orNull?.let { output ->
    layout.buildDirectory.set(file("$output/app"))
    android.externalNativeBuild.cmake.buildStagingDirectory = file("$output/native")
}
