plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "link.lan.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "link.lan.android"
        // 26 (Oreo) is where the JDK 8 date/time and crypto behaviour the core
        // relies on is dependable, and covers everything still receiving
        // security updates. The test device is Android 14 (API 34).
        minSdk = 26
        targetSdk = 35
        versionCode = 3
        versionName = "0.2.0"
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            // Nothing is shipped from here yet; a release build would need
            // signing configuration and a proguard pass over the core.
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        // LanLinkApplication logs BuildConfig.VERSION_NAME at startup.
        buildConfig = true
    }

    // The tested Kotlin, compiled into the app rather than copied into it.
    //
    // android/core and android/logic import nothing but the Kotlin standard
    // library and the JDK — a test enforces that — so they compile for Android
    // unchanged. Pointing at the directories means the phone runs exactly what
    // the CI suite ran, and a change to either is picked up by both.
    sourceSets {
        getByName("main") {
            java.srcDirs("src/main/java", "../core/src", "../logic/src")
        }
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(libs.kotlinx.coroutines.android)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)

    // Keystore-backed storage for the paired-device file: it holds tokens.
    implementation(libs.androidx.security.crypto)

    // For the Storage Access Framework tree downloads are written into.
    implementation(libs.androidx.documentfile)

    // Scanning the PC's QR code. Brings its own capture Activity.
    implementation(libs.zxing.embedded)

    debugImplementation(libs.androidx.compose.ui.tooling)
}
