package com.georgewaller.audiobrain.player

import android.app.SearchManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaMetadata
import android.media.browse.MediaBrowser
import android.media.session.PlaybackState
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.service.media.MediaBrowserService
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/**
 * Phase 0: what this device and its players actually allow. Each part answers one question in the plan's routes
 * table; the report is plain text, to copy and paste back.
 */
object DeviceCheck {

    fun report(context: Context): String = buildString {
        appendLine("# Device check · ${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
        appendLine("Google Play services: ${installed(context, "com.google.android.gms")?.let { "yes ($it)" } ?: "no"}")
        appendLine("Notification access: ${if (NowPlayingListener.hasAccess(context)) "granted" else "NOT granted"}; listener ${if (SessionTracker.connected.value) "bound" else "not bound"}")
        appendLine()
        appendLine("## Players installed")
        Players.known.forEach { appendLine("- $it: ${installed(context, it) ?: "not installed"}") }
        appendLine()
        appendLine("## Media sessions")
        val sessions = SessionTracker.sessions
        if (sessions.isEmpty()) appendLine("(none: start something playing, then run the check again)")
        sessions.forEach { c ->
            appendLine("### ${c.packageName}${if (c == SessionTracker.current) "  ← followed" else ""}")
            val state = c.playbackState
            appendLine("state: ${stateName(state?.state)}, position ${state?.position}ms, rating type ${c.ratingType}")
            appendLine("actions: ${actions(state?.actions ?: 0).joinToString()}")
            state?.customActions?.takeIf { it.isNotEmpty() }?.let { list ->
                appendLine("custom actions: ${list.joinToString { "${it.action} (\"${it.name}\")" }}")
            }
            c.metadata?.let { m -> appendLine("metadata: ${metadata(m)}") }
            c.extras?.keySet()?.takeIf { it.isNotEmpty() }?.let { appendLine("session extras: ${it.joinToString()}") }
            appendLine("queue: ${c.queue?.size ?: "none"}${c.queueTitle?.let { " (\"$it\")" } ?: ""}")
            c.queue?.take(3)?.forEach { item -> appendLine("  - id ${item.queueId}: ${item.description.title} · mediaId ${item.description.mediaId}") }
        }
        appendLine()
        appendLine("## Who opens what")
        mapOf(
            "https://tidal.com/browse/track/1566" to Intent(Intent.ACTION_VIEW, Uri.parse("https://tidal.com/browse/track/1566")),
            "https://tidal.com/browse/playlist/<uuid>" to Intent(Intent.ACTION_VIEW, Uri.parse("https://tidal.com/browse/playlist/a8aad350-b03c-4103-9cf3-0c919787368f")),
            "https://listen.tidal.com/track/1566" to Intent(Intent.ACTION_VIEW, Uri.parse("https://listen.tidal.com/track/1566")),
            "tidal://track/1566" to Intent(Intent.ACTION_VIEW, Uri.parse("tidal://track/1566")),
            "MEDIA_PLAY_FROM_SEARCH (any)" to Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH),
            "MEDIA_PLAY_FROM_SEARCH → Tidal" to Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).setPackage(Players.TIDAL),
            "MEDIA_PLAY_FROM_SEARCH → UAPP" to Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).setPackage(Players.UAPP),
        ).forEach { (label, intent) -> appendLine("- $label: ${handlers(context, intent)}") }
        val browsers = context.packageManager.queryIntentServices(Intent(MediaBrowserService.SERVICE_INTERFACE), 0)
        appendLine("- media browser services: ${browsers.joinToString { "${it.serviceInfo.packageName}/${it.serviceInfo.name}" }.ifEmpty { "none visible" }}")
    }

    /** Try connecting to each player's media browser (the Android Auto interface): approved apps only, usually. */
    suspend fun browsers(context: Context): String = buildString {
        appendLine("## Media browser connections")
        val services = context.packageManager.queryIntentServices(Intent(MediaBrowserService.SERVICE_INTERFACE), 0)
            .filter { it.serviceInfo.packageName in Players.known }
        if (services.isEmpty()) appendLine("(no known player offers one)")
        for (info in services) {
            val component = ComponentName(info.serviceInfo.packageName, info.serviceInfo.name)
            appendLine("- ${component.flattenToShortString()}: ${connect(context, component)}")
        }
    }

    private suspend fun connect(context: Context, component: ComponentName): String = withTimeoutOrNull(6000) {
        suspendCancellableCoroutine { done ->
            var browser: MediaBrowser? = null
            browser = MediaBrowser(context, component, object : MediaBrowser.ConnectionCallback() {
                override fun onConnected() {
                    val b = browser!!
                    val root = b.root
                    b.subscribe(root, object : MediaBrowser.SubscriptionCallback() {
                        override fun onChildrenLoaded(parentId: String, children: MutableList<MediaBrowser.MediaItem>) {
                            b.unsubscribe(root)
                            b.disconnect()
                            if (done.isActive) done.resume("connected, root \"$root\", ${children.size} children: ${children.take(6).joinToString { "${it.description.title} [${it.mediaId}]" }}")
                        }
                        override fun onError(parentId: String) {
                            b.disconnect()
                            if (done.isActive) done.resume("connected, root \"$root\", but listing it failed")
                        }
                    })
                }
                override fun onConnectionFailed() { if (done.isActive) done.resume("refused (not on its list of allowed apps)") }
                override fun onConnectionSuspended() { if (done.isActive) done.resume("connected then dropped") }
            }, null)
            browser.connect()
            done.invokeOnCancellation { browser.disconnect() }
        }
    } ?: "no answer in 6 s"

    /** Ask Tidal to search and play: if it honours this, "play this exact track" can be one tap. */
    fun playFromSearch(context: Context, packageName: String, artist: String, title: String): String {
        val intent = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
            .setPackage(packageName)
            .putExtra(SearchManager.QUERY, "$artist $title")
            .putExtra(MediaStore.EXTRA_MEDIA_FOCUS, MediaStore.Audio.Media.ENTRY_CONTENT_TYPE)
            .putExtra(MediaStore.EXTRA_MEDIA_ARTIST, artist)
            .putExtra(MediaStore.EXTRA_MEDIA_TITLE, title)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return runCatching { context.startActivity(intent); "sent to $packageName: watch whether it starts \"$title\"" }
            .getOrElse { "$packageName didn't take it: ${it.javaClass.simpleName}" }
    }

    private fun installed(context: Context, packageName: String): String? = runCatching {
        context.packageManager.getPackageInfo(packageName, 0).versionName ?: "installed"
    }.getOrNull()

    private fun handlers(context: Context, intent: Intent): String =
        context.packageManager.queryIntentActivities(intent, PackageManager.MATCH_DEFAULT_ONLY)
            .joinToString { "${it.activityInfo.packageName}/${it.activityInfo.name.substringAfterLast('.')}" }
            .ifEmpty { "nobody" }

    private fun metadata(m: MediaMetadata): String = m.keySet().sorted().joinToString { key ->
        val value = when {
            m.getBitmap(key) != null -> m.getBitmap(key)!!.let { "bitmap ${it.width}×${it.height}" }
            m.getString(key) != null -> "\"${m.getString(key)}\""
            m.getRating(key) != null -> "rating ${m.getRating(key)}"
            else -> m.getLong(key).toString()
        }
        "${key.removePrefix("android.media.metadata.")}=$value"
    }

    private fun stateName(state: Int?) = when (state) {
        PlaybackState.STATE_PLAYING -> "playing"
        PlaybackState.STATE_PAUSED -> "paused"
        PlaybackState.STATE_STOPPED -> "stopped"
        PlaybackState.STATE_BUFFERING -> "buffering"
        PlaybackState.STATE_NONE -> "none"
        null -> "unknown"
        else -> "state $state"
    }

    private fun actions(bits: Long): List<String> = listOf(
        PlaybackState.ACTION_PLAY to "play", PlaybackState.ACTION_PAUSE to "pause", PlaybackState.ACTION_SKIP_TO_NEXT to "next",
        PlaybackState.ACTION_SKIP_TO_PREVIOUS to "previous", PlaybackState.ACTION_SEEK_TO to "seek",
        PlaybackState.ACTION_PLAY_FROM_SEARCH to "PLAY_FROM_SEARCH", PlaybackState.ACTION_PLAY_FROM_MEDIA_ID to "PLAY_FROM_MEDIA_ID",
        PlaybackState.ACTION_PLAY_FROM_URI to "PLAY_FROM_URI", PlaybackState.ACTION_PREPARE_FROM_MEDIA_ID to "PREPARE_FROM_MEDIA_ID",
        PlaybackState.ACTION_SKIP_TO_QUEUE_ITEM to "SKIP_TO_QUEUE_ITEM", PlaybackState.ACTION_SET_RATING to "SET_RATING",
    ).filter { (bit, _) -> bits and bit != 0L }.map { it.second }
}

/** Opening things in the players. */
object Launch {
    /** Open a Tidal link in the Tidal app if it's installed, else wherever Android sends it. */
    fun tidal(context: Context, url: String): Boolean {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val inTidal = Intent(intent).setPackage(Players.TIDAL)
        return runCatching { context.startActivity(inTidal); true }.recoverCatching { context.startActivity(intent); true }.getOrDefault(false)
    }
}
