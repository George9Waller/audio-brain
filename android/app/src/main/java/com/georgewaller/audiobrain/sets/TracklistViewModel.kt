package com.georgewaller.audiobrain.sets

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.FixLineRequest
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.Tracklist
import com.georgewaller.audiobrain.data.TracklistRequest
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * Giving the server a set's tracklist: pasted or shared text, or a link (1001Tracklists, MixesDB, YouTube,
 * SoundCloud). The server answers at once from the catalogue and looks the rest up on Tidal in the background, so
 * while lines are pending this polls until they've all been looked up. Lines it got wrong are fixed one at a time.
 * When a site turns the server away (1001Tracklists' browser check), the link opens in the app's own browser and
 * the page it shows is sent instead.
 */
class TracklistViewModel(private val graph: DataGraph) : ViewModel() {
    data class State(
        val text: String = "",
        val title: String = "",
        val dj: String = "",
        val attachTo: Long? = null,  // an existing set this tracklist is for
        val reading: Boolean = false,
        val browse: String? = null,  // a link the server couldn't fetch, open in the app's browser
        val result: Tracklist? = null,
        val error: String? = null,
        val fixing: Int? = null,  // the position whose match is being fixed
        val candidates: List<ListItem> = emptyList(),
        val searching: Boolean = false,
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()
    private var poll: Job? = null
    private var search: Job? = null

    /** Start fresh with shared or prefilled text, for a set if [attachTo], or open an existing set's matches. */
    fun start(prefill: String, attachTo: Long?, show: Long?) {
        poll?.cancel()
        _state.value = State(text = prefill, attachTo = attachTo)
        if (show != null) load(show)
    }

    fun setText(v: String) = _state.update { it.copy(text = v, error = null) }
    fun setTitle(v: String) = _state.update { it.copy(title = v) }
    fun setDj(v: String) = _state.update { it.copy(dj = v) }

    fun read() {
        val s = _state.value
        val input = s.text.trim()
        if (input.isBlank() || s.reading) return
        val url = linkIn(input)
        _state.update { it.copy(reading = true, error = null) }
        viewModelScope.launch {
            try {
                val request = TracklistRequest(text = if (url == null) input else "", url = url.orEmpty(), title = s.title.trim(), dj = s.dj.trim(), setId = s.attachTo)
                show(graph.api.addTracklist(request))
            } catch (e: ApiException) {
                if (url != null && e is ApiException.Http && e.code == BLOCKED) _state.update { it.copy(reading = false, browse = url) }
                else _state.update { it.copy(reading = false, error = e.message ?: "Couldn't read that tracklist") }
            }
        }
    }

    /** Read [html], the page the app's browser shows at [url], for the link the server couldn't fetch. */
    fun readPage(url: String, html: String) {
        val s = _state.value
        if (s.reading) return
        _state.update { it.copy(reading = true, browse = null, error = null) }
        viewModelScope.launch {
            try {
                show(graph.api.addTracklist(TracklistRequest(url = url, html = html, title = s.title.trim(), dj = s.dj.trim(), setId = s.attachTo)))
            } catch (e: ApiException) {
                _state.update { it.copy(reading = false, error = e.message ?: "Couldn't read that page") }
            }
        }
    }

    fun closeBrowser() = _state.update { it.copy(browse = null) }

    private fun load(setId: Long) {
        _state.update { it.copy(reading = true) }
        viewModelScope.launch {
            try { show(graph.api.tracklist(setId)) } catch (e: ApiException) { _state.update { it.copy(reading = false, error = e.message) } }
        }
    }

    private suspend fun show(result: Tracklist) {
        _state.update { it.copy(reading = false, result = result) }
        graph.lists.refresh()  // its "From this set" list
        if (result.summary.pending > 0) watch(result.set.id)
    }

    /** Ask again every few seconds while the server is still looking lines up on Tidal (about a few minutes at most). */
    private fun watch(setId: Long) {
        poll?.cancel()
        poll = viewModelScope.launch {
            repeat(60) {
                delay(4000)
                val latest = runCatching { graph.api.tracklist(setId) }.getOrNull() ?: return@repeat
                _state.update { it.copy(result = latest) }
                if (latest.summary.pending == 0) {
                    graph.lists.refresh()
                    return@launch
                }
            }
        }
    }

    fun openFix(position: Int) {
        _state.update { it.copy(fixing = position, candidates = emptyList()) }
        findCandidates("")
    }

    fun closeFix() = _state.update { it.copy(fixing = null, candidates = emptyList()) }

    fun findCandidates(q: String) {
        val set = _state.value.result?.set ?: return
        val position = _state.value.fixing ?: return
        search?.cancel()
        search = viewModelScope.launch {
            if (q.isNotBlank()) delay(300)
            _state.update { it.copy(searching = true) }
            val found = runCatching { graph.api.lineCandidates(set.id, position, q) }.getOrDefault(emptyList())
            _state.update { it.copy(candidates = found, searching = false) }
        }
    }

    /** Match the line being fixed to [track], or mark it an ID, or leave it unmatched. */
    fun fix(track: ListItem? = null, isId: Boolean = false, clear: Boolean = false) {
        val set = _state.value.result?.set ?: return
        val position = _state.value.fixing ?: return
        closeFix()
        viewModelScope.launch {
            try {
                _state.update { it.copy(result = graph.api.fixLine(set.id, position, FixLineRequest(trackId = track?.trackId, id = isId, clear = clear))) }
                graph.lists.refresh()
            } catch (e: ApiException) {
                _state.update { it.copy(error = e.message ?: "Couldn't fix that line") }
            }
        }
    }

    fun again() {
        poll?.cancel()
        _state.update { State(attachTo = it.attachTo) }
    }

    companion object {
        private const val BLOCKED = 428  // the server was turned away from the link: open it here
        private val URL = Regex("""https?://\S+""")

        /** A shared or pasted link, when that's all the text is (sharing from a browser adds the page title). */
        fun linkIn(text: String): String? {
            val link = URL.find(text)?.value?.trimEnd('.', ',', ')') ?: return null
            val rest = text.replace(link, "").trim()
            return link.takeIf { rest.lines().size <= 2 && rest.length < 200 }
        }
    }
}
