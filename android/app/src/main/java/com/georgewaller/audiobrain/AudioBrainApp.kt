package com.georgewaller.audiobrain

import android.app.Application
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.DataGraphOwner
import com.georgewaller.audiobrain.data.SyncScheduler
import com.georgewaller.audiobrain.player.FeedbackNotifier

class AudioBrainApp : Application(), DataGraphOwner {
    override val graph by lazy { DataGraph(this) }

    override fun onCreate() {
        super.onCreate()
        FeedbackNotifier.ensureChannel(this)
        SyncScheduler.schedulePeriodic(this)
        SyncScheduler.requestNow(this) // anything left from last time
    }
}
