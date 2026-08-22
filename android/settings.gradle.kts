// The Android application. The protocol core and the app logic are not modules
// of their own: they are plain-JVM Kotlin with no Android dependency, and
// app/build.gradle.kts adds their directories as source sets. That keeps one
// copy of the tested code rather than a copy the phone might drift from.

pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "LanLink"
include(":app")
