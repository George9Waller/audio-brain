package com.georgewaller.audiobrain

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
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.DataGraphOwner
import java.util.concurrent.TimeUnit

/**
 * Asks the server for its warnings once an hour (models out of date, a scheduled job that failed) and posts a
 * notification when there's one it hasn't notified before; tapping it opens Jobs. The server has no way to push, so
 * this is the nearest thing. Away from the tailnet it just tries again next hour.
 */
class ServerAlertWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val graph = (applicationContext as DataGraphOwner).graph
        val settings = graph.settings.current()
        if (!settings.configured || !settings.serverAlerts) return Result.success()
        val warnings = try {
            graph.jobs.warnings()
        } catch (e: ApiException) {
            return Result.success()
        }
        val keys = warnings.map { it.key }.toSet()
        // keep only what still stands, so a warning that clears and comes back notifies again
        val fresh = keys - graph.settings.notifiedWarnings()
        graph.settings.setNotifiedWarnings(keys)
        if (warnings.isEmpty()) ServerAlerts.cancel(applicationContext)
        else if (fresh.isNotEmpty()) ServerAlerts.post(applicationContext, warnings.map { it.text })
        return Result.success()
    }
}

object ServerAlerts {
    private const val CHANNEL = "server_health"
    private const val ID = 1002

    fun schedule(context: Context) {
        val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
        val request = PeriodicWorkRequestBuilder<ServerAlertWorker>(1, TimeUnit.HOURS).setConstraints(online).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("server-alerts", ExistingPeriodicWorkPolicy.KEEP, request)
    }

    fun ensureChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "Server health", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "When the server's nightly jobs fail or its models are out of date"
            })
        }
    }

    fun post(context: Context, warnings: List<String>) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val open = Intent(context, MainActivity::class.java)
            .putExtra(MainActivity.EXTRA_JOBS, true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val style = NotificationCompat.InboxStyle()
        warnings.forEach { style.addLine(it) }
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_server)
            .setContentTitle(if (warnings.size == 1) "The server needs a look" else "The server needs a look: ${warnings.size} things")
            .setContentText(warnings.first())
            .setStyle(style)
            .setContentIntent(PendingIntent.getActivity(context, ID, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .build()
        NotificationManagerCompat.from(context).notify(ID, notification)
    }

    fun cancel(context: Context) = NotificationManagerCompat.from(context).cancel(ID)
}
