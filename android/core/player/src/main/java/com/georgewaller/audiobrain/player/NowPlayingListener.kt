package com.georgewaller.audiobrain.player

import android.content.ComponentName
import android.content.Context
import android.service.notification.NotificationListenerService
import androidx.core.app.NotificationManagerCompat
import com.georgewaller.audiobrain.data.dataGraph
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel

/**
 * Bound by Android once notification access is granted, and kept running by it, app open or not. It doesn't read
 * notifications: being a listener is what lets an app see other apps' media sessions. While bound it feeds
 * [SessionTracker] and keeps the feedback notification up to date.
 */
class NowPlayingListener : NotificationListenerService() {
    private var scope: CoroutineScope? = null

    override fun onListenerConnected() {
        SessionTracker.attach(this, ComponentName(this, NowPlayingListener::class.java))
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Main).also { FeedbackNotifier(this, dataGraph).start(it) }
    }

    override fun onListenerDisconnected() {
        scope?.cancel()
        scope = null
        FeedbackNotifier.cancel(this)
        SessionTracker.detach()
    }

    companion object {
        fun hasAccess(context: Context) = context.packageName in NotificationManagerCompat.getEnabledListenerPackages(context)

        /** Ask Android to rebind after access was granted (it otherwise waits until the next boot on some builds). */
        fun requestRebind(context: Context) {
            if (hasAccess(context)) requestRebind(ComponentName(context, NowPlayingListener::class.java))
        }
    }
}
