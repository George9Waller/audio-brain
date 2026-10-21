package com.georgewaller.audiobrain

import android.os.SystemClock
import com.georgewaller.audiobrain.data.Tracklist
import com.georgewaller.audiobrain.data.TracklistLine
import com.georgewaller.audiobrain.data.Settings
import com.georgewaller.audiobrain.player.NowPlaying

/**
 * Which line of a set's tracklist is playing. From the cue times when the tracklist has them ("1:02:30"); else
 * spread evenly over the set's length, and marked [estimated].
 */
data class SetNow(val tracklist: Tracklist, val index: Int, val estimated: Boolean) {
    val line: TracklistLine get() = tracklist.lines[index]
    val next: TracklistLine? get() = tracklist.lines.getOrNull(index + 1)

    companion object {
        fun at(tracklist: Tracklist, np: NowPlaying): SetNow? {
            val lines = tracklist.lines
            if (lines.isEmpty()) return null
            val elapsed = if (np.playing) SystemClock.elapsedRealtime() - np.positionAt else 0
            val seconds = (np.positionMs + elapsed) / 1000
            val cues = lines.map { cueSeconds(it.cue) }
            if (cues.count { it != null } >= lines.size / 2) {
                val index = cues.indexOfLast { it != null && it <= seconds }.coerceAtLeast(0)
                return SetNow(tracklist, index, estimated = false)
            }
            val length = np.durationMs?.div(1000) ?: tracklist.set.minutes?.times(60L) ?: return SetNow(tracklist, 0, estimated = true)
            val index = (seconds * lines.size / length.coerceAtLeast(1)).toInt().coerceIn(0, lines.lastIndex)
            return SetNow(tracklist, index, estimated = true)
        }

        private val CUE = Regex("""^\[?(?:(\d{1,2}):)?(\d{1,3}):(\d{2})]?$""")

        /** "1:02:30", "62:30" or "[04:10]" in seconds; null for track numbers like "043" or "0??". */
        fun cueSeconds(cue: String): Long? {
            val m = CUE.find(cue.trim()) ?: return null
            val (h, min, s) = m.destructured
            return (h.toLongOrNull() ?: 0) * 3600 + min.toLong() * 60 + s.toLong()
        }
    }
}

fun playerName(packageName: String) = if (packageName == Settings.YT_MUSIC) "YouTube Music" else "Tidal"
