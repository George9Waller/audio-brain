package com.georgewaller.audiobrain.overlays

import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.georgewaller.audiobrain.SetNow
import com.georgewaller.audiobrain.shortName
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbIconButton
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.player.NowPlaying
import com.georgewaller.audiobrain.player.SessionTracker
import kotlinx.coroutines.delay

/** Previous, play or pause, next, for whichever player is playing. [size] is the play button's. */
@Composable
fun Transport(playing: Boolean, modifier: Modifier = Modifier, size: Dp = 64.dp) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(Ab.space.l, Alignment.CenterHorizontally), verticalAlignment = Alignment.CenterVertically) {
        AbIconButton(AbIcons.Previous, "Previous", { SessionTracker.previous() }, tint = Ab.colors.textMuted)
        AbIconButton(if (playing) AbIcons.Pause else AbIcons.Play, if (playing) "Pause" else "Play", { SessionTracker.playPause() }, background = Ab.colors.raised, size = size)
        AbIconButton(AbIcons.Next, "Next", { SessionTracker.next() }, tint = Ab.colors.textMuted)
    }
}

/** A thin line for how far through the track (or set) playback is, with the times under it. */
@Composable
fun PlaybackProgress(np: NowPlaying, modifier: Modifier = Modifier) {
    val duration = np.durationMs ?: return
    val position by produceState(np.positionMs, np) {
        while (true) {
            value = np.positionMs + if (np.playing) SystemClock.elapsedRealtime() - np.positionAt else 0
            if (!np.playing) break
            delay(1000)
        }
    }
    val fraction = (position.toFloat() / duration).coerceIn(0f, 1f)
    val line = Ab.colors.line
    val done = Ab.colors.textMuted
    Column(modifier) {
        Canvas(Modifier.fillMaxWidth().height(3.dp)) {
            val y = size.height / 2
            drawLine(line, Offset(0f, y), Offset(size.width, y), strokeWidth = size.height, cap = StrokeCap.Round)
            drawLine(done, Offset(0f, y), Offset(size.width * fraction, y), strokeWidth = size.height, cap = StrokeCap.Round)
        }
        Spacer(Modifier.height(Ab.space.xs))
        Row {
            AbText(clock(position), style = Ab.type.number, color = Ab.colors.textFaint)
            Spacer(Modifier.weight(1f))
            AbText(clock(duration), style = Ab.type.number, color = Ab.colors.textFaint)
        }
    }
}

/**
 * Where in a set playback is: "Track 7 of 24", the set's name and what's next. The track itself is the screen's
 * title (see NowSet); tap here for its verdicts, or [onTracklist] for the whole list. An estimated position (a
 * tracklist without times) says "About".
 */
@Composable
fun SetLine(now: SetNow, onTrack: (ListItem) -> Unit, onTracklist: () -> Unit, modifier: Modifier = Modifier) {
    val line = now.line
    val total = now.tracklist.lines.size
    Column(modifier.fillMaxWidth().clickable { line.track?.let(onTrack) ?: onTracklist() }.padding(vertical = Ab.space.xs)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Overline("${if (now.estimated) "About track" else "Track"} ${now.index + 1} of $total", color = Ab.colors.textMuted)
            Spacer(Modifier.weight(1f))
            AbText("Tracklist ›", style = Ab.type.number, color = Ab.colors.textFaint, modifier = Modifier.clickable(onClick = onTracklist).padding(start = Ab.space.s))
        }
        AbText("In ${shortName(now.tracklist.set.title)}", style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
        now.next?.let { next ->
            AbText("Next: " + listOf(next.artist, next.title).filter { it.isNotBlank() }.joinToString(" – ").ifBlank { "ID" }, style = Ab.type.caption, color = Ab.colors.textFaint, maxLines = 1)
        }
    }
}

private fun clock(ms: Long): String {
    val s = ms / 1000
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s % 3600 / 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
}
