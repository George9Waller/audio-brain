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
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.input.pointer.pointerInput
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.design.AbIconButton
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.VerdictBar
import com.georgewaller.audiobrain.overlays.PlaybackProgress
import com.georgewaller.audiobrain.overlays.SetLine
import com.georgewaller.audiobrain.overlays.Transport
import com.georgewaller.audiobrain.style
import com.georgewaller.audiobrain.verdict
import kotlinx.coroutines.delay
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AskBox
import com.georgewaller.audiobrain.design.SectionHeader
import com.georgewaller.audiobrain.design.SigilSpec
import com.georgewaller.audiobrain.design.SoundSigil
import com.georgewaller.audiobrain.shortName
import com.georgewaller.audiobrain.sigil
import com.georgewaller.audiobrain.sigilFor
import com.georgewaller.audiobrain.data.QuickAction
import com.georgewaller.audiobrain.leadTag
import com.georgewaller.audiobrain.design.AgeTagPill
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.platform.LocalConfiguration
import com.georgewaller.audiobrain.design.HourBars
import com.georgewaller.audiobrain.design.contextColor
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import java.time.LocalTime
import kotlin.math.roundToInt

/**
 * Home: the sigil of what's playing, a box to ask for a playlist and the leads for now (recommend.actions): the
 * contexts that fit this hour and day, then whatever has most behind it to explore. All of it fits on one screen.
 * Tap the sigil for focus mode. With nothing playing, the sigil shows the first lead's list, faintly.
 */
@Composable
fun HomePage(vm: AppViewModel, padding: PaddingValues, onAsk: () -> Unit, onFocus: () -> Unit, onMenu: (ListItem, String) -> Unit, onTracklist: (Long) -> Unit, onOpenList: (String) -> Unit) {
    val setNow by vm.setNow.collectAsStateWithLifecycle()
    val within by vm.within.collectAsStateWithLifecycle()
    val np by vm.nowPlaying.collectAsStateWithLifecycle()
    val track by vm.track.collectAsStateWithLifecycle()
    val actions by vm.actions.collectAsStateWithLifecycle()
    val lists by vm.lists.collectAsStateWithLifecycle()
    val listening by vm.listening.collectAsStateWithLifecycle()
    val playing = np?.takeIf { it.hasTrack }
    val hour = LocalTime.now().hour
    // the list behind the first lead, for the faint sigil when nothing's playing
    val topLists = listOfNotNull(actions.firstNotNullOfOrNull { a -> lists.lists.find { it.key == a.listKey } }) + lists.lists.filter { it.kind == "mix" }.take(1)
    val setTrack = setNow?.line?.track
    val (sigil, faint) = remember(playing?.ref?.key, track, setTrack, topLists.firstOrNull()?.key) {
        when {
            setTrack?.sound != null -> setTrack.sigil()  // within a set: the track playing in it
            playing != null -> sigilFor(playing, track)
            else -> topLists.firstOrNull()?.tracks?.firstOrNull()?.sigil()?.let { it.first to true } ?: (SigilSpec.fromSeed("home") to true)
        }
    }

    // sized so the leads fit on screen below it without scrolling
    val screenHeight = LocalConfiguration.current.screenHeightDp.dp
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(padding)) {
        BoxWithConstraints(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            val size = minOf(maxWidth * 0.62f, 280.dp, screenHeight * (if (playing != null) 0.3f else 0.22f))
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Box(Modifier.clickable(remember { MutableInteractionSource() }, null, onClick = onFocus)) {
                    SoundSigil(sigil, size = size, playing = playing?.playing == true, faint = faint)
                }
                Spacer(Modifier.height(Ab.space.l))
                if (playing != null) {
                    AbText(within?.title ?: playing.title, style = Ab.type.title, maxLines = 2)
                    AbText("${within?.artist ?: playing.artist} · ${playing.appLabel}", style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
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
        if (playing != null) {
            setNow?.let { now -> SetLine(now, { onMenu(it, "set") }, { onTracklist(now.tracklist.set.id) }, Modifier.padding(top = Ab.space.m)) }
            Transport(playing.playing, Modifier.fillMaxWidth().padding(top = Ab.space.s), size = 52.dp)
        }
        Spacer(Modifier.height(Ab.space.m))
        AskBox("What do you want to hear?", onClick = onAsk)
        if (actions.isNotEmpty()) {
            SectionHeader("For now")
            actions.take(5).forEach { a -> LeadRow(a, hour) { vm.makeFromAction(a) } }
        }
        Spacer(Modifier.height(Ab.space.l))
    }
}

/**
 * A lead on Home: what it is, why now (with the numbers), its tag and, for a context, the day's listening as a small
 * graph with this hour lit. Opens its list, or makes a draft from its recipe.
 */
@Composable
private fun LeadRow(action: QuickAction, hour: Int, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).heightIn(min = 52.dp).padding(vertical = Ab.space.xs), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                AbText(action.label, Modifier.weight(1f, fill = false), style = Ab.type.label, maxLines = 1)
                leadTag(action.kind)?.let { Spacer(Modifier.width(Ab.space.s)); AgeTagPill(it) }
            }
            AbText(action.detail, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
        }
        Spacer(Modifier.width(Ab.space.m))
        if (action.hours.size == 24) {
            HourBars(action.hours, contextColor(action.hue), hour, "Your plays of this by hour of the day", Modifier.width(56.dp).height(22.dp))
        } else {
            AbText("→", style = Ab.type.number, color = Ab.colors.textMuted)
        }
    }
}

/**
 * Focus mode: the sigil alone, pulsing on the beat, the screen kept on. A tap brings up the drawer's controls
 * (playback, verdicts, radio) for a few seconds; Back or the close button leaves.
 */
@Composable
fun FocusScreen(vm: AppViewModel, onExit: () -> Unit, onMenu: (ListItem, String) -> Unit, onTracklist: (Long) -> Unit) {
    val np by vm.nowPlaying.collectAsStateWithLifecycle()
    val track by vm.track.collectAsStateWithLifecycle()
    val verdict by vm.nowVerdict.collectAsStateWithLifecycle()
    val setNow by vm.setNow.collectAsStateWithLifecycle()
    val within by vm.within.collectAsStateWithLifecycle()
    val view = LocalView.current
    DisposableEffect(Unit) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
    var shown by remember { mutableStateOf(false) }
    var touched by remember { mutableIntStateOf(0) }
    LaunchedEffect(shown, touched) { if (shown) { delay(6_000); shown = false } }
    val playing = np?.takeIf { it.hasTrack }
    val setTrack = setNow?.line?.track
    val (sigil, faint) = remember(playing?.ref?.key, track, setTrack) {
        setTrack?.takeIf { it.sound != null }?.sigil() ?: playing?.let { sigilFor(it, track) } ?: (SigilSpec.fromSeed("focus") to true)
    }
    BoxWithConstraints(
        Modifier.fillMaxSize().background(Ab.colors.ground).statusBarsPadding().navigationBarsPadding()
            .clickable(remember { MutableInteractionSource() }, null) { shown = !shown; touched++ },
    ) {
        val size = (minOf(maxWidth, maxHeight) * if (shown) 0.5f else 0.78f)
        Column(Modifier.fillMaxSize().padding(horizontal = Ab.space.gutter), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
            SoundSigil(sigil, size = size, playing = playing?.playing == true, faint = faint)
            Spacer(Modifier.height(Ab.space.xl))
            AbText(within?.title ?: playing?.title ?: "Nothing playing", style = Ab.type.heading, color = Ab.colors.textMuted, maxLines = 1)
            AbText(within?.artist ?: playing?.artist.orEmpty(), style = Ab.type.caption, color = Ab.colors.textFaint, maxLines = 1)
            AnimatedVisibility(shown && playing != null, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
                val p = playing ?: return@AnimatedVisibility
                // any tap inside the controls keeps them up a little longer
                Column(Modifier.padding(top = Ab.space.l).pointerInput(Unit) { awaitEachGesture { awaitFirstDown(requireUnconsumed = false); touched++ } }, verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                    setNow?.let { now -> SetLine(now, { onMenu(it, "set") }, { onTracklist(now.tracklist.set.id) }) }
                    PlaybackProgress(p)
                    Transport(p.playing, Modifier.fillMaxWidth())
                    VerdictBar(verdict?.style(), { vm.giveNow(it.verdict()) })
                }
            }
        }
        AnimatedVisibility(shown, Modifier.align(Alignment.TopStart), enter = fadeIn(), exit = fadeOut()) {
            AbIconButton(AbIcons.Close, "Leave focus", onExit, tint = Ab.colors.textMuted)
        }
    }
}
