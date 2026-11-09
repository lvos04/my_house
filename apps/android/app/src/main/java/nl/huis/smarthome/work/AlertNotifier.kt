package nl.huis.smarthome.work

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import nl.huis.smarthome.MainActivity
import nl.huis.smarthome.R
import nl.huis.smarthome.core.Alert

/**
 * Phone notifications for alerts. Each alert has its own notification (tag = alert id), so a
 * resolved or acknowledged alert can be removed again without touching the others.
 */
object AlertNotifier {
    private const val NOTIFICATION_ID = 1

    /** Notifications may be shown: permission granted (Android 13+) and not switched off for the app. */
    fun canPost(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }
        return NotificationManagerCompat.from(context).areNotificationsEnabled()
    }

    fun post(context: Context, alert: Alert): Boolean {
        if (!canPost(context)) return false
        val open = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = NotificationCompat.Builder(context, AlertWorker.CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(alert.title)
            .setContentText(alert.detail)
            .setStyle(NotificationCompat.BigTextStyle().bigText(alert.detail ?: alert.title))
            .setPriority(if (alert.severity == "error") NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        return try {
            NotificationManagerCompat.from(context).notify(alert.id, NOTIFICATION_ID, n)
            true
        } catch (e: SecurityException) {
            false // permission withdrawn between the check and the call
        }
    }

    fun cancel(context: Context, alertId: String) {
        NotificationManagerCompat.from(context).cancel(alertId, NOTIFICATION_ID)
    }

    fun cancelAll(context: Context) {
        NotificationManagerCompat.from(context).cancelAll()
    }

    /** Opens the system settings page for this app's notifications. */
    fun settingsIntent(context: Context): Intent =
        Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, context.packageName)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
}
