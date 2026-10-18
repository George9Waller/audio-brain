package com.georgewaller.audiobrain.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Why a call didn't work, in the terms the UI needs: set it up, check the token, it's offline, or the server said no. */
sealed class ApiException(message: String) : Exception(message) {
    class NotConfigured : ApiException("Add the server and token in Settings")
    class Unauthorized : ApiException("The server didn't accept the token")
    class Offline(cause: IOException) : ApiException("Can't reach the server") {
        init { initCause(cause) }
    }
    class Http(val code: Int, detail: String) : ApiException(detail)
}

val ApiJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
    coerceInputValues = true
}

/** The home server's API (server/api), over Tailscale. Every call reads the current settings, so changes apply at once. */
class ApiClient(private val settings: SettingsStore) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    suspend fun health(): Health = get("/health")
    suspend fun jobs(): JobsResponse = get("/jobs")
    suspend fun runJob(key: String, options: Map<String, Boolean> = emptyMap()): RunDetail =
        post("/jobs/$key/run", "{\"options\":${ApiJson.encodeToString(options)}}")
    suspend fun run(id: Long): RunDetail = get("/runs/$id")
    suspend fun cancelRun(id: Long): RunDetail = post("/runs/$id/cancel", "{}")
    suspend fun now(country: String = ""): NowResponse = get(if (country.isBlank()) "/now" else "/now?country=$country")
    suspend fun resolve(ref: TrackRef): Resolved = post("/tracks/resolve", ApiJson.encodeToString(ref))
    suspend fun sync(request: SyncRequest): SyncResponse = post("/sync", ApiJson.encodeToString(request))
    suspend fun actions(playing: Long? = null): List<QuickAction> = get(if (playing != null) "/actions?playing=$playing" else "/actions")
    suspend fun make(request: MakeRequest): DraftResponse = post("/make", ApiJson.encodeToString(request))
    suspend fun keepDraft(key: String, request: KeepRequest): MixList = post("/drafts/${enc(key)}/keep", ApiJson.encodeToString(request))
    suspend fun discardDraft(key: String) { call("/drafts/${enc(key)}", null, method = "DELETE") }
    suspend fun play(key: String): Played = post("/mixes/${enc(key)}/play", "{}")
    suspend fun editList(key: String, add: List<Long>, remove: List<Long>): MixList = post("/mixes/${enc(key)}/edit", ApiJson.encodeToString(EditRequest(add, remove)))
    suspend fun suggestions(key: String): List<ListItem> = get("/mixes/${enc(key)}/suggestions")
    suspend fun sync(key: String, on: Boolean): Synced = post("/mixes/${enc(key)}/sync", ApiJson.encodeToString(SyncRequest2(on)))
    suspend fun radio(request: RadioRequest): RadioResponse = post("/radio", ApiJson.encodeToString(request))
    suspend fun explore(request: ExploreRequest): RadioResponse = post("/explore", ApiJson.encodeToString(request))
    suspend fun extendRadio(key: String): RadioResponse = post("/radio/${enc(key)}/extend", "{}")

    suspend fun contexts(): List<ContextDto> = get("/contexts")
    suspend fun contextTracks(id: Long): List<ListItem> = get("/contexts/$id/tracks")
    suspend fun artistTracks(id: Long): List<ListItem> = get("/artists/$id/tracks")
    suspend fun search(q: String, sound: Boolean): SearchResult = post("/search", ApiJson.encodeToString(SearchRequest(q, sound)))

    suspend fun libraryTracks(q: String, sort: String, offset: Int): TrackPage = get("/library/tracks?${query(q, sort, offset)}")
    suspend fun libraryArtists(q: String, sort: String, offset: Int): List<ArtistRow> = get("/library/artists?${query(q, sort, offset)}")
    suspend fun libraryAlbums(q: String, sort: String, offset: Int): List<AlbumRow> = get("/library/albums?${query(q, sort, offset)}")
    suspend fun librarySets(q: String, source: String, offset: Int): List<SetRow> = get("/library/sets?q=${enc(q)}&source=$source&offset=$offset")
    suspend fun artistPage(id: Long): ArtistPage = get("/library/artists/$id")
    suspend fun albumPage(id: Long): AlbumPage = get("/library/albums/$id")
    suspend fun setPage(id: Long): SetPage = get("/library/sets/$id")

    suspend fun mapRaw(): String = call("/map", null)
    suspend fun track(id: Long): TrackDto = get("/tracks/$id")

    private fun query(q: String, sort: String, offset: Int) = "q=${enc(q)}&sort=$sort&offset=$offset"

    private fun enc(key: String) = java.net.URLEncoder.encode(key, "UTF-8")

    /** Raw body, for the cache: [now] is stored as the server sent it. */
    suspend fun getRaw(path: String): String = call(path, null)

    private suspend inline fun <reified T> get(path: String): T = ApiJson.decodeFromString(call(path, null))
    private suspend inline fun <reified T> post(path: String, body: String): T = ApiJson.decodeFromString(call(path, body))

    private suspend fun call(path: String, body: String?, method: String? = null): String = withContext(Dispatchers.IO) {
        val (server, token) = settings.connection()
        if (server.isBlank() || token.isBlank()) throw ApiException.NotConfigured()
        val request = Request.Builder()
            .url(server.trimEnd('/') + "/api/v1" + path)
            .header("Authorization", "Bearer $token")
            .apply { if (method != null) method(method, body?.toRequestBody(JSON)) else if (body != null) post(body.toRequestBody(JSON)) }
            .build()
        try {
            http.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                when {
                    response.isSuccessful -> text
                    response.code == 401 -> throw ApiException.Unauthorized()
                    else -> throw ApiException.Http(response.code, detail(text) ?: "Server error ${response.code}")
                }
            }
        } catch (e: IOException) {
            throw ApiException.Offline(e)
        } catch (e: IllegalArgumentException) {
            throw ApiException.Http(0, "The server address isn't a valid URL")
        }
    }

    private fun detail(text: String) = runCatching { ApiJson.parseToJsonElement(text) as JsonObject }.getOrNull()?.get("detail")?.jsonPrimitive?.content

    companion object {
        private val JSON = "application/json".toMediaType()
    }
}
