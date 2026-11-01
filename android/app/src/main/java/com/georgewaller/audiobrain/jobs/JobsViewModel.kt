package com.georgewaller.audiobrain.jobs

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.JobsResponse
import com.georgewaller.audiobrain.data.RunDetail
import com.georgewaller.audiobrain.data.ServerWarning
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** The server's jobs: when they run, how the last run went, and starting one now. Polls faster while one is running. */
class JobsViewModel(private val graph: DataGraph) : ViewModel() {

    data class State(
        val jobs: JobsResponse? = null,
        val error: String? = null,
        val loading: Boolean = false,
        val expanded: String? = null,         // job key
        val detail: RunDetail? = null,        // the expanded job's active or latest run
        val starting: Set<String> = emptySet(),
        val options: Map<String, Map<String, Boolean>> = emptyMap(),
        val warnings: List<ServerWarning> = emptyList(),  // from /health: failed jobs, stale models
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    /** Runs while the Jobs screen is on screen (called from a LaunchedEffect). */
    suspend fun poll() {
        while (true) {
            load()
            val busy = _state.value.jobs?.jobs?.any { it.activeRun != null } == true
            delay(if (busy) 3_000 else 20_000)
        }
    }

    suspend fun load() {
        _state.update { it.copy(loading = it.jobs == null) }
        try {
            val jobs = graph.jobs.list()
            _state.update { it.copy(jobs = jobs, error = null, loading = false) }
            runCatching { graph.jobs.warnings() }.onSuccess { w -> _state.update { it.copy(warnings = w) } }
            loadDetail()
        } catch (e: ApiException) {
            _state.update { it.copy(error = e.message, loading = false) }
        }
    }

    private suspend fun loadDetail() {
        val key = _state.value.expanded ?: return
        val job = _state.value.jobs?.jobs?.firstOrNull { it.key == key } ?: return
        val runId = (job.activeRun ?: job.lastRun)?.id ?: return _state.update { it.copy(detail = null) }
        runCatching { graph.jobs.detail(runId) }.onSuccess { d -> _state.update { if (it.expanded == key) it.copy(detail = d) else it } }
    }

    fun toggle(key: String) {
        _state.update { if (it.expanded == key) it.copy(expanded = null, detail = null) else it.copy(expanded = key, detail = null) }
        viewModelScope.launch { loadDetail() }
    }

    fun setOption(key: String, option: String, on: Boolean) = _state.update {
        it.copy(options = it.options + (key to ((it.options[key] ?: emptyMap()) + (option to on))))
    }

    fun run(key: String, onMessage: (String) -> Unit) {
        _state.update { it.copy(starting = it.starting + key) }
        viewModelScope.launch {
            try {
                val run = graph.jobs.run(key, _state.value.options[key] ?: emptyMap())
                val title = _state.value.jobs?.jobs?.firstOrNull { it.key == key }?.title ?: key
                onMessage(if (run.state == "running") "$title is already running" else "$title queued")
                load()
            } catch (e: ApiException) {
                onMessage(e.message ?: "Couldn't start it")
            } finally {
                _state.update { it.copy(starting = it.starting - key) }
            }
        }
    }

    fun cancel(runId: Long, onMessage: (String) -> Unit) {
        viewModelScope.launch {
            try {
                graph.jobs.cancel(runId)
                onMessage("Cancelled")
                load()
            } catch (e: ApiException) {
                onMessage(e.message ?: "Couldn't cancel it")
            }
        }
    }
}
