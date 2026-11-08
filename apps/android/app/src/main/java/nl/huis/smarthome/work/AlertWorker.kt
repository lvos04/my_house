package nl.huis.smarthome.work

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import nl.huis.smarthome.MainActivity
import nl.huis.smarthome.R
import nl.huis.smarthome.core.AlertFilter
import nl.huis.smarthome.core.ApiException
import nl.huis.smarthome.huisApp
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Every 15 minutes: fetch open alerts and post a notification for new warnings/errors.
 * No Firebase/push service: the phone asks the server itself (LAN or tunnel).
 */
class AlertWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val repo = applicationContext.huisApp.repository
        val pairing = repo.state.value.pairing ?: return Result.success()
        val homeId = pairing.homeId ?: return Result.success()
        return try {
            repo.ensureRoute() ?: return Result.retry()
            val alerts = repo.api.alerts(homeId)
            val prefs = applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val seen = prefs.getStringSet(KEY_SEEN, emptySet()).orEmpty()
            val fresh = AlertFilter.toNotify(alerts, seen)
            fresh.forEach { notify(it.id, it.title, it.detail, it.severity) }
            // Remember only ids that are still open, so the set doesn't grow forever.
            val openIds = alerts.filterNot { it.acknowledged }.map { it.id }.toSet()
            prefs.edit { putStringSet(KEY_SEEN, (seen + fresh.map { it.id }).intersect(openIds)) }
            Result.success()
        } catch (e: ApiException) {
            if (e.isAuthError) Result.success() else Result.retry()
        } catch (e: IOException) {
            Result.retry()
        }
    }

    private fun notify(id: String, title: String, detail: String?, severity: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val intent = PendingIntent.getActivity(
            applicationContext,
            0,
            Intent(applicationContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(detail)
            .setPriority(if (severity == "error") NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(intent)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(applicationContext).notify(id.hashCode(), n)
    }

    companion object {
        private const val WORK = "shc-alerts"
        private const val PREFS = "shc_alerts"
        private const val KEY_SEEN = "notified_ids"
        const val CHANNEL = "alerts"

        fun createChannel(context: Context) {
            val ch = NotificationChannel(CHANNEL, context.getString(R.string.notification_channel), NotificationManager.IMPORTANCE_DEFAULT)
            ch.description = context.getString(R.string.notification_channel_desc)
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
        }

        fun schedule(context: Context) {
            val req = PeriodicWorkRequestBuilder<AlertWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(WORK)
        }
    }
}
