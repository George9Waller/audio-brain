package com.georgewaller.audiobrain

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.data.TrackDto
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AgeTag
import com.georgewaller.audiobrain.design.TrackHue
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.ListRow
import com.georgewaller.audiobrain.design.SigilSpec
import com.georgewaller.audiobrain.design.TrackRow
import com.georgewaller.audiobrain.design.VerdictStyle
import com.georgewaller.audiobrain.player.Launch
import com.georgewaller.audiobrain.player.NowPlaying
import kotlin.math.roundToInt

fun Verdict.style() = VerdictStyle.valueOf(name)
fun VerdictStyle.verdict() = Verdict.valueOf(name)

/** A list item's sigil: from its measured sound, else a faint stand-in from its names. Second is "faint". */
fun ListItem.sigil(): Pair<SigilSpec, Boolean> {
    val s = sound
    return if (s != null && s.moods.size == 6) SigilSpec(s.shape?.takeIf { it.size == 6 } ?: s.moods, s.energy, s.bpm, hue, s.camelot, s.instrumental < 0.5f, near = hueNear) to false
    else SigilSpec.fromSeed(ref.key, hue).copy(near = hueNear) to true
}

/** The sigil for what's playing: from the catalogue's measured sound when it has it. */
fun sigilFor(np: NowPlaying, track: TrackDto?): Pair<SigilSpec, Boolean> {
    val sound = track?.sound ?: return SigilSpec.fromSeed(np.ref.key, track?.context?.hue) to true
    val moods = sound.shape?.takeIf { it.size == 6 } ?: listOf(sound.happy, sound.party, sound.danceability, sound.aggressive, sound.sad, sound.relaxed)
    return SigilSpec(moods, sound.energy, sound.bpm, track.context?.hue, sound.camelot, sound.instrumental < 0.5f) to false
}

fun ListItem.facts(): String? = sound?.let { "${it.bpm.roundToInt()}\n${it.camelot ?: ""}".trim() }

fun ListItem.shortReason(): String = reason.removePrefix("New to you: ").removePrefix("New release: ")

/** "House: Dom Dolla (2019–2024)" -> "House: Dom Dolla". */
fun shortName(name: String) = name.substringBefore(" (")

fun MixList.meta(): String = buildString {
    append(tracks.size.takeIf { it > 0 } ?: items.size)
    if (tracks.isNotEmpty()) append(" · ${(novelShare * 100).roundToInt()}% new")
}

fun MixList.kindLabel(): String = when (kind) {
    "mix" -> "Context mix"
    "era" -> "Past era"
    "fresh" -> "New releases"
    "rediscovery" -> "Era"
    "new" -> "Never played"
    "edges" -> "Edges"
    "repeat" -> "On repeat"
    "year" -> "A year of your listening"
    "asked" -> "Asked"
    "radio" -> "Radio"
    "explore" -> "Explore"
    "interest" -> "New interest"
    "set" -> "From a set"
    "draft" -> "Draft"
    else -> title
}

/** A track in any list: swipe right Keep, left No (or what [onSwipeLeft] says), long-press for the rest. */
@Composable
fun ItemRow(
    item: ListItem,
    verdict: Verdict?,
    onVerdict: (Verdict) -> Unit,
    onMenu: () -> Unit,
    modifier: Modifier = Modifier,
    onSwipeLeft: (() -> Unit)? = null,
    leftLabel: String = "No",
) {
    val (sigil, faint) = item.sigil()
    val subtitle = listOf(item.artist, item.shortReason()).filter { it.isNotBlank() }.joinToString(" · ")
    TrackRow(
        item.title, subtitle, modifier, sigil = sigil, sigilFaint = faint, facts = item.facts(), verdict = (verdict ?: Verdict.fromApi(item.verdict))?.style(),
        onClick = onMenu, onLongClick = onMenu,
        onSwipeRight = { onVerdict(Verdict.Keep) }, onSwipeLeft = onSwipeLeft ?: { onVerdict(Verdict.No) }, leftLabel = leftLabel,
    )
}

/** A release in the discovery queue: Save and dismiss, shown done once decided. */
@Composable
fun ReleaseRow(item: ListItem, state: String?, onDecide: (String) -> Unit, onOpen: () -> Unit) {
    TrackRow(
        item.title, listOf(item.artist, item.detail.split(" · ").take(2).joinToString(" · ")).joinToString(" · "),
        onClick = onOpen, onSwipeRight = { onDecide("saved") }, onSwipeLeft = { onDecide("dismissed") }, rightLabel = "Save", leftLabel = "Dismiss",
        trailing = {
            when (state ?: item.state) {
                "saved" -> AbText("Saved", style = Ab.type.number, color = Ab.colors.keep)
                "dismissed" -> AbText("Dismissed", style = Ab.type.number, color = Ab.colors.textFaint)
                else -> Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
                    AbButton("Save", { onDecide("saved") }, kind = ButtonKind.Ghost)
                }
            }
        },
    )
}

/** An artist, album or set: opens where it lives (Tidal or YouTube). */
@Composable
fun LinkRow(item: ListItem) {
    val context = LocalContext.current
    TrackRow(item.title, listOf(item.artist, item.detail, item.reason).filter { it.isNotBlank() }.joinToString(" · "), onClick = { openUrl(context, item.url) })
}

@Composable
fun MixRow(list: MixList, onOpen: () -> Unit) = MixRow(list, list.meta(), null, onOpen)

/** A list's row with its own [meta] line and age [tag] (the Lists page's sections). */
@Composable
fun MixRow(list: MixList, meta: String, tag: AgeTag?, onOpen: () -> Unit) {
    val hues = list.tracks.map { t -> t.hue?.let { TrackHue(it, t.hueNear) } }.ifEmpty { list.items.map { null } }
    ListRow(shortName(list.title), meta, hues, synced = list.synced, tag = tag, onClick = onOpen)
}

fun openUrl(context: Context, url: String?): Boolean {
    if (url == null) return false
    if ("tidal.com" in url) return Launch.tidal(context, url)
    return runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true }.getOrDefault(false)
}

/** Whether two Camelot keys mix: same key, or a neighbour on the wheel in the same letter, or the relative key. */
fun camelotCompatible(a: String?, b: String?): Boolean {
    if (a == null || b == null) return true
    val na = a.dropLast(1).toIntOrNull() ?: return true
    val nb = b.dropLast(1).toIntOrNull() ?: return true
    val d = minOf((na - nb + 12) % 12, (nb - na + 12) % 12)
    return na == nb || (a.last() == b.last() && d <= 1)
}
