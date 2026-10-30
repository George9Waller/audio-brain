package com.georgewaller.audiobrain.pages

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.georgewaller.audiobrain.data.AlbumRow
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.ArtistRow
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.SetRow
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * The Catalogue page's state: one tab (tracks, artists, albums, sets) at a time, with a search, a sort, and pages of
 * 50 loaded as you scroll. Kept outside the page so it survives sliding away and back.
 */
@OptIn(FlowPreview::class)
class LibraryViewModel(private val graph: DataGraph) : ViewModel() {
    enum class Tab(val label: String) { Tracks("Tracks"), Artists("Artists"), Albums("Albums"), Sets("Sets") }

    data class State(
        val tab: Tab = Tab.Tracks,
        val query: String = "",
        val sort: String = "plays",       // plays, recent, az
        val setSource: String = "",       // "", played, saved, new
        val tracks: List<ListItem> = emptyList(),
        val total: Int? = null,
        val artists: List<ArtistRow> = emptyList(),
        val albums: List<AlbumRow> = emptyList(),
        val sets: List<SetRow> = emptyList(),
        val loading: Boolean = false,
        val end: Boolean = false,
        val error: String? = null,
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()
    private var job: Job? = null

    init {
        // load once connected, and again whenever the server or token changes
        viewModelScope.launch { graph.settings.settings.map { it.serverUrl to it.token }.distinctUntilChanged().collect { if (it.first.isNotBlank()) reload() } }
        // and once the network comes back, if the last load failed
        viewModelScope.launch { graph.network.changes.debounce(1_500).collect { retryIfFailed() } }
    }

    fun retryIfFailed() { if (_state.value.error != null) reload() }

    fun setTab(tab: Tab) { _state.update { it.copy(tab = tab, sort = if (tab != Tab.Tracks && it.sort == "recent") "plays" else it.sort) }; reload() }
    fun setQuery(q: String) { _state.update { it.copy(query = q) }; reload(debounce = true) }
    fun setSort(sort: String) { _state.update { it.copy(sort = sort) }; reload() }
    fun setSource(source: String) { _state.update { it.copy(setSource = source) }; reload() }

    private fun reload(debounce: Boolean = false) {
        job?.cancel()
        _state.update { it.copy(tracks = emptyList(), artists = emptyList(), albums = emptyList(), sets = emptyList(), total = null, end = false, error = null) }
        job = viewModelScope.launch {
            if (debounce) delay(300)
            load()
        }
    }

    /** The next page, when the list is scrolled near its end. */
    fun more() {
        val s = _state.value
        if (s.loading || s.end) return
        job = viewModelScope.launch { load() }
    }

    private suspend fun load() {
        val s = _state.value
        _state.update { it.copy(loading = true) }
        try {
            when (s.tab) {
                Tab.Tracks -> {
                    val page = graph.api.libraryTracks(s.query, s.sort, s.tracks.size)
                    _state.update { it.copy(tracks = it.tracks + page.items, total = page.total, end = it.tracks.size + page.items.size >= page.total) }
                }
                Tab.Artists -> graph.api.libraryArtists(s.query, s.sort, s.artists.size).let { rows -> _state.update { it.copy(artists = it.artists + rows, end = rows.size < 50) } }
                Tab.Albums -> graph.api.libraryAlbums(s.query, s.sort, s.albums.size).let { rows -> _state.update { it.copy(albums = it.albums + rows, end = rows.size < 50) } }
                Tab.Sets -> graph.api.librarySets(s.query, s.setSource, s.sets.size).let { rows -> _state.update { it.copy(sets = it.sets + rows, end = rows.size < 50) } }
            }
            _state.update { it.copy(loading = false, error = null) }
        } catch (e: ApiException) {
            _state.update { it.copy(loading = false, error = e.message, end = true) }
        }
    }
}
