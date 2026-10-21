import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// The version comes from the release tag: v1.2.3 gives versionName 1.2.3 (what Obtainium compares with the tag) and
// versionCode 1002003. CI passes the tag as VERSION_NAME; locally it's the latest v* tag, or 0.1.0 without one.
val appVersionName: String = (System.getenv("VERSION_NAME")
    ?: providers.exec {
        commandLine("git", "describe", "--tags", "--abbrev=0", "--match", "v*")
        isIgnoreExitValue = true
    }.standardOutput.asText.get().trim().ifEmpty { null }
    ?: "0.1.0").removePrefix("v")
val appVersionCode: Int = (appVersionName.substringBefore("-").split(".").map { it.toInt() } + listOf(0, 0))
    .let { (major, minor, patch) -> major * 1_000_000 + minor * 1_000 + patch }

// Release signing: env vars in CI, or keystore.properties next to this file (gitignored). The keystore itself lives
// outside the repo. Without either, local release builds fall back to the debug key; CI refuses to.
val keystoreProperties = Properties().apply {
    rootProject.file("keystore.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
fun signingValue(env: String, key: String): String? = System.getenv(env) ?: keystoreProperties.getProperty(key)
val releaseStoreFile = signingValue("ANDROID_KEYSTORE_FILE", "storeFile")
if (releaseStoreFile == null && System.getenv("CI") != null) error("No release keystore: set ANDROID_KEYSTORE_FILE")

android {
    namespace = "com.georgewaller.audiobrain"
    compileSdk { version = release(36) { minorApiLevel = 1 } }

    defaultConfig {
        applicationId = "com.georgewaller.audiobrain"
        minSdk = 29 // Android 10 up: the FiiO DAPs and older phones
        targetSdk = 36
        versionCode = appVersionCode
        versionName = appVersionName
    }

    signingConfigs {
        if (releaseStoreFile != null) create("release") {
            storeFile = rootProject.file(releaseStoreFile)
            storePassword = signingValue("ANDROID_KEYSTORE_PASSWORD", "storePassword")
            keyAlias = signingValue("ANDROID_KEY_ALIAS", "keyAlias") ?: "audiobrain"
            keyPassword = signingValue("ANDROID_KEY_PASSWORD", "keyPassword")
        }
    }

    buildTypes {
        debug {
            // Installs alongside the signed release, which can't replace a debug-signed app
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-dev"
            resValue("string", "app_name", "Audio Brain dev")
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
        resValues = true
    }
}

dependencies {
    implementation(project(":core:design"))
    implementation(project(":core:data"))
    implementation(project(":core:player"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.coil.compose)
    implementation(libs.androidx.glance.appwidget)
    debugImplementation(libs.androidx.compose.ui.tooling)
    testImplementation(libs.junit)
}
