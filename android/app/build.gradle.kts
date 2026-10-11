plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.hurmat.tasaaweer"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.hurmat.tasaaweer"
        // takeScreenshotOfWindow and attachAccessibilityOverlayToWindow are API 34.
        minSdk = 34
        targetSdk = 36
        versionCode = 1
        versionName = "0.1-probe"
    }

    ndkVersion = "28.2.13676358"
    defaultConfig.ndk.abiFilters += "arm64-v8a"
    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
        }
    }
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/models"))

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

// The model files come from the desktop project's models/ directory
// (tools/export_models.sh), renamed for the assets folder.
val copyModels by tasks.registering(Copy::class) {
    for (name in listOf("yolo11s-seg", "yolo11n-seg", "yolo11n-pose", "yolo11s-pose")) {
        from("../../models/${name}_ncnn_model") {
            include("model.ncnn.param", "model.ncnn.bin")
            rename { it.replace("model.ncnn", name) }
        }
    }
    into(layout.buildDirectory.dir("generated/models"))
}
tasks.named("preBuild") { dependsOn(copyModels) }
