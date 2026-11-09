import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// ---------------------------------------------------------------------------------------------
// Version from the repository's VERSION file (x.y.z, y and z below 100), shared with the server
// images: versionCode = major*10000 + minor*100 + patch, so every release installs over the last.
// ---------------------------------------------------------------------------------------------
val versionFile = rootProject.layout.projectDirectory.file("../../VERSION")
val appVersionName: String = providers.fileContents(versionFile).asText.orNull?.trim()?.ifEmpty { null } ?: "0.0.0-dev"
val appVersionCode: Int = run {
    val m = Regex("""^(\d+)\.(\d{1,2})\.(\d{1,2})(?:[-+].*)?$""").matchEntire(appVersionName)
        ?: throw GradleException("VERSION '$appVersionName' is geen geldige versie (verwacht x.y.z, y en z onder 100).")
    val (major, minor, patch) = m.destructured
    (major.toInt() * 10000 + minor.toInt() * 100 + patch.toInt()).coerceAtLeast(1)
}

// ---------------------------------------------------------------------------------------------
// Release signing (contract C10): only from environment variables, never from the repository.
// Without them `assembleRelease` gives app-release-unsigned.apk (release.yml signs that itself);
// debug builds always use the normal debug key and stay installable.
// ---------------------------------------------------------------------------------------------
val signingStoreFile: String? = providers.environmentVariable("SHC_SIGNING_STORE_FILE").orNull?.takeIf { it.isNotBlank() }
val signingStorePassword: String? = providers.environmentVariable("SHC_SIGNING_STORE_PASSWORD").orNull
val signingKeyAlias: String? = providers.environmentVariable("SHC_SIGNING_KEY_ALIAS").orNull
val signingKeyPassword: String? = providers.environmentVariable("SHC_SIGNING_KEY_PASSWORD").orNull
val hasReleaseKey = signingStoreFile != null && file(signingStoreFile).isFile &&
    !signingStorePassword.isNullOrEmpty() && !signingKeyAlias.isNullOrEmpty() && !signingKeyPassword.isNullOrEmpty()

android {
    namespace = "nl.huis.smarthome"
    compileSdk = 35

    defaultConfig {
        applicationId = "nl.huis.smarthome"
        minSdk = 26
        targetSdk = 35
        versionCode = appVersionCode
        versionName = appVersionName
    }

    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = file(signingStoreFile!!)
                storePassword = signingStorePassword
                keyAlias = signingKeyAlias
                keyPassword = signingKeyPassword
                // keytool makes PKCS12 by default, also with a .jks name (docs/installatie-truenas.md).
                storeType = providers.environmentVariable("SHC_SIGNING_STORE_TYPE").orNull ?: "pkcs12"
                enableV1Signing = true
                enableV2Signing = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = if (hasReleaseKey) signingConfigs.getByName("release") else null
        }
        debug {
            // Debug builds keep the standard debug key so they can always be installed for testing.
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    implementation(project(":core"))

    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    debugImplementation(libs.androidx.compose.ui.tooling)

    implementation(libs.androidx.camera.camera2)
    implementation(libs.androidx.camera.lifecycle)
    implementation(libs.androidx.camera.view)
    implementation(libs.mlkit.barcode.scanning)

    implementation(libs.androidx.security.crypto)
    implementation(libs.androidx.glance.appwidget)
    implementation(libs.androidx.glance.material3)
    implementation(libs.androidx.work.runtime.ktx)

    testImplementation(libs.junit)
}
