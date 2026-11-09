# R8 rules for the release build (minify + shrinkResources). Most libraries ship their own
# consumer rules inside their AAR/JAR; the rules below cover what this app does on top of that,
# plus known gaps. Keep them narrow: everything kept here is not shrunk.

# ---- kotlinx.serialization -------------------------------------------------------------------
# The app always passes serializers explicitly (X.serializer()), so R8 sees the references; these
# rules keep the generated serializers and Companion objects in case a serializer is looked up.
-keepattributes *Annotation*, InnerClasses, Signature, RuntimeVisibleAnnotations, AnnotationDefault
-dontnote kotlinx.serialization.**
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.json.** { kotlinx.serialization.KSerializer serializer(...); }
-keep,includedescriptorclasses class nl.huis.smarthome.**$$serializer { *; }
-keepclassmembers class nl.huis.smarthome.** { *** Companion; }
-keepclasseswithmembers class nl.huis.smarthome.** { kotlinx.serialization.KSerializer serializer(...); }
# Serializable classes: Companion field and serializer() (also for objects and enums).
-if @kotlinx.serialization.Serializable class **
-keepclassmembers class <1> {
    static <1>$Companion Companion;
}
-if @kotlinx.serialization.Serializable class ** {
    static **$* *;
}
-keepclassmembers class <2>$<3> {
    kotlinx.serialization.KSerializer serializer(...);
}
-if @kotlinx.serialization.Serializable class ** {
    public static ** INSTANCE;
}
-keepclassmembers class <1> {
    public static <1> INSTANCE;
    kotlinx.serialization.KSerializer serializer(...);
}

# ---- OkHttp / Okio ---------------------------------------------------------------------------
# OkHttp ships its own rules; these optional TLS providers are not on Android.
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# ---- WorkManager -----------------------------------------------------------------------------
# Workers are created by class name.
-keep class * extends androidx.work.ListenableWorker {
    public <init>(android.content.Context, androidx.work.WorkerParameters);
}

# ---- Glance (home-screen widget) -------------------------------------------------------------
# The receiver is in the manifest (kept by aapt). Glance stores its layout state with a shaded
# protobuf-lite that reads message fields by reflection.
-keepclassmembers class * extends androidx.glance.appwidget.protobuf.GeneratedMessageLite { <fields>; }

# ---- androidx.security:security-crypto (Tink) ------------------------------------------------
# Tink's keysets are protobuf-lite messages read by reflection; without this the encrypted store
# fails to open in a minified build ("Field keyData_ ... not found").
-keepclassmembers class * extends com.google.crypto.tink.shaded.protobuf.GeneratedMessageLite { <fields>; }
# Compile-only annotations and optional KMS/HTTP code referenced by Tink.
-dontwarn com.google.errorprone.annotations.**
-dontwarn javax.annotation.**
-dontwarn com.google.api.client.**
-dontwarn org.joda.time.**

# ---- ML Kit barcode scanning (QR pairing) ----------------------------------------------------
# ML Kit finds its components through registrar classes named in its manifest and creates them by
# reflection; the AAR's own rules cover this, the no-arg constructors are kept to be sure.
-keep class * implements com.google.firebase.components.ComponentRegistrar { <init>(); }
-keep class com.google.mlkit.**.*Registrar { <init>(); }
-dontwarn com.google.mlkit.**
