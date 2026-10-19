package com.georgewaller.audiobrain

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.ContextDto
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.data.QuickAction
import com.georgewaller.audiobrain.data.TrackDto
import com.georgewaller.audiobrain.data.TrackRef
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.player.NowPlaying
import com.georgewaller.audiobrain.player.SessionTracker
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.distinctUntilChangedBy
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * The app's shared state: what's playing, every list, the verdicts given here, quick actions, and making mixes
 * (drafts, radio). One instance for the activity; pages read from it and call into it.
 */
@OptIn(ExperimentalCoroutinesApi::class, FlowPreview::class)
class AppViewModel(private val graph: DataGraph, private val app: Context) : ViewModel() {

    /** Where making a mix has got to. Drives the Building and Draft screens. */
    sealed interface Making {
        data object Idle : Making
        data class Building(val title: String, val stages: List<Pair<String, String>>) : Making
        data class Draft(val mix: MixList, val understood: String, val prompt: String, val removed: Set<Long> = emptySet()) : Making
    }

    sealed interface Event {
        data class Message(val text: String) : Event
        data class OpenList(val key: String) : Event
    }

    val settings = graph.settings.settings.stateIn(viewModelScope, SharingStarted.Eagerly, null)
    val nowPlaying: StateFlow<NowPlaying?> = SessionTracker.nowPlaying
    val listening: StateFlow<Boolean> = SessionTracker.connected
    val pending: StateFlow<Int> = graph.feedback.pendingCount().stateIn(viewModelScope, SharingStarted.Eagerly, 0)
    val lists = graph.lists.state
    val verdicts: StateFlow<Map<String, Verdict>> = graph.feedback.verdicts.stateIn(viewModelScope, SharingStarted.Eagerly, emptyMap())
    val queueStates: StateFlow<Map<Long, String>> = graph.feedback.queueStates.stateIn(viewModelScope, SharingStarted.Eagerly, emptyMap())

    private val _track = MutableStateFlow<TrackDto?>(null)
    /** The catalogue's view of the playing track, once the server has matched it. */
    val track: StateFlow<TrackDto?> = _track.asStateFlow()

    val nowVerdict: StateFlow<Verdict?> = SessionTracker.nowPlaying
        .distinctUntilChangedBy { it?.ref?.key }
        .flatMapLatest { np -> if (np == null) flowOf(null) else graph.feedback.latestFor(np.ref).map { Verdict.fromApi(it?.verdict) } }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private val _actions = MutableStateFlow<List<QuickAction>>(emptyList())
    val actions: StateFlow<List<QuickAction>> = _actions.asStateFlow()

    private val _contexts = MutableStateFlow<List<ContextDto>>(emptyList())
    val contexts: StateFlow<List<ContextDto>> = _contexts.asStateFlow()

    private val _making = MutableStateFlow<Making>(Making.Idle)
    val making: StateFlow<Making> = _making.asStateFlow()
    private var makingJob: Job? = null

    private val _events = MutableSharedFlow<Event>(extraBufferCapacity = 8)
    val events: SharedFlow<Event> = _events

    init {
        viewModelScope.launch { graph.lists.load() }
        // fetch again once connected, or when the server or token changes
        viewModelScope.launch {
            graph.settings.settings.map { it.serverUrl to it.token }.distinctUntilChanged().drop(1).collect { if (it.first.isNotBlank()) refresh() }
        }
        viewModelScope.launch {
            SessionTracker.nowPlaying.distinctUntilChangedBy { it?.ref?.key }.debounce(400).collect { np ->
                _track.value = null
                if (np != null && np.hasTrack) _track.value = runCatching { graph.now.resolve(np.ref) }.getOrNull()
                refreshActions()
                np?.let { extendRadioIfNearEnd(it) }
            }
        }
    }

    fun refresh() {
        viewModelScope.launch { graph.lists.refresh() }
        refreshActions()
    }

    fun refreshActions() {
        viewModelScope.launch { runCatching { graph.make.actions(track.value?.id) }.onSuccess { _actions.value = it } }
    }

    fun loadContexts() {
        if (_contexts.value.isNotEmpty()) return
        viewModelScope.launch { runCatching { graph.api.contexts() }.onSuccess { _contexts.value = it } }
    }

    fun message(text: String) { _events.tryEmit(Event.Message(text)) }

    // Feedback

    fun giveNow(verdict: Verdict) {
        val np = nowPlaying.value ?: return
        val known = track.value
        viewModelScope.launch { graph.feedback.give(verdict, np.ref.copy(id = known?.id), "now playing", known?.context?.id) }
    }

    fun give(verdict: Verdict, item: ListItem, where: String) {
        viewModelScope.launch { graph.feedback.give(verdict, item.ref, where, item.contextId) }
    }

    fun verdictFor(item: ListItem): Verdict? = verdicts.value[item.ref.key]

    fun decide(item: ListItem, state: String) {
        val id = item.releaseId ?: return
        viewModelScope.launch { graph.feedback.decide(id, state) }
    }

    // Making mixes

    fun makeFromPrompt(prompt: String, length: Int?) = build(prompt.trim(), stagesFor(prompt)) { graph.make.fromPrompt(prompt.trim(), length) }

    fun makeFromAction(action: QuickAction) {
        val ready = action.listKey?.takeIf { graph.lists.find(it) != null }
        if (ready != null) _events.tryEmit(Event.OpenList(ready))  // a list already covers it: open that at once
        else build(action.label, stagesFor(action.label)) { graph.make.fromAction(action.recipe) }
    }

    /** "More like this" for what's playing, as a draft. */
    fun moreLikeNow() {
        val id = track.value?.id ?: return message("This track isn't matched in the catalogue yet")
        makeFromAction(QuickAction("More like ${track.value?.name}", "", JsonObject(mapOf("type" to JsonPrimitive("seed"), "track_id" to JsonPrimitive(id)))))
    }

    private fun build(title: String, stages: List<Pair<String, String>>, call: suspend () -> com.georgewaller.audiobrain.data.DraftResponse) {
        makingJob?.cancel()
        _making.value = Making.Building(title, stages)
        makingJob = viewModelScope.launch {
            try {
                val draft = call()
                val mix = draft.mix
                if (mix == null) {
                    _making.value = Making.Idle
                    message("Nothing matched: ${draft.understood}")
                } else _making.value = Making.Draft(mix, draft.understood, title)
            } catch (e: ApiException) {
                _making.value = Making.Idle
                message(e.message ?: "Couldn't make it")
            }
        }
    }

    private fun stagesFor(request: String) = listOf(
        "Reading your request" to "“${request.take(40)}”",
        "Searching your catalogue" to "22,800 analysed tracks, by sound and by what you play",
        "Scoring fit" to "how likely each is to stick, from what you keep",
        "Ordering" to "an energy arc, compatible keys, nearby tempos",
    )

    fun cancelMaking() {
        makingJob?.cancel()
        _making.value = Making.Idle
    }

    fun removeFromDraft(item: ListItem) {
        val d = _making.value as? Making.Draft ?: return
        _making.value = d.copy(removed = d.removed + (item.trackId ?: return))
    }

    fun keepDraft() {
        val d = _making.value as? Making.Draft ?: return
        viewModelScope.launch {
            try {
                val kept = graph.make.keep(d.mix.key, remove = d.removed.toList())
                graph.lists.upsert(kept)
                _making.value = Making.Idle
                message("Kept in Lists")
                _events.tryEmit(Event.OpenList(kept.key))
            } catch (e: ApiException) {
                message(e.message ?: "Couldn't keep it")
            }
        }
    }

    fun discardDraft() {
        val d = _making.value as? Making.Draft ?: return
        _making.value = Making.Idle
        viewModelScope.launch { runCatching { graph.make.discard(d.mix.key) } }
    }

    fun play(key: String) {
        viewModelScope.launch {
            try {
                val url = graph.make.play(key).tidalUrl
                if (url == null || !openUrl(app, url)) message("Log in to Tidal on the server to play lists there")
            } catch (e: ApiException) {
                message(e.message ?: "Couldn't send it to Tidal")
            }
        }
    }

    /** Keep a list in Tidal as its own playlist (to download it, say), or stop updating it there. */
    fun setSync(list: MixList, on: Boolean) {
        graph.lists.upsert(list.copy(sync = on))
        viewModelScope.launch {
            try {
                val r = graph.api.sync(list.key, on)
                graph.lists.upsert(list.copy(sync = r.sync, playlist = r.tidalUrl?.let { com.georgewaller.audiobrain.data.PlaylistDto(list.key, list.title, it, list.builtAt) } ?: list.playlist))
                message(
                    when {
                        !on -> "Stopped syncing; the playlist stays in Tidal"
                        r.tidalUrl != null -> "In Tidal as its own playlist; download it there"
                        else -> "Will sync once Tidal is logged in on the server"
                    }
                )
            } catch (e: ApiException) {
                graph.lists.upsert(list)
                message(e.message ?: "Couldn't change sync")
            }
        }
    }

    /** Take a track out of a list, or put one in. Kept through rebuilds; removing isn't a No. */
    fun editList(list: MixList, add: ListItem? = null, remove: ListItem? = null) {
        // show it straight away
        graph.lists.upsert(list.copy(items = list.items.filter { it.trackId != remove?.trackId } + listOfNotNull(add?.copy(reason = "You added this"))))
        viewModelScope.launch {
            try {
                graph.lists.upsert(graph.api.editList(list.key, listOfNotNull(add?.trackId), listOfNotNull(remove?.trackId)))
                message(if (add != null) "Added to ${shortName(list.title)}" else "Removed from this list")
            } catch (e: ApiException) {
                graph.lists.upsert(list)
                message(e.message ?: "Couldn't change the list")
            }
        }
    }

    /** Small, high-signal judgements for the list ranker.  The repository queues these first, so this is safe offline. */
    fun rateMix(key: String, rating: Int) = viewModelScope.launch {
        graph.feedback.rateMix(key, rating)
        message("Rated $rating out of 5; it will help shape future lists")
    }

    fun compareInMix(key: String, left: ListItem, right: ListItem, preferred: String) = viewModelScope.launch {
        val a = left.trackId ?: return@launch
        val b = right.trackId ?: return@launch
        graph.feedback.compare(key, a, b, preferred)
        message("Saved your preference for this mix")
    }

    suspend fun suggestionsFor(key: String): List<ListItem> = graph.api.suggestions(key)

    // Radio

    /** Start a radio from what's playing, or from a list item. Opens it in Tidal and in the app. */
    fun startRadio(item: ListItem? = null) {
        val np = nowPlaying.value
        val trackId = item?.trackId ?: track.value?.id
        val ref = if (item == null && trackId == null) np?.ref else null
        if (trackId == null && ref == null) return message("Nothing to start a radio from")
        val title = item?.title ?: np?.title ?: "this track"
        makingJob?.cancel()
        _making.value = Making.Building("Radio · $title", listOf(
            "Starting a radio" to "from $title",
            "Finding neighbours" to "close in sound and in what you play with it",
            "Ordering" to "an energy arc, compatible keys, nearby tempos",
        ))
        makingJob = viewModelScope.launch {
            try {
                val radio = graph.make.radio(trackId, ref)
                graph.lists.upsert(radio.mix)
                _making.value = Making.Idle
                _events.tryEmit(Event.OpenList(radio.mix.key))
                if (radio.tidalUrl != null) openUrl(app, radio.tidalUrl) else message("Radio ready. Log in to Tidal on the server to play it there")
            } catch (e: ApiException) {
                _making.value = Making.Idle
                message(e.message ?: "Couldn't start a radio")
            }
        }
    }

    /** An Explore list around an artist: by name (even one the catalogue doesn't know), by id, or by a track's artist. */
    fun explore(name: String? = null, artistId: Long? = null, trackId: Long? = null) {
        val label = name ?: "this artist"
        makingJob?.cancel()
        _making.value = Making.Building("Explore · $label", listOf(
            "Finding $label" to "in your catalogue, or on Tidal",
            "Listening" to "analysing tracks the catalogue hasn't heard yet",
            "Finding what sounds like them" to "in your library, then similar artists you haven't heard",
            "Ordering" to "from familiar to new",
        ))
        makingJob = viewModelScope.launch {
            try {
                val r = graph.api.explore(com.georgewaller.audiobrain.data.ExploreRequest(name, artistId, trackId))
                graph.lists.upsert(r.mix)
                _making.value = Making.Idle
                _events.tryEmit(Event.OpenList(r.mix.key))
            } catch (e: ApiException) {
                _making.value = Making.Idle
                message(e.message ?: "Couldn't explore that")
            }
        }
    }

    fun extendRadio(key: String) {
        viewModelScope.launch {
            try {
                val r = graph.make.extendRadio(key)
                graph.lists.upsert(r.mix)
                message("Added ${r.added} tracks")
            } catch (e: ApiException) {
                message(e.message ?: "Couldn't add more")
            }
        }
    }

    private val extendedAt = mutableMapOf<String, Int>()

    /** When what's playing is one of the last five tracks of a radio, add the next batch, once per length. */
    private fun extendRadioIfNearEnd(np: NowPlaying) {
        for (radio in lists.value.lists.filter { it.kind == "radio" }) {
            val tracks = radio.tracks
            val index = tracks.indexOfFirst { (np.tidalId != null && it.tidalId == np.tidalId) || (it.title.equals(np.title, true) && it.artist.equals(np.artist, true)) }
            if (index >= 0 && index >= tracks.size - 5 && extendedAt[radio.key] != tracks.size) {
                extendedAt[radio.key] = tracks.size
                extendRadio(radio.key)
            }
        }
    }

    fun refFor(np: NowPlaying): TrackRef = np.ref.copy(id = track.value?.id)
}
