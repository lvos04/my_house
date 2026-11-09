package nl.huis.smarthome.work

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.content.edit
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.CancellationException
import nl.huis.smarthome.R
import nl.huis.smarthome.core.AlertNotifications
import nl.huis.smarthome.core.ConnectionStatus
import nl.huis.smarthome.core.Failures
import nl.huis.smarthome.huisApp
import java.util.concurrent.TimeUnit

/**
 * Every 15 minutes, also when the app is closed:
 * 1. refresh the overview (cache + home-screen widget), the stored addresses and, when due, the token;
 * 2. fetch open alerts, post a notification for new warnings/errors and remove the ones that were
 *    resolved or acknowledged. Alerts that arrive while notifications are not allowed are kept
 *    and shown once they are allowed.
 *
 * No Firebase/push service: the phone asks the server itself, through the same route helper as
 * the app (home address first, else the tunnel; a failed route is forgotten and picked again).
 */
class AlertWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val repo = ctx.huisApp.repository
        val start = repo.state.value
        if (start.pairing == null || start.unpaired != null) return Result.success()

        val refreshed = repo.refresh()
        val s = repo.state.value
        if (s.unpaired != null) return Result.success()
        if (!refreshed) return retryOrWait(s.connection)
        val homeId = s.pairing?.homeId ?: return Result.success()

        return try {
            val alerts = repo.routed { route -> repo.api.alerts(route, homeId) }
            val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val notified = prefs.getStringSet(KEY_NOTIFIED, emptySet()).orEmpty().toSet()
            val plan = AlertNotifications.plan(alerts, notified, AlertNotifier.canPost(ctx))
            val posted = plan.post.filter { AlertNotifier.post(ctx, it) }.map { it.id }.toSet()
            plan.cancel.forEach { AlertNotifier.cancel(ctx, it) }
            // Only what was really shown counts as notified; the rest waits for a next run.
            val waiting = plan.pending + (plan.post.map { it.id }.toSet() - posted)
            prefs.edit {
                putStringSet(KEY_NOTIFIED, plan.notified - (plan.post.map { it.id }.toSet() - posted))
                putInt(KEY_WAITING, waiting.size)
            }
            repo.setNotificationsWaiting(waiting.size)
            Result.success()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            repo.reportFailure(e, quiet = true)
            if (Failures.classify(e).offline) retryOrWait(repo.state.value.connection) else Result.success()
        }
    }

    /** Offline: retry a couple of times with WorkManager's backoff, then wait for the next period. */
    private fun retryOrWait(connection: ConnectionStatus): Result =
        if (connection != ConnectionStatus.WRONG_SERVER && runAttemptCount < 2) Result.retry() else Result.success()

    companion object {
        private const val WORK = "shc-alerts"
        private const val WORK_NOW = "shc-alerts-now"
        private const val PREFS = "shc_alerts"
        private const val KEY_NOTIFIED = "notified_ids"
        private const val KEY_WAITING = "waiting_count"
        const val CHANNEL = "alerts"

        fun createChannel(context: Context) {
            val ch = NotificationChannel(CHANNEL, context.getString(R.string.notification_channel), NotificationManager.IMPORTANCE_DEFAULT)
            ch.description = context.getString(R.string.notification_channel_desc)
            context.getSystemService(NotificationManager::class.java)?.createNotificationChannel(ch)
        }

        private fun constraints() = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

        fun schedule(context: Context) {
            val req = PeriodicWorkRequestBuilder<AlertWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, req)
        }

        /** Runs once now, e.g. right after notifications were allowed, to show what was waiting. */
        fun runNow(context: Context) {
            val req = OneTimeWorkRequestBuilder<AlertWorker>().setConstraints(constraints()).build()
            WorkManager.getInstance(context).enqueueUniqueWork(WORK_NOW, ExistingWorkPolicy.REPLACE, req)
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(WORK)
            WorkManager.getInstance(context).cancelUniqueWork(WORK_NOW)
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit { clear() }
        }

        /** Alerts waiting for notification permission, as counted by the last run. */
        fun waitingCount(context: Context): Int =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(KEY_WAITING, 0)
    }
}
