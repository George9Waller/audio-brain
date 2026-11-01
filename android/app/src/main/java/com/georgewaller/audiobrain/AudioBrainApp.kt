package com.georgewaller.audiobrain

import android.app.Application
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.DataGraphOwner
import com.georgewaller.audiobrain.data.SyncScheduler
import com.georgewaller.audiobrain.player.FeedbackNotifier
import com.georgewaller.audiobrain.widget.NowWidgetUpdater
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class AudioBrainApp : Application(), DataGraphOwner {
    override val graph by lazy { DataGraph(this) }

    override fun onCreate() {
        super.onCreate()
        FeedbackNotifier.ensureChannel(this)
        ServerAlerts.ensureChannel(this)
        ServerAlerts.schedule(this)
        SyncScheduler.schedulePeriodic(this)
        SyncScheduler.requestNow(this) // anything left from last time
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
        NowSet.start(graph, scope)
        NowWidgetUpdater.start(this, scope)
    }
}
