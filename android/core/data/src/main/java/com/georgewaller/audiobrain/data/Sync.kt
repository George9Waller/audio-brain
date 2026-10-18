package com.georgewaller.audiobrain.data

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.time.Instant
import java.util.concurrent.TimeUnit

/**
 * Sends the outbox to the server in batches until it's empty. Runs when there's a network; if the server can't be
 * reached (away from the tailnet, server down) it backs off and tries again. Items are stored once by client ID, so
 * a batch cut off halfway is simply sent again.
 */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val graph = (applicationContext as DataGraphOwner).graph
        if (!graph.settings.current().configured) return Result.success()
        val dao = graph.db.feedback()
        val queue = graph.db.queue()
        repeat(MAX_BATCHES) {
            val batch = dao.pending(BATCH)
            val decisions = queue.pending(BATCH)
            if (batch.isEmpty() && decisions.isEmpty()) return Result.success()
            val request = SyncRequest(
                feedback = batch.map {
                    FeedbackItem(it.clientId, it.verdict, it.ref, where = it.where, contextId = it.contextId, givenAt = Instant.ofEpochMilli(it.givenAt).toString())
                },
                queue = decisions.map { QueueItem(it.clientId, it.releaseId, it.state, Instant.ofEpochMilli(it.givenAt).toString()) },
            )
            val response = try {
                graph.api.sync(request)
            } catch (e: ApiException) {
                if (batch.isNotEmpty()) dao.failedAttempt(batch.map { it.clientId }, e.message ?: "failed")
                return when (e) {
                    is ApiException.Offline -> Result.retry()
                    is ApiException.Http -> if (e.code >= 500) Result.retry() else Result.failure()
                    else -> Result.failure() // not set up or a bad token: waits for the next change or enqueue
                }
            }
            val queued = decisions.map { it.clientId }.toSet()
            response.accepted.forEach { if (it.clientId in queued) queue.markSent(it.clientId) else dao.markSent(it.clientId, it.trackId) }
            response.rejected.forEach { if (it.clientId in queued) queue.delete(it.clientId) else dao.markRejected(it.clientId, it.reason) }
        }
        return Result.success()
    }

    companion object {
        private const val BATCH = 50
        private const val MAX_BATCHES = 20
    }
}

object SyncScheduler {
    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /** Send what's pending as soon as there's a network. A run already going is followed by another, so nothing waits. */
    fun requestNow(context: Context) {
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(online)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork("sync-now", ExistingWorkPolicy.APPEND_OR_REPLACE, request)
    }

    /** A sweep every 15 minutes, for anything left after the server was unreachable for a long time. */
    fun schedulePeriodic(context: Context) {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES).setConstraints(online).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("sync-periodic", ExistingPeriodicWorkPolicy.KEEP, request)
    }
}
