package com.georgewaller.audiobrain.widget

import android.content.Context
import androidx.glance.appwidget.updateAll
import com.georgewaller.audiobrain.NowSet
import com.georgewaller.audiobrain.data.dataGraph
import com.georgewaller.audiobrain.player.SessionTracker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

/**
 * Redraws the widget when the track, play state or lists change. The widget reads them live while its session
 * runs; this restarts it after Android has let the session lapse.
 */
object NowWidgetUpdater {
    @OptIn(FlowPreview::class)
    fun start(context: Context, scope: CoroutineScope) {
        val app = context.applicationContext
        scope.launch {
            combine(
                SessionTracker.nowPlaying.map { np -> np?.takeIf { it.hasTrack }?.let { it.ref.key to it.playing } },
                SessionTracker.within.map { it?.ref?.key },
                NowSet.track.map { it?.id },  // the sigil is drawn from the matched track's sound
                app.dataGraph.lists.state.map { s -> s.lists.filter { it.kind == "mix" }.take(3).map { it.key to it.title } },
            ) { a, b, c, d -> listOf(a, b, c, d) }
                .distinctUntilChanged()
                .debounce(300)
                .collect { runCatching { NowWidget().updateAll(app) } }
        }
    }
}
