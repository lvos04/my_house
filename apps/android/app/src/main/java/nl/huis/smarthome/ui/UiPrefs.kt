package nl.huis.smarthome.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.net.Uri
import android.provider.Settings

/**
 * Small per-install UI memory (not secret, not part of the pairing): whether the notification
 * explanation was answered and whether the system was already asked for a permission. The
 * system itself does not tell "never asked" apart from "niet meer vragen".
 */
class UiPrefs(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var notificationsExplained: Boolean
        get() = prefs.getBoolean(K_NOTIF_EXPLAINED, false)
        set(v) = prefs.edit().putBoolean(K_NOTIF_EXPLAINED, v).apply()

    var notificationsAsked: Boolean
        get() = prefs.getBoolean(K_NOTIF_ASKED, false)
        set(v) = prefs.edit().putBoolean(K_NOTIF_ASKED, v).apply()

    var cameraAsked: Boolean
        get() = prefs.getBoolean(K_CAMERA_ASKED, false)
        set(v) = prefs.edit().putBoolean(K_CAMERA_ASKED, v).apply()

    private companion object {
        const val FILE = "shc_ui"
        const val K_NOTIF_EXPLAINED = "notifications_explained"
        const val K_NOTIF_ASKED = "notifications_asked"
        const val K_CAMERA_ASKED = "camera_asked"
    }
}

/** The Activity behind a Compose [Context] (needed for shouldShowRequestPermissionRationale). */
tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

/** Android's page for this app (permissions), used after "niet meer vragen". */
fun appSettingsIntent(context: Context): Intent =
    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

/** Starts [intent]; false when no app can handle it (some phones hide settings pages). */
fun Context.startSafely(intent: Intent): Boolean = try {
    startActivity(intent)
    true
} catch (e: android.content.ActivityNotFoundException) {
    false
} catch (e: SecurityException) {
    false
}
