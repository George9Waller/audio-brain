package com.georgewaller.audiobrain.player

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.georgewaller.audiobrain.data.ApiJson
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.TrackRef
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.data.dataGraph
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

/**
 * A quiet notification while something plays, with Keep, No and Never on it: feedback from the lock screen or the
 * shade, offline included, without opening the app. Follows the track; shows the verdict once given.
 */
class FeedbackNotifier(private val context: Context, private val graph: DataGraph) {

    private data class Shown(val track: NowPlaying, val verdict: Verdict?)

    @OptIn(ExperimentalCoroutinesApi::class, FlowPreview::class)
    fun start(scope: CoroutineScope) {
        ensureChannel(context)
        val track = SessionTracker.nowPlaying
            .map { it?.takeIf { np -> np.hasTrack } }
            .distinctUntilChanged { a, b -> a?.ref?.key == b?.ref?.key && a?.playing == b?.playing }
        val shown = track.flatMapLatest { np ->
            if (np == null) flowOf(null)
            else graph.feedback.latestFor(np.ref).map { Shown(np, Verdict.fromApi(it?.verdict)) }
        }
        scope.launch {
            combine(shown, graph.settings.settings.map { it.feedbackNotification }.distinctUntilChanged()) { s, on -> s.takeIf { on } }
                .debounce(250)
                .collect { if (it == null) cancel(context) else post(it) }
        }
    }

    private fun post(shown: Shown) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val np = shown.track
        val open = context.packageManager.getLaunchIntentForPackage(context.packageName)?.let {
            PendingIntent.getActivity(context, 0, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        }
        val text = when (shown.verdict) {
            null -> "${np.artist} · ${np.appLabel}"
            Verdict.Keep -> "Kept · ${np.artist}"
            Verdict.No -> "Marked no · ${np.artist}"
            Verdict.NotNow -> "Not now · ${np.artist}"
            Verdict.Never -> "Never again · ${np.artist}"
        }
        val builder = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_feedback)
            .setContentTitle(np.title)
            .setContentText(text)
            .setLargeIcon(np.art)
            .setContentIntent(open)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setShowWhen(false)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setPriority(NotificationCompat.PRIORITY_LOW)
        listOf(Verdict.Keep to "Keep", Verdict.No to "No", Verdict.Never to "Never").forEachIndexed { i, (verdict, label) ->
            val chosen = verdict == shown.verdict
            builder.addAction(0, if (chosen) "✓ $label" else label, FeedbackActionReceiver.intent(context, verdict, np.ref, requestCode = i))
        }
        NotificationManagerCompat.from(context).notify(ID, builder.build())
    }

    companion object {
        const val CHANNEL = "now_playing_feedback"
        private const val ID = 1001

        fun ensureChannel(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java)
            if (manager.getNotificationChannel(CHANNEL) == null) {
                manager.createNotificationChannel(NotificationChannel(CHANNEL, "Feedback while listening", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Keep, No and Never for what's playing in Tidal, UAPP or any player"
                    setShowBadge(false)
                })
            }
        }

        fun cancel(context: Context) = NotificationManagerCompat.from(context).cancel(ID)
    }
}

/** Handles the notification's buttons: records the verdict in the outbox (offline is fine) and lets sync send it. */
class FeedbackActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val verdict = Verdict.fromApi(intent.getStringExtra(EXTRA_VERDICT)) ?: return
        val ref = intent.getStringExtra(EXTRA_TRACK)?.let { runCatching { ApiJson.decodeFromString<TrackRef>(it) }.getOrNull() } ?: return
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                context.dataGraph.feedback.give(verdict, ref, where = "notification")
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        private const val EXTRA_VERDICT = "verdict"
        private const val EXTRA_TRACK = "track"

        fun intent(context: Context, verdict: Verdict, ref: TrackRef, requestCode: Int): PendingIntent {
            val intent = Intent(context, FeedbackActionReceiver::class.java)
                .putExtra(EXTRA_VERDICT, verdict.api)
                .putExtra(EXTRA_TRACK, ApiJson.encodeToString(ref))
            return PendingIntent.getBroadcast(context, requestCode, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        }
    }
}
