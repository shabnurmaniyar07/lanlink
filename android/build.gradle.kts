// Versions are declared here and nowhere else. Every one of them is marked in
// android/README.md as needing to be confirmed by a real Gradle sync: this
// container cannot reach Google's Maven, so none of them has been resolved.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
}
