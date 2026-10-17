plugins {
    id("com.android.application")
}

android {
    namespace = "dev.amora.chibi"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.amora.chibi"
        minSdk = 26
        targetSdk = 36
        versionCode = 12
        versionName = "1.0.0-dev9-vocal-synth-dsp"
    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(files("libs/sherpa-onnx-1.13.8.aar"))
    testImplementation("junit:junit:4.13.2")
}
