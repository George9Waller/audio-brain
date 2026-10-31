package com.georgewaller.audiobrain.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.OffsetDateTime

/** The server's types, as `server/api` sends them. Times stay ISO strings on the wire; [instant] parses them. */

fun instant(iso: String?): Instant? = iso?.let { runCatching { OffsetDateTime.parse(it).toInstant() }.getOrNull() }

@Serializable
data class Health(val ok: Boolean, @SerialName("server_time") val serverTime: String, @SerialName("worker_alive") val workerAlive: Boolean)

// Jobs

@Serializable
data class WorkerState(
    val alive: Boolean,
    val scheduling: Boolean,
    @SerialName("seen_at") val seenAt: String? = null,
    @SerialName("started_at") val startedAt: String? = null,
    val host: String = "",
)

@Serializable
data class Step(val name: String, val state: String, val seconds: Int? = null, val result: String? = null)

@Serializable
data class RunSummary(
    val id: Long,
    val job: String,
    val state: String,
    val trigger: String,
    @SerialName("queued_at") val queuedAt: String,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
    @SerialName("steps_done") val stepsDone: Int,
    @SerialName("steps_total") val stepsTotal: Int,
    @SerialName("steps_failed") val stepsFailed: Int,
    @SerialName("current_step") val currentStep: String? = null,
) {
    val active get() = state == "queued" || state == "running"
}

@Serializable
data class RunDetail(
    val id: Long,
    val job: String,
    val state: String,
    val trigger: String,
    @SerialName("queued_at") val queuedAt: String,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
    @SerialName("steps_done") val stepsDone: Int,
    @SerialName("steps_total") val stepsTotal: Int,
    @SerialName("steps_failed") val stepsFailed: Int,
    @SerialName("current_step") val currentStep: String? = null,
    val options: Map<String, Boolean> = emptyMap(),
    val steps: List<Step> = emptyList(),
    val log: String = "",
    val error: String = "",
) {
    val active get() = state == "queued" || state == "running"
}

@Serializable
data class Job(
    val key: String,
    val title: String,
    val description: String,
    val schedule: String? = null,
    val lane: String,
    val options: Map<String, String> = emptyMap(),
    @SerialName("next_due") val nextDue: String? = null,
    @SerialName("active_run") val activeRun: RunSummary? = null,
    @SerialName("last_run") val lastRun: RunSummary? = null,
    @SerialName("last_success_at") val lastSuccessAt: String? = null,
)

@Serializable
data class JobsResponse(val worker: WorkerState, val jobs: List<Job>)

// Music

@Serializable
data class PlaylistDto(val key: String, val name: String, @SerialName("tidal_url") val tidalUrl: String? = null, @SerialName("updated_at") val updatedAt: String)

@Serializable
data class ContextDto(
    val id: Long,
    val name: String,
    val description: String = "",
    val family: String = "",
    @SerialName("era_start") val eraStart: String? = null,
    @SerialName("era_end") val eraEnd: String? = null,
    val size: Int = 0,
    @SerialName("bpm_low") val bpmLow: Double? = null,
    @SerialName("bpm_high") val bpmHigh: Double? = null,
    val energy: Double? = null,
    val instrumental: Double? = null,
    @SerialName("top_styles") val topStyles: List<String> = emptyList(),
    @SerialName("top_artists") val topArtists: List<String> = emptyList(),
    @SerialName("novelty_share") val noveltyShare: Double = 0.35,
    val hue: Float = 0f,
    val playlist: PlaylistDto? = null,
    val probability: Double? = null,
)

@Serializable
data class NowResponse(
    val at: String,
    @SerialName("ranked_by") val rankedBy: String,
    val contexts: List<ContextDto>,
    val mixes: List<PlaylistDto> = emptyList(),
)

@Serializable
data class Sound(
    val bpm: Float,
    val key: String,
    val scale: String,
    val camelot: String? = null,
    val energy: Float,
    val danceability: Float,
    val instrumental: Float,
    val happy: Float,
    val sad: Float,
    val aggressive: Float,
    val relaxed: Float,
    val party: Float,
    val shape: List<Float>? = null,  // the moods ranked within its context, for the sigil (server/recommend/sigil.py)
)

@Serializable
data class TrackContext(val id: Long, val name: String, val hue: Float)

@Serializable
data class VerdictDto(val verdict: String, @SerialName("given_at") val givenAt: String, val where: String = "")

@Serializable
data class TrackDto(
    val id: Long,
    val name: String,
    val artist: String,
    val album: String? = null,
    @SerialName("length_ms") val lengthMs: Long? = null,
    val isrc: String = "",
    @SerialName("tidal_id") val tidalId: String? = null,
    @SerialName("tidal_url") val tidalUrl: String? = null,
    val plays: Int = 0,
    @SerialName("last_played") val lastPlayed: String? = null,
    val favourite: Boolean = false,
    val verdict: VerdictDto? = null,
    val context: TrackContext? = null,
    val sound: Sound? = null,
)

@Serializable
data class Resolved(val track: TrackDto? = null, @SerialName("set_id") val setId: Long? = null)

/** A track as the app knows it: a catalogue ID, or what a player reported. Sent with feedback and lookups. */
@Serializable
data class TrackRef(
    val id: Long? = null,
    @SerialName("tidal_id") val tidalId: String? = null,
    val artist: String = "",
    val title: String = "",
    val album: String = "",
    @SerialName("duration_ms") val durationMs: Long? = null,
    @SerialName("package") val packageName: String = "",
    @SerialName("media_id") val mediaId: String? = null,  // the player's own ID: YouTube Music's is the video ID
) {
    /**
     * Same track for the app's purposes: the key lookups and local verdicts are stored under. The player's own
     * identity comes first, so a verdict from the notification and one from the app (which may also know the
     * catalogue ID) land on the same key.
     */
    val key: String
        get() = tidalId?.let { "tidal:$it" } ?: if (title.isNotBlank()) "${artist.lowercase()}\u0000${title.lowercase()}" else "id:$id"
}

// Sync

@Serializable
data class FeedbackItem(
    @SerialName("client_id") val clientId: String,
    val verdict: String,
    val track: TrackRef,
    val note: String = "",
    val where: String = "",
    @SerialName("context_id") val contextId: Long? = null,
    @SerialName("given_at") val givenAt: String,
)

@Serializable
data class MixRatingItem(@SerialName("client_id") val clientId: String, val key: String, val rating: Int, val note: String = "", @SerialName("given_at") val givenAt: String)

@Serializable
data class ComparisonItem(@SerialName("client_id") val clientId: String, val left: TrackRef, val right: TrackRef, val preferred: String, val key: String = "", @SerialName("given_at") val givenAt: String)

@Serializable
data class ListEditItem(@SerialName("client_id") val clientId: String, val key: String, val add: List<Long> = emptyList(), val remove: List<Long> = emptyList())

@Serializable
data class ListSyncItem(@SerialName("client_id") val clientId: String, val key: String, val on: Boolean, val pinned: Boolean = false)

@Serializable
data class SyncRequest(val feedback: List<FeedbackItem> = emptyList(), val queue: List<QueueItem> = emptyList(), val ratings: List<MixRatingItem> = emptyList(), val comparisons: List<ComparisonItem> = emptyList(), @SerialName("list_edits") val listEdits: List<ListEditItem> = emptyList(), @SerialName("list_syncs") val listSyncs: List<ListSyncItem> = emptyList())

@Serializable
data class Accepted(@SerialName("client_id") val clientId: String, @SerialName("track_id") val trackId: Long)

@Serializable
data class Rejected(@SerialName("client_id") val clientId: String, val reason: String)

@Serializable
data class SyncResponse(val accepted: List<Accepted>, val rejected: List<Rejected>)

/** The four verdicts and their API names. */
enum class Verdict(val api: String) {
    Keep("yes"), No("no"), NotNow("not_now"), Never("never");

    companion object {
        fun fromApi(name: String?) = entries.firstOrNull { it.api == name }
    }
}

// Lists, asking and radio

@Serializable
data class ItemSound(val bpm: Float, val camelot: String? = null, val energy: Float, val instrumental: Float = 0f, val moods: List<Float> = emptyList(), val shape: List<Float>? = null)

/** One entry in a list: a track, or for discovery lists a release, artist, album or set. See server/recommend/lists.py. */
@Serializable
data class ListItem(
    val kind: String,
    @SerialName("track_id") val trackId: Long? = null,
    @SerialName("release_id") val releaseId: Long? = null,
    @SerialName("artist_id") val artistId: Long? = null,
    @SerialName("set_id") val setId: Long? = null,
    val title: String,
    val artist: String = "",
    val album: String = "",
    val detail: String = "",
    val reason: String = "",
    val novel: Boolean = false,
    val score: Double? = null,
    @SerialName("tidal_id") val tidalId: String? = null,
    @SerialName("context_id") val contextId: Long? = null,
    val styles: List<String> = emptyList(),
    val sound: ItemSound? = null,
    val hue: Float? = null,
    @SerialName("hue_near") val hueNear: Boolean = false,  // hue is the context it sounds closest to, not one it's in
    val verdict: String? = null,
    val state: String? = null,
    val url: String? = null,
    // Discover's evidence, by kind (server/recommend/lists.py and beyond.py):
    val source: String = "",  // a release: artist, remixer or label
    val why: String = "",  // and that in words: "artist you play", "on Ninja Tune"
    val released: String = "",  // and its release date, "2026-09-18"
    val hues: List<Float> = emptyList(),  // and a colour per analysed track: the context each sounds closest to
    val others: List<ListItem> = emptyList(),  // a release: the same artist's other waiting releases, folded under it
    @SerialName("others_count") val othersCount: Int = 0,
    @SerialName("close_to") val closeTo: List<String> = emptyList(),  // an artist to try: yours they're closest to
    @SerialName("close_to_more") val closeToMore: Int = 0,
    val years: Map<String, Int> = emptyMap(),  // an artist to come back to: plays per year
    val span: String = "",  // and the years they were biggest
    val plays: Int? = null,
    val recent: Int? = null,
    val minutes: Int? = null,  // a set: its length, tracklist entries found, how many of those you like, and its context
    val identified: Int? = null,
    val yours: Int? = null,
    val context: String = "",
    val then: Then? = null,  // a faded favourite: when it peaked, and when you last played it
    // the library's (server/api/library.py): when you played it, as well as how much
    val age: String = "",  // lately, year (earlier this year), older or never
    @SerialName("last_played") val lastPlayed: String? = null,
    val peak: String? = null,  // an older track's biggest month, "2016-03"
    @SerialName("near_context_id") val nearContextId: Long? = null,
    val key: String = "",  // an album's tracks: "F min"
    val distance: Float? = null,  // a context's tracks: from its centre
) {
    val isTrack get() = kind == "track" && trackId != null
    val ref: TrackRef get() = TrackRef(trackId, tidalId, artist, title, album)
}

/** A faded favourite's story: its plays in all and at its peak, and the months of the peak and the last play ("2016-03"). */
@Serializable
data class Then(val plays: Int = 0, val peak: Int = 0, @SerialName("peak_at") val peakAt: String = "", @SerialName("last_at") val lastAt: String = "")

/** The release queue's size by why each release is in it (artist, remixer, label), and how much has been decided. */
@Serializable
data class QueueSummary(val waiting: Map<String, Int> = emptyMap(), val decided: Int = 0, @SerialName("decided_this_week") val decidedThisWeek: Int = 0) {
    val total get() = waiting.values.sum()
}

@Serializable
data class ListContext(
    val id: Long,
    val name: String,
    val hue: Float,
    val family: String = "",
    // how recent it is (lists only): for the Lists page's sections and Home's "Fits now"
    val active: Boolean = true,
    @SerialName("recent_plays") val recentPlays: Int = 0,  // in the server's RECENT window (45 days)
    @SerialName("lifetime_plays") val lifetimePlays: Int = 0,
    @SerialName("last_played") val lastPlayed: String? = null,
    @SerialName("era_start") val eraStart: String? = null,  // "2018-05-13"
    @SerialName("era_end") val eraEnd: String? = null,
    val hours: List<Float> = emptyList(),  // share of its plays in each local hour, 24 values
    val weekdays: List<Float> = emptyList(),  // Monday first
)

@Serializable
data class MixList(
    val key: String,
    val kind: String,
    val section: String,
    val title: String,
    val subtitle: String = "",
    @SerialName("built_at") val builtAt: String,
    val context: ListContext? = null,
    val playlist: PlaylistDto? = null,
    val sync: Boolean = false,
    val pinned: Boolean = false,
    val items: List<ListItem> = emptyList(),
) {
    val tracks get() = items.filter { it.isTrack }
    /** Kept in Tidal as its own playlist. */
    val synced get() = sync
    val novelShare get() = if (tracks.isEmpty()) 0f else tracks.count { it.novel }.toFloat() / tracks.size
}

@Serializable
data class ListsResponse(val lists: List<MixList>)

@Serializable
data class QuickAction(
    val label: String,
    val detail: String,  // why, with the numbers behind it
    val recipe: kotlinx.serialization.json.JsonObject,
    @SerialName("list_key") val listKey: String? = null,
    val kind: String = "",  // seed, now, interest, explore, faded, fresh, era
    val hue: Float? = null,  // a context's colour, and its share of plays in each local hour
    val hours: List<Float> = emptyList(),
)

@Serializable
data class MakeRequest(
    val prompt: String = "",
    val recipe: kotlinx.serialization.json.JsonObject? = null,
    val length: Int? = null,
    /** 0: familiar rediscovery; 1: discovery, with a deliberate anchor mix between. */
    val exploration: Double? = null,
)

@Serializable
data class DraftResponse(val mix: MixList? = null, val understood: String = "")

@Serializable
data class KeepRequest(val title: String? = null, val remove: List<Long> = emptyList())

@Serializable
data class SyncRequest2(val on: Boolean, val pinned: Boolean = false)

@Serializable
data class Synced(val sync: Boolean, val pinned: Boolean = false, @SerialName("tidal_url") val tidalUrl: String? = null)

@Serializable
data class EditRequest(val add: List<Long> = emptyList(), val remove: List<Long> = emptyList())

@Serializable
data class ExploreRequest(val artist: String? = null, @SerialName("artist_id") val artistId: Long? = null, @SerialName("track_id") val trackId: Long? = null)

@Serializable
data class Played(@SerialName("tidal_url") val tidalUrl: String? = null)

@Serializable
data class RadioRequest(@SerialName("track_id") val trackId: Long? = null, val track: TrackRef? = null)

@Serializable
data class RadioResponse(val mix: MixList, @SerialName("tidal_url") val tidalUrl: String? = null, val added: Int = 0)

@Serializable
data class QueueItem(@SerialName("client_id") val clientId: String, @SerialName("release_id") val releaseId: Long, val state: String, @SerialName("given_at") val givenAt: String)

// Catalogue

@Serializable
data class SearchRequest(val q: String, val sound: Boolean = false)

@Serializable
data class ArtistHit(val id: Long, val name: String, val tracks: Int)

@Serializable
data class SearchResult(val tracks: List<ListItem> = emptyList(), val artists: List<ArtistHit> = emptyList(), val contexts: List<ListContext> = emptyList(), val understood: String = "")

// Library (server/api/library.py)

@Serializable
data class TrackPage(val total: Int, val offset: Int, val items: List<ListItem>, val recency: Map<String, Int>? = null)

/** A language other than English you play: MusicBrainz's code ("fra"), its name, and tracks played and in all. */
@Serializable
data class LanguageCount(val code: String, val name: String, val played: Int = 0, val tracks: Int = 0)

/** A cell of a colour icon: a context's hue, hollow when the track only sounds like it. */
@Serializable
data class HueDto(val hue: Float, val near: Boolean = false)

@Serializable
data class Shelf(val kind: String, val id: Long, val title: String, val detail: String, val hues: List<HueDto> = emptyList())

@Serializable
data class LibrarySummary(
    val tracks: Int = 0,
    val recency: Map<String, Int> = emptyMap(),  // lately, year, older, never
    val artists: Int = 0, val albums: Int = 0, val sets: Map<String, Int> = emptyMap(), val contexts: Int = 0, val families: Int = 0,
    val languages: List<LanguageCount> = emptyList(),
    val recent: List<Shelf> = emptyList(),
)

@Serializable
data class ArtistRow(
    val id: Long, val name: String, val tracks: Int, val plays: Double, val listens: Int = 0,
    @SerialName("this_year") val thisYear: Int = 0, val years: Map<String, Int> = emptyMap(),
)

@Serializable
data class AlbumRow(
    val id: Long, val name: String, val artist: String, @SerialName("artist_id") val artistId: Long,
    val tracks: Int, val plays: Double, val played: Int = 0, val listens: Int = 0, val recent: Int = 0,
    @SerialName("last_played") val lastPlayed: String? = null, val hues: List<HueDto> = emptyList(),
    @SerialName("tidal_url") val tidalUrl: String? = null,
)

@Serializable
data class SetRow(
    val id: Long, val title: String, val by: String, val source: String, val minutes: Int? = null,
    val published: String? = null, @SerialName("saved_at") val savedAt: String? = null, val entries: Int = 0,
    @SerialName("in_catalogue") val inCatalogue: Int = 0, val yours: Int = 0, val plays: Int = 0,
    @SerialName("last_played") val lastPlayed: String? = null, val strip: List<Int> = emptyList(), val hits: Int = 0, val url: String,
)

@Serializable
data class ContextCount(val id: Long, val name: String, val hue: Float, val tracks: Int = 0)

@Serializable
data class ArtistPage(
    val artist: ArtistRow, @SerialName("tidal_url") val tidalUrl: String? = null, val catalogue: Int = 0, val never: Int = 0, val waiting: Int = 0,
    val contexts: List<ContextCount> = emptyList(), @SerialName("context_count") val contextCount: Int = 0,
    val albums: List<AlbumRow> = emptyList(), val tracks: List<ListItem> = emptyList(), val unplayed: List<ListItem> = emptyList(),
)

@Serializable
data class MostPlayed(val title: String, val plays: Int)

@Serializable
data class AlbumPage(val album: AlbumRow, @SerialName("most_played") val mostPlayed: MostPlayed? = null, val context: ContextCount? = null, val tracks: List<ListItem>)

@Serializable
data class SetEntry(val position: Int, val cue: String = "", val minute: Float? = null, val artist: String, val title: String, val label: String = "", val track: ListItem? = null)

@Serializable
data class SetPage(val set: SetRow, val context: ContextCount? = null, val entries: List<SetEntry>)

@Serializable
data class ContextPage(
    val id: Long, val name: String, val family: String = "", val hue: Float, val active: Boolean = true, val size: Int = 0,
    val recent: Int = 0, val lifetime: Int = 0, @SerialName("last_played") val lastPlayed: String? = null, val rank: Int? = null,
    @SerialName("era_start") val eraStart: String? = null, @SerialName("era_end") val eraEnd: String? = null,
    val hours: List<Float> = emptyList(), val styles: List<String> = emptyList(), @SerialName("list_key") val listKey: String? = null,
    val synced: Boolean = false, val members: List<Long> = emptyList(), val far: Float = 1f, val tracks: List<ListItem> = emptyList(),
)

@Serializable
data class StoryContext(val id: Long, val name: String, val hue: Float, val near: Boolean = false, val recent: Int = 0)

@Serializable
data class ListRef(val key: String, val title: String, val kind: String = "", val subtitle: String = "")

@Serializable
data class TrackStory(
    val track: ListItem, @SerialName("artist_id") val artistId: Long, @SerialName("album_id") val albumId: Long? = null,
    val key: String = "", val danceability: Float? = null, val released: String = "", @SerialName("first_played") val firstPlayed: String? = null,
    val months: Map<String, Int> = emptyMap(), val years: Map<String, Int> = emptyMap(), val context: StoryContext? = null,
    val lists: List<ListRef> = emptyList(), @SerialName("tidal_url") val tidalUrl: String? = null,
)

@Serializable
data class BestMatch(
    val kind: String, val id: Long, val name: String, val tracks: Int = 0, val listens: Int = 0, val recent: Int = 0,
    val years: Map<String, Int> = emptyMap(), val months: List<Int> = emptyList(),
)

@Serializable
data class Found<T>(val total: Int = 0, val items: List<T> = emptyList())

@Serializable
data class WaitingRelease(val id: Long, val title: String, val artist: String = "")

@Serializable
data class LibrarySearch(
    val q: String = "", val best: BestMatch? = null,
    val tracks: Found<ListItem> = Found(), val artists: Found<ArtistRow> = Found(), val albums: Found<AlbumRow> = Found(),
    val lists: Found<ListRef> = Found(), val sets: Found<SetRow> = Found(), val discover: Found<WaitingRelease> = Found(),
)

// Tracklists you provide (server/api/sets.py)

@Serializable
data class TracklistRequest(
    val text: String = "", val url: String = "", val title: String = "", val dj: String = "",
    val html: String = "",  // the url's page as the app's browser showed it, when the server was turned away
    @SerialName("set_id") val setId: Long? = null, val wait: Boolean = false,
)

/** One line of a provided tracklist and how it matched: matched, added, original, fixed, no_tidal, pending, unmatched or id. */
@Serializable
data class TracklistLine(
    val position: Int, val cue: String = "", val artist: String = "", val title: String = "", val label: String = "",
    val match: String, val track: ListItem? = null,
)

@Serializable
data class TracklistSummary(val known: Int, val matched: Int, val playable: Int, val rate: Float, val pending: Int = 0, val counts: Map<String, Int> = emptyMap())

@Serializable
data class Tracklist(
    val set: SetRow, @SerialName("list_key") val listKey: String? = null, val via: String = "",
    val summary: TracklistSummary, val lines: List<TracklistLine>,
)

@Serializable
data class RenameRequest(val title: String)

@Serializable
data class FixLineRequest(@SerialName("track_id") val trackId: Long? = null, val id: Boolean = false, val clear: Boolean = false)

// Map

@Serializable
data class MapPlace(
    val key: String, val name: String, val hue: Float? = null, val x: Float, val y: Float, val r: Float, val kind: String, val size: Int, val members: List<Long> = emptyList(),
    val family: String = "", val recent: Int = 0, @SerialName("era_start") val eraStart: String? = null, @SerialName("era_end") val eraEnd: String? = null,  // contexts
)

@Serializable
data class MapPointMeta(val country: String = "", val language: String = "", val era: Int? = null, val energy: Float? = null)

/**
 * Every placed track as [id, x, y, hue or -1, flags, near hue or -1, family or -1]; flags 1 favourite, 2 played,
 * 4 Fresh/Rediscovery, 8 in a list, 16 placed by sound alone, 32 played lately, 64 earlier this year. Near hue: a
 * track in no context, the one it sounds closest to; family: an index into [families].
 */
@Serializable
data class MapData(
    val version: String, val scale: Int, val points: List<List<Float>>,
    val families: List<MapPlace> = emptyList(), val contexts: List<MapPlace>, val artists: List<MapPlace> = emptyList(), val lists: List<MapPlace>,
    val metadata: Map<String, MapPointMeta> = emptyMap(),
)

@Serializable
data class MapVersion(val version: String)
