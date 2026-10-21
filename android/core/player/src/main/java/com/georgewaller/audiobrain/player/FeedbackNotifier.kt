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
import androidx.media.app.NotificationCompat.MediaStyle
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

    private data class Shown(val track: NowPlaying, val within: Within?, val verdict: Verdict?) {
        val ref get() = within?.ref ?: track.ref
    }

    @OptIn(ExperimentalCoroutinesApi::class, FlowPreview::class)
    fun start(scope: CoroutineScope) {
        ensureChannel(context)
        val track = SessionTracker.nowPlaying
            .map { it?.takeIf { np -> np.hasTrack } }
            .distinctUntilChanged { a, b -> a?.ref?.key == b?.ref?.key && a?.playing == b?.playing }
        // within a set, the notification is about the set's current track
        val shown = combine(track, SessionTracker.within) { np, within -> np?.let { it to within } }.flatMapLatest { pair ->
            if (pair == null) flowOf(null)
            else { val (np, within) = pair; graph.feedback.latestFor(within?.ref ?: np.ref).map { Shown(np, within, Verdict.fromApi(it?.verdict)) } }
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
        val given = when (shown.verdict) {
            null -> null
            Verdict.Keep -> "Kept"
            Verdict.No -> "Marked no"
            Verdict.NotNow -> "Not now"
            Verdict.Never -> "Never again"
        }
        // the media template: the three verdicts as a row of icons, beside the artwork, even when collapsed. It has no
        // session of its own (the player's notification has playback), so it sits in the shade as an ordinary one.
        val builder = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_feedback)
            .setContentTitle(shown.within?.title ?: np.title)
            .setContentText(listOfNotNull(given, (shown.within?.artist ?: np.artist).takeIf { it.isNotBlank() }, shown.within?.detail).joinToString(" · "))
            .setSubText(np.appLabel)
            .setOngoing(true)  // stays while something plays; Android 14+ still lets you swipe it away until the next track
            .setLargeIcon(np.art)
            .setContentIntent(open)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setShowWhen(false)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setCategory(NotificationCompat.CATEGORY_TRANSPORT)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setStyle(MediaStyle().setShowActionsInCompactView(0, 1, 2))
        shown.verdict?.let { builder.setColor(COLOURS.getValue(it)) }
        listOf(
            Triple(Verdict.Keep, "Keep", R.drawable.ic_verdict_keep to R.drawable.ic_verdict_keep_on),
            Triple(Verdict.No, "No", R.drawable.ic_verdict_no to R.drawable.ic_verdict_no_on),
            Triple(Verdict.Never, "Never", R.drawable.ic_verdict_never to R.drawable.ic_verdict_never_on),
        ).forEachIndexed { i, (verdict, label, icons) ->
            val chosen = verdict == shown.verdict
            builder.addAction(if (chosen) icons.second else icons.first, if (chosen) "✓ $label" else label, FeedbackActionReceiver.intent(context, verdict, shown.ref, requestCode = i))
        }
        NotificationManagerCompat.from(context).notify(ID, builder.build())
    }

    companion object {
        // default importance, so it sits with the media and alerting notifications rather than down among the silent
        // ones; it never makes a sound (no sound on the channel, and setSilent). A new ID, as a channel's importance
        // can't be raised once created.
        const val CHANNEL = "now_playing"
        private const val OLD_CHANNEL = "now_playing_feedback"
        // the design system's verdict colours (core:design's AbColors), for the notification's accent once one is given
        private val COLOURS = mapOf(Verdict.Keep to 0xFF7BD99A.toInt(), Verdict.No to 0xFFFF7A6B.toInt(), Verdict.NotNow to 0xFFF2C35B.toInt(), Verdict.Never to 0xFFB79BFF.toInt())
        private const val ID = 1001

        fun ensureChannel(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.deleteNotificationChannel(OLD_CHANNEL)
            if (manager.getNotificationChannel(CHANNEL) == null) {
                manager.createNotificationChannel(NotificationChannel(CHANNEL, "Now playing", NotificationManager.IMPORTANCE_DEFAULT).apply {
                    description = "Keep, No and Never for what's playing in Tidal, UAPP or any player"
                    setSound(null, null)
                    enableVibration(false)
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
