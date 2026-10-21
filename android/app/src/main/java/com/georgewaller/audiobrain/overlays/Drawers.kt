package com.georgewaller.audiobrain.overlays

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbIconButton
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.SoundSigil
import com.georgewaller.audiobrain.design.VerdictBar
import com.georgewaller.audiobrain.data.Settings
import com.georgewaller.audiobrain.facts
import com.georgewaller.audiobrain.playerName
import com.georgewaller.audiobrain.openUrl
import com.georgewaller.audiobrain.player.Launch
import com.georgewaller.audiobrain.shortName
import com.georgewaller.audiobrain.sigil
import com.georgewaller.audiobrain.sigilFor
import com.georgewaller.audiobrain.style
import com.georgewaller.audiobrain.verdict
import kotlin.math.roundToInt

/** The now-playing drawer: the track, all four verdicts, radio and more like this, playback. */
@Composable
fun NowDrawerContent(vm: AppViewModel, onMenu: (ListItem, String) -> Unit, onTracklist: (Long) -> Unit, onClose: () -> Unit) {
    val np by vm.nowPlaying.collectAsStateWithLifecycle()
    val setNow by vm.setNow.collectAsStateWithLifecycle()
    val within by vm.within.collectAsStateWithLifecycle()
    val track by vm.track.collectAsStateWithLifecycle()
    val verdict by vm.nowVerdict.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val playing = np?.takeIf { it.hasTrack }
    if (playing == null) {
        AbText("Nothing playing", style = Ab.type.heading)
        AbText("Start something in Tidal or UAPP.", style = Ab.type.caption, color = Ab.colors.textMuted)
        return
    }
    val setTrack = setNow?.line?.track
    val (sigil, faint) = remember(playing.ref.key, track, setTrack) { setTrack?.takeIf { it.sound != null }?.sigil() ?: sigilFor(playing, track) }
    Column(verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            SoundSigil(sigil, size = 56.dp, playing = playing.playing, faint = faint)
            Spacer(Modifier.width(Ab.space.m))
            Column(Modifier.weight(1f)) {
                AbText(within?.title ?: playing.title, style = Ab.type.heading, maxLines = 2)
                AbText("${within?.artist ?: playing.artist} · ${playing.appLabel}", style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
            }
        }
        val sound = track?.sound
        AbText(
            listOfNotNull(sound?.let { "${it.bpm.roundToInt()} BPM" }, sound?.camelot, track?.let { "${it.plays} plays" }, track?.context?.name?.let(::shortName) ?: if (track == null) "not in the catalogue yet" else "no context")
                .joinToString(" · "),
            style = Ab.type.number, color = Ab.colors.textMuted,
        )
        setNow?.let { now -> SetLine(now, { onClose(); onMenu(it, "set") }, { onClose(); onTracklist(now.tracklist.set.id) }) }
        VerdictBar(verdict?.style(), { vm.giveNow(it.verdict()) })
        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbButton("Start radio", { onClose(); vm.startRadio() })
            AbButton("More like this", { onClose(); vm.moreLikeNow() }, kind = ButtonKind.Quiet)
            AbButton("Explore", { onClose(); vm.explore(name = playing.artist.split(",").first().trim(), trackId = track?.id) }, kind = ButtonKind.Ghost)
        }
        PlaybackProgress(playing)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Transport(playing.playing, size = Ab.space.touch)
            Spacer(Modifier.weight(1f))
            track?.tidalUrl?.let { url -> AbButton("Open in Tidal", { Launch.tidal(context, url) }, kind = ButtonKind.Ghost, icon = AbIcons.Open) }
        }
    }
}

/** Long-press on a track: every verdict, radio from it, open it in Tidal. */
@Composable
fun ItemMenuContent(vm: AppViewModel, item: ListItem, where: String, onClose: () -> Unit) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val settings by vm.settings.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val (sigil, faint) = remember(item) { item.sigil() }
    val playOn = settings?.playOn ?: Settings.TIDAL
    val other = if (playOn == Settings.TIDAL) Settings.YT_MUSIC else Settings.TIDAL
    Column(verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            SoundSigil(sigil, size = 48.dp, faint = faint)
            Spacer(Modifier.width(Ab.space.m))
            Column(Modifier.weight(1f)) {
                AbText(item.title, style = Ab.type.heading, maxLines = 2)
                AbText(item.artist, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
            }
            item.facts()?.let { AbText(it, style = Ab.type.number, color = Ab.colors.textFaint) }
        }
        if (item.reason.isNotBlank()) AbText(item.reason, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 3)
        VerdictBar((verdicts[item.ref.key] ?: Verdict.fromApi(item.verdict))?.style(), { vm.give(it.verdict(), item, where); onClose() })
        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbButton("Play in ${playerName(playOn)}", { onClose(); vm.playTrack(item) }, icon = AbIcons.Play)
            AbButton(playerName(other), { onClose(); vm.playTrack(item, other) }, kind = ButtonKind.Ghost)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbButton("Start radio", { onClose(); vm.startRadio(item) }, kind = ButtonKind.Quiet)
            AbButton("Explore artist", { onClose(); vm.explore(name = item.artist.split(",").first().trim(), trackId = item.trackId) }, kind = ButtonKind.Quiet)
            item.tidalId?.let { id -> AbIconButton(AbIcons.Open, "Open in Tidal", { openUrl(context, "https://tidal.com/browse/track/$id") }, tint = Ab.colors.textMuted) }
        }
        Spacer(Modifier.height(Ab.space.xs))
    }
}
