package com.georgewaller.audiobrain.pages

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.LibrarySummary
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * The Library home's state: how the catalogue splits by when you played it, how big each part is, and what you've
 * been in lately. Kept outside the page so it survives sliding away and back; each list it opens loads its own pages.
 */
@OptIn(FlowPreview::class)
class LibraryViewModel(private val graph: DataGraph) : ViewModel() {
    data class State(val summary: LibrarySummary? = null, val loading: Boolean = false, val error: String? = null)

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    init {
        // load once connected, and again whenever the server or token changes
        viewModelScope.launch { graph.settings.settings.map { it.serverUrl to it.token }.distinctUntilChanged().collect { if (it.first.isNotBlank()) reload() } }
        // and once the network comes back, if the last load failed
        viewModelScope.launch { graph.network.changes.debounce(1_500).collect { retryIfFailed() } }
    }

    fun retryIfFailed() { if (_state.value.error != null) reload() }

    fun reload() {
        if (_state.value.loading) return
        _state.update { it.copy(loading = true) }
        viewModelScope.launch {
            try {
                val summary = graph.api.librarySummary()
                _state.update { State(summary = summary) }
            } catch (e: ApiException) {
                _state.update { it.copy(loading = false, error = e.message) }
            }
        }
    }
}
