package com.georgewaller.audiobrain

import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.TrackDto
import com.georgewaller.audiobrain.data.TrackRef
import com.georgewaller.audiobrain.data.Tracklist
import com.georgewaller.audiobrain.player.SessionTracker
import com.georgewaller.audiobrain.player.Within
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.distinctUntilChangedBy
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * The DJ set being played, if the catalogue has its tracklist, and where in it playback is. Runs for the whole
 * process (started by [AudioBrainApp]), so the notification and widget follow the set with the app closed.
 */
@OptIn(ExperimentalCoroutinesApi::class, FlowPreview::class)
object NowSet {
    private val _track = MutableStateFlow<TrackDto?>(null)
    /** The catalogue's view of what's playing, for the widget's sigil. */
    val track: StateFlow<TrackDto?> = _track.asStateFlow()

    private val _tracklist = MutableStateFlow<Tracklist?>(null)
    val tracklist: StateFlow<Tracklist?> = _tracklist.asStateFlow()

    private var started = false
    lateinit var now: StateFlow<SetNow?>
        private set

    fun start(graph: DataGraph, scope: CoroutineScope) {
        if (started) return
        started = true
        now = combine(SessionTracker.nowPlaying, _tracklist) { np, tl -> np to tl }
            .flatMapLatest { (np, tl) ->
                if (np == null || tl == null) flowOf<SetNow?>(null)
                else flow<SetNow?> { while (true) { emit(SetNow.at(tl, np)); if (!np.playing) break; delay(5_000) } }
            }
            .distinctUntilChanged()
            .stateIn(scope, SharingStarted.Eagerly, null)
        scope.launch {
            SessionTracker.nowPlaying.distinctUntilChangedBy { it?.ref?.key }.debounce(400).collect { np ->
                _track.value = null
                val resolved = if (np != null && np.hasTrack) runCatching { graph.now.resolve(np.ref) }.getOrNull() else null
                _track.value = resolved?.track
                val setId = resolved?.setId
                if (setId != _tracklist.value?.set?.id) _tracklist.value = setId?.let { id -> runCatching { graph.api.tracklist(id) }.getOrNull() }
            }
        }
        scope.launch { now.collect { SessionTracker.within.value = it?.within() } }
    }

    /** The line playing, to show and rate in place of the set; none for an unidentified line ("ID – ID"). */
    private fun SetNow.within(): Within? {
        val l = line
        if (l.track == null && (l.title.isBlank() || l.title.equals("ID", true))) return null
        val ref = l.track?.ref ?: TrackRef(artist = l.artist, title = l.title)
        val title = l.track?.title ?: l.title
        val artist = l.track?.artist ?: l.artist
        return Within(title, artist, "${if (estimated) "about " else ""}${index + 1} of ${tracklist.lines.size} · ${shortName(tracklist.set.title)}", ref)
    }
}
