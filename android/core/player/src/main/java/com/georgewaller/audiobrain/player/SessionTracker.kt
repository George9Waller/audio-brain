package com.georgewaller.audiobrain.player

import android.content.ComponentName
import android.content.Context
import android.graphics.Bitmap
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import com.georgewaller.audiobrain.data.TrackRef
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Packages the app knows by name. Any other player still works; it's just labelled by its app name. */
object Players {
    const val TIDAL = "com.aspiro.tidal"
    const val UAPP = "com.extreamsd.usbaudioplayerpro"
    const val FIIO_MUSIC = "com.fiio.music"
    val known = listOf(TIDAL, UAPP, FIIO_MUSIC, "com.google.android.apps.youtube.music", "com.spotify.music", "deezer.android.app", "com.maxmpz.audioplayer")
}

/** What a player says it's playing. */
data class NowPlaying(
    val packageName: String,
    val appLabel: String,
    val title: String,
    val artist: String,
    val album: String,
    val durationMs: Long?,
    val mediaId: String?,
    val playing: Boolean,
    val positionMs: Long,
    val positionAt: Long,  // elapsedRealtime when [positionMs] was reported
    val art: Bitmap?,
) {
    /** Tidal's track ID, when Tidal puts one in the media ID (checked on the device in phase 0). */
    val tidalId: String?
        get() = if (packageName == Players.TIDAL) mediaId?.let { TRAILING_ID.find(it)?.groupValues?.get(1) } else null

    val ref: TrackRef get() = TrackRef(null, tidalId, artist, title, album, durationMs, packageName)

    val hasTrack get() = title.isNotBlank()

    companion object {
        private val TRAILING_ID = Regex("""(?:^|\D)(\d{3,})$""")
    }
}

/**
 * Follows whichever app is playing, through Android's media sessions. Fed by [NowPlayingListener], which is what
 * Android requires for reading other apps' sessions: it only works once notification access is granted.
 */
object SessionTracker {
    private val _nowPlaying = MutableStateFlow<NowPlaying?>(null)
    val nowPlaying: StateFlow<NowPlaying?> = _nowPlaying.asStateFlow()

    private val _connected = MutableStateFlow(false)
    /** Whether the listener is bound, i.e. notification access is on and the system has started it. */
    val connected: StateFlow<Boolean> = _connected.asStateFlow()

    private val main = Handler(Looper.getMainLooper())
    private var manager: MediaSessionManager? = null
    private var component: ComponentName? = null
    private var appContext: Context? = null
    private var controller: MediaController? = null
    var sessions: List<MediaController> = emptyList()
        private set

    private val sessionsChanged = MediaSessionManager.OnActiveSessionsChangedListener { list -> onSessions(list.orEmpty()) }

    private val callback = object : MediaController.Callback() {
        override fun onMetadataChanged(metadata: MediaMetadata?) = publish()
        override fun onPlaybackStateChanged(state: PlaybackState?) {
            publish()
            // another session may have started playing while this one paused
            if (state?.state != PlaybackState.STATE_PLAYING) onSessions(sessions)
        }
        override fun onSessionDestroyed() = onSessions(sessions.filter { it != controller })
    }

    fun attach(context: Context, component: ComponentName) {
        val manager = context.getSystemService(MediaSessionManager::class.java)
        this.appContext = context.applicationContext
        this.manager = manager
        this.component = component
        manager.addOnActiveSessionsChangedListener(sessionsChanged, component, main)
        onSessions(manager.getActiveSessions(component))
        _connected.value = true
    }

    fun detach() {
        manager?.removeOnActiveSessionsChangedListener(sessionsChanged)
        controller?.unregisterCallback(callback)
        controller = null
        sessions = emptyList()
        _nowPlaying.value = null
        _connected.value = false
    }

    /** Re-read the sessions, e.g. after notification access was granted while the app was open. */
    fun refresh() {
        val manager = manager ?: return
        val component = component ?: return
        runCatching { onSessions(manager.getActiveSessions(component)) }
    }

    private fun onSessions(list: List<MediaController>) {
        val own = appContext?.packageName
        sessions = list.filter { it.packageName != own }
        val playing = sessions.firstOrNull { it.playbackState?.state == PlaybackState.STATE_PLAYING }
        val next = playing ?: sessions.firstOrNull { it.sessionToken == controller?.sessionToken } ?: sessions.firstOrNull()
        if (next?.sessionToken != controller?.sessionToken) {
            controller?.unregisterCallback(callback)
            controller = next
            next?.registerCallback(callback, main)
        }
        publish()
    }

    private fun publish() {
        val c = controller
        val metadata = c?.metadata
        if (c == null || metadata == null) {
            _nowPlaying.value = null
            return
        }
        val state = c.playbackState
        _nowPlaying.value = NowPlaying(
            packageName = c.packageName,
            appLabel = label(c.packageName),
            title = metadata.getString(MediaMetadata.METADATA_KEY_TITLE) ?: metadata.getString(MediaMetadata.METADATA_KEY_DISPLAY_TITLE).orEmpty(),
            artist = metadata.getString(MediaMetadata.METADATA_KEY_ARTIST) ?: metadata.getString(MediaMetadata.METADATA_KEY_ALBUM_ARTIST).orEmpty(),
            album = metadata.getString(MediaMetadata.METADATA_KEY_ALBUM).orEmpty(),
            durationMs = metadata.getLong(MediaMetadata.METADATA_KEY_DURATION).takeIf { it > 0 },
            mediaId = metadata.getString(MediaMetadata.METADATA_KEY_MEDIA_ID),
            playing = state?.state == PlaybackState.STATE_PLAYING,
            positionMs = state?.position ?: 0,
            positionAt = state?.lastPositionUpdateTime?.takeIf { it > 0 } ?: SystemClock.elapsedRealtime(),
            art = metadata.getBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART) ?: metadata.getBitmap(MediaMetadata.METADATA_KEY_ART),
        )
    }

    private val labels = mutableMapOf<String, String>()

    private fun label(packageName: String): String = labels.getOrPut(packageName) {
        when (packageName) {
            Players.TIDAL -> "Tidal"
            Players.UAPP -> "UAPP"
            Players.FIIO_MUSIC -> "FiiO Music"
            else -> appContext?.packageManager?.let { pm ->
                runCatching { pm.getApplicationLabel(pm.getApplicationInfo(packageName, 0)).toString() }.getOrNull()
            } ?: packageName
        }
    }

    fun playPause() {
        val c = controller ?: return
        if (c.playbackState?.state == PlaybackState.STATE_PLAYING) c.transportControls.pause() else c.transportControls.play()
    }

    fun next() = controller?.transportControls?.skipToNext()
    fun previous() = controller?.transportControls?.skipToPrevious()

    /** The live controller, for the device check. */
    val current: MediaController? get() = controller
}
