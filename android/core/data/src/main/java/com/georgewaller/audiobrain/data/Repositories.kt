package com.georgewaller.audiobrain.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import java.io.File
import java.util.UUID

/** Anything that can hand out the graph: the Application. Lets workers, receivers and services reach it. */
interface DataGraphOwner {
    val graph: DataGraph
}

val Context.dataGraph: DataGraph get() = (applicationContext as DataGraphOwner).graph

/** The data layer, built once per process. */
class DataGraph(context: Context) {
    private val app = context.applicationContext
    val settings = SettingsStore(app)
    val api = ApiClient(settings)
    val db = OutboxDatabase.create(app)
    val feedback = FeedbackRepository(app, db.feedback(), db.queue(), db.ratings(), db.comparisons(), db.listEdits())
    val now = NowRepository(app, api)
    val lists = ListsRepository(app, api)
    val make = MakeRepository(api)
    val map = MapRepository(app, api)
    val jobs = JobsRepository(api)
}

class FeedbackRepository(private val context: Context, private val dao: FeedbackDao, private val queue: QueueDao, private val ratings: RatingDao, private val comparisons: ComparisonDao, private val listEdits: ListEditDao) {
    fun latestFor(track: TrackRef): Flow<FeedbackEntity?> = dao.latestFor(track.key)
    fun pendingCount(): Flow<Int> = combine(dao.pendingCount(), queue.pendingCount()) { a, b -> a + b }
    fun recent(limit: Int = 50): Flow<List<FeedbackEntity>> = dao.recent(limit)

    /** The latest verdict given on this device for every track key; rows overlay it on what the server said. */
    val verdicts: Flow<Map<String, Verdict>> = dao.latestAll().map { rows -> rows.mapNotNull { r -> Verdict.fromApi(r.verdict)?.let { r.trackKey to it } }.toMap() }

    /** The latest queue decision per release made on this device. */
    val queueStates: Flow<Map<Long, String>> = queue.latest().map { rows -> rows.associate { it.releaseId to it.state } }

    /**
     * Record a verdict and ask for it to be sent. Changing your mind within [REVISE_WINDOW_MS] replaces the earlier
     * verdict if it hasn't gone yet; tapping the same one again takes it back. Works offline: it's sent later.
     * Returns the verdict that now stands for the track, or null if it was taken back.
     */
    suspend fun give(verdict: Verdict, track: TrackRef, where: String, contextId: Long? = null): Verdict? {
        val previous = dao.latestForNow(track.key)
        val now = System.currentTimeMillis()
        val revisable = previous != null && previous.state == FeedbackEntity.PENDING && now - previous.givenAt < REVISE_WINDOW_MS
        if (revisable && previous!!.verdict == verdict.api) {
            dao.deletePending(previous.clientId)
            return null
        }
        dao.upsert(
            FeedbackEntity(
                clientId = if (revisable) previous!!.clientId else UUID.randomUUID().toString(),
                verdict = verdict.api, trackKey = track.key, trackId = track.id, tidalId = track.tidalId,
                artist = track.artist, title = track.title, album = track.album, durationMs = track.durationMs,
                packageName = track.packageName, where = where, contextId = contextId, givenAt = now,
            )
        )
        SyncScheduler.requestNow(context)
        return verdict
    }

    /** Save or dismiss a release in the discovery queue; offline-safe like verdicts. */
    suspend fun decide(releaseId: Long, state: String) {
        queue.upsert(QueueEntity(UUID.randomUUID().toString(), releaseId, state, System.currentTimeMillis()))
        SyncScheduler.requestNow(context)
    }

    /** Rate a whole list or choose which of two tracks fits it better; both survive offline use. */
    suspend fun rateMix(key: String, rating: Int, note: String = "") {
        require(rating in 1..5)
        ratings.upsert(MixRatingEntity(UUID.randomUUID().toString(), key, rating, note, System.currentTimeMillis()))
        SyncScheduler.requestNow(context)
    }

    suspend fun compare(key: String, leftId: Long, rightId: Long, preferred: String) {
        require(leftId != rightId && preferred in setOf("left", "right"))
        comparisons.upsert(ComparisonEntity(UUID.randomUUID().toString(), leftId, rightId, preferred, key, System.currentTimeMillis()))
        SyncScheduler.requestNow(context)
    }

    suspend fun editList(key: String, addId: Long? = null, removeId: Long? = null) {
        listEdits.upsert(ListEditEntity(UUID.randomUUID().toString(), key, addId, removeId, System.currentTimeMillis()))
        SyncScheduler.requestNow(context)
    }

    companion object {
        const val REVISE_WINDOW_MS = 2 * 60 * 1000L
    }
}

/** What to play now. The last answer is kept on disk, so the screen has something to show offline. */
class NowRepository(context: Context, private val api: ApiClient) {
    private val cache = File(context.filesDir, "now.json")
    private val tracks = object : LinkedHashMap<String, Resolved>(64, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Resolved>?) = size > 64
    }

    suspend fun cached(): NowResponse? = withContext(Dispatchers.IO) {
        if (cache.exists()) runCatching { ApiJson.decodeFromString<NowResponse>(cache.readText()) }.getOrNull() else null
    }

    suspend fun refresh(): NowResponse {
        val raw = api.getRaw("/now")
        val parsed = ApiJson.decodeFromString<NowResponse>(raw)
        withContext(Dispatchers.IO) { cache.writeText(raw) }
        return parsed
    }

    /** The catalogue track (and set, if it's one) for what's playing, cached for the session. */
    suspend fun resolve(ref: TrackRef): Resolved {
        synchronized(tracks) { tracks[ref.key]?.let { return it } }
        val resolved = api.resolve(ref)
        synchronized(tracks) { tracks[ref.key] = resolved }
        return resolved
    }
}

/**
 * Every list (mixes, radios, asked mixes and discovery lists), kept on disk so Lists, Home and Discover work
 * offline. [state] starts from the cache and updates when the server answers.
 */
class ListsRepository(context: Context, private val api: ApiClient) {
    data class State(val lists: List<MixList> = emptyList(), val fromCache: Boolean = true, val loading: Boolean = false, val error: String? = null)

    private val cache = File(context.filesDir, "lists.json")
    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    /** The lists on disk, without asking the server: for the widget. */
    suspend fun cached(): List<MixList> = _state.value.lists.ifEmpty {
        withContext(Dispatchers.IO) { if (cache.exists()) runCatching { ApiJson.decodeFromString<ListsResponse>(cache.readText()).lists }.getOrNull() else null }.orEmpty()
    }

    suspend fun load() {
        if (_state.value.lists.isEmpty()) {
            withContext(Dispatchers.IO) {
                if (cache.exists()) runCatching { ApiJson.decodeFromString<ListsResponse>(cache.readText()) }.getOrNull()
                else null
            }?.let { cached -> _state.update { it.copy(lists = cached.lists, fromCache = true) } }
        }
        refresh()
    }

    suspend fun refresh() {
        _state.update { it.copy(loading = true, error = null) }
        try {
            val raw = api.getRaw("/lists")
            val parsed = ApiJson.decodeFromString<ListsResponse>(raw)
            withContext(Dispatchers.IO) { cache.writeText(raw) }
            _state.value = State(parsed.lists, fromCache = false)
        } catch (e: ApiException) {
            _state.update { it.copy(loading = false, error = e.message) }
        }
    }

    /** Put a list in place (a new radio, a kept draft, an extended radio) without waiting for a full refresh. */
    fun upsert(list: MixList) = _state.update { s ->
        val rest = s.lists.filterNot { it.key == list.key }
        s.copy(lists = listOf(list) + rest)
    }

    fun find(key: String) = _state.value.lists.firstOrNull { it.key == key }

    fun remove(key: String) = _state.update { s -> s.copy(lists = s.lists.filterNot { it.key == key }) }
}

/** Asking for a mix, radio, and what's done with a draft. Needs the server. */
class MakeRepository(private val api: ApiClient) {
    suspend fun actions(playing: Long?) = api.actions(playing)
    /** No length: whatever the prompt says ("two hours of..."), else 30. */
    suspend fun fromPrompt(prompt: String, length: Int? = null, exploration: Double? = null) = api.make(MakeRequest(prompt = prompt, length = length, exploration = exploration))
    suspend fun fromAction(recipe: JsonObject, length: Int? = null, exploration: Double? = null) = api.make(MakeRequest(recipe = recipe, length = length, exploration = exploration))
    suspend fun keep(key: String, title: String? = null, remove: List<Long> = emptyList()) = api.keepDraft(key, KeepRequest(title, remove))
    suspend fun discard(key: String) = api.discardDraft(key)
    suspend fun play(key: String) = api.play(key)
    suspend fun radio(trackId: Long?, ref: TrackRef?) = api.radio(RadioRequest(trackId, ref))
    suspend fun extendRadio(key: String) = api.extendRadio(key)
}

class JobsRepository(private val api: ApiClient) {
    suspend fun list() = api.jobs()
    suspend fun run(key: String, options: Map<String, Boolean> = emptyMap()) = api.runJob(key, options)
    suspend fun detail(id: Long) = api.run(id)
    suspend fun cancel(id: Long) = api.cancelRun(id)
}

/** The map's points and places, kept on disk: the layout changes once a night, so it's fetched when the app opens. */
class MapRepository(context: Context, private val api: ApiClient) {
    private val cache = File(context.filesDir, "map.json")
    private val _data = MutableStateFlow<MapData?>(null)
    val data: StateFlow<MapData?> = _data.asStateFlow()

    suspend fun load() {
        if (_data.value == null) withContext(Dispatchers.Default) {
            if (cache.exists()) runCatching { ApiJson.decodeFromString<MapData>(cache.readText()) }.getOrNull()?.let { _data.value = it }
        }
        runCatching {
            val raw = api.mapRaw()
            val parsed = withContext(Dispatchers.Default) { ApiJson.decodeFromString<MapData>(raw) }
            if (parsed.version != _data.value?.version) {
                _data.value = parsed
                withContext(Dispatchers.IO) { cache.writeText(raw) }
            }
        }
    }
}
