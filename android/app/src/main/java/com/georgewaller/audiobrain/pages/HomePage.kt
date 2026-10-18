package com.georgewaller.audiobrain.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.MixRow
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.ActionRow
import com.georgewaller.audiobrain.design.AskBox
import com.georgewaller.audiobrain.design.SectionHeader
import com.georgewaller.audiobrain.design.SigilSpec
import com.georgewaller.audiobrain.design.SoundSigil
import com.georgewaller.audiobrain.shortName
import com.georgewaller.audiobrain.sigil
import com.georgewaller.audiobrain.sigilFor
import kotlin.math.roundToInt

/**
 * Home: the sigil of what's playing, a box to ask for a playlist, three quick actions and the top lists. Tap the
 * sigil for focus mode. With nothing playing, the sigil shows the first track of your top list, faintly.
 */
@Composable
fun HomePage(vm: AppViewModel, padding: PaddingValues, onAsk: () -> Unit, onFocus: () -> Unit, onOpenList: (String) -> Unit) {
    val np by vm.nowPlaying.collectAsStateWithLifecycle()
    val track by vm.track.collectAsStateWithLifecycle()
    val actions by vm.actions.collectAsStateWithLifecycle()
    val lists by vm.lists.collectAsStateWithLifecycle()
    val listening by vm.listening.collectAsStateWithLifecycle()
    val playing = np?.takeIf { it.hasTrack }
    val topLists = lists.lists.filter { it.kind == "mix" }.take(3)
    val (sigil, faint) = remember(playing?.ref?.key, track, topLists.firstOrNull()?.key) {
        when {
            playing != null -> sigilFor(playing, track)
            else -> topLists.firstOrNull()?.tracks?.firstOrNull()?.sigil()?.let { it.first to true } ?: (SigilSpec.fromSeed("home") to true)
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(padding)) {
        BoxWithConstraints(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            val size = (maxWidth * 0.62f).coerceAtMost(280.dp)
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Box(Modifier.clickable(remember { MutableInteractionSource() }, null, onClick = onFocus)) {
                    SoundSigil(sigil, size = size, playing = playing?.playing == true, faint = faint)
                }
                Spacer(Modifier.height(Ab.space.l))
                if (playing != null) {
                    AbText(playing.title, style = Ab.type.title, maxLines = 2)
                    AbText("${playing.artist} · ${playing.appLabel}", style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
                    val sound = track?.sound
                    AbText(
                        listOfNotNull(
                            sound?.let { "${it.bpm.roundToInt()} BPM" }, sound?.camelot, sound?.let { "energy ${"%.2f".format(it.energy)}" },
                            track?.context?.name?.let { shortName(it) } ?: if (track == null) "not in the catalogue yet" else null,
                        ).joinToString(" · "),
                        style = Ab.type.number, color = Ab.colors.textFaint, maxLines = 1,
                    )
                } else {
                    AbText("Nothing playing", style = Ab.type.title)
                    AbText(
                        if (listening) "Start something in Tidal or UAPP, or pick a list below" else "Allow notification access in More › Settings to see what's playing",
                        style = Ab.type.caption, color = Ab.colors.textMuted,
                    )
                }
            }
        }
        Spacer(Modifier.height(Ab.space.xl))
        AskBox("What do you want to hear?", onClick = onAsk)
        if (actions.isNotEmpty()) {
            SectionHeader("For now")
            actions.take(4).forEach { a -> ActionRow(a.label, a.detail) { vm.makeFromAction(a) } }
        }
        if (topLists.isNotEmpty()) {
            SectionHeader("Top lists")
            topLists.forEach { l -> MixRow(l) { onOpenList(l.key) } }
        }
        Spacer(Modifier.height(Ab.space.xl))
    }
}

/** Focus mode: the sigil alone, pulsing on the beat, the screen kept on. Tap to leave. */
@Composable
fun FocusScreen(vm: AppViewModel, onExit: () -> Unit) {
    val np by vm.nowPlaying.collectAsStateWithLifecycle()
    val track by vm.track.collectAsStateWithLifecycle()
    val view = LocalView.current
    DisposableEffect(Unit) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
    val playing = np?.takeIf { it.hasTrack }
    val (sigil, faint) = remember(playing?.ref?.key, track) { playing?.let { sigilFor(it, track) } ?: (SigilSpec.fromSeed("focus") to true) }
    BoxWithConstraints(
        Modifier.fillMaxSize().background(Ab.colors.ground).clickable(remember { MutableInteractionSource() }, null, onClick = onExit),
        contentAlignment = Alignment.Center,
    ) {
        val size = (minOf(maxWidth, maxHeight) * 0.78f)
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            SoundSigil(sigil, size = size, playing = playing?.playing == true, faint = faint)
            Spacer(Modifier.height(Ab.space.xl))
            AbText(playing?.title ?: "Nothing playing", style = Ab.type.heading, color = Ab.colors.textMuted, maxLines = 1)
            AbText(playing?.artist.orEmpty(), style = Ab.type.caption, color = Ab.colors.textFaint, maxLines = 1)
        }
    }
}
