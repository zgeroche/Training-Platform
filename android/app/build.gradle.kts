plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.parcelize) // required by the Samsung Health Data SDK
}

android {
    namespace = "com.trainingplatform.trainingsync"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.trainingplatform.trainingsync"
        minSdk = 29
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            optimization {
                enable = true
                packageScope = setOf("androidx.**", "kotlin.**", "kotlinx.**")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
    }
}

dependencies {
    // Samsung Health Data SDK (app/libs/*.aar, kept out of Git) and the Gson it needs.
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar"))))
    implementation(libs.gson)
    // The SDK's classes use kotlinx.parcelize.Parceler at runtime. With AGP 9's built-in Kotlin the
    // parcelize plugin doesn't add this library on its own, so the app crashed without it.
    implementation(libs.kotlin.parcelize.runtime)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}