package com.georgewaller.audiobrain.sets

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.Tracklist
import com.georgewaller.audiobrain.data.TracklistLine
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbChip
import com.georgewaller.audiobrain.design.AbDrawer
import com.georgewaller.audiobrain.design.AbSurface
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbTextField
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.Dot
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.design.SectionHeader
import com.georgewaller.audiobrain.design.Spinner
import com.georgewaller.audiobrain.design.TrackRow
import com.georgewaller.audiobrain.facts
import com.georgewaller.audiobrain.sigil
import kotlin.math.roundToInt

/**
 * Paste a tracklist (or share one to the app, or give a link) and see how each line matched: tracks in the
 * catalogue, ones just found on Tidal, originals standing in for a DJ's own edits, IDs, and lines nothing matched.
 * Tapping a line opens the fix-match picker. The set's "From this set" list opens like any other list.
 */
@Composable
fun TracklistScreen(vm: TracklistViewModel, padding: PaddingValues, onOpenList: (String) -> Unit, onPlay: (String) -> Unit) {
    val s by vm.state.collectAsStateWithLifecycle()
    val result = s.result
    Box(Modifier.fillMaxSize()) {
        if (result == null) Entry(vm, s, padding) else Result(vm, result, padding, onOpenList, onPlay)
        s.browse?.let { PageBrowser(it, padding, vm::readPage, vm::closeBrowser) }
        AbDrawer(s.fixing != null, vm::closeFix) {
            val line = result?.lines?.firstOrNull { it.position == s.fixing }
            if (line != null) FixPicker(vm, line, s.candidates, s.searching)
        }
    }
}

@Composable
private fun Entry(vm: TracklistViewModel, s: TracklistViewModel.State, padding: PaddingValues) {
    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "form") {
            Column(Modifier.padding(top = Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                AbText(if (s.attachTo != null) "This set's tracklist" else "Paste a tracklist", style = Ab.type.display)
                AbText(
                    "Paste the tracks, one a line, or a link: 1001Tracklists, MixesDB, YouTube or SoundCloud. Times, numbers, labels and IDs are fine.",
                    style = Ab.type.caption, color = Ab.colors.textMuted,
                )
                Overline("Tracklist or link")
                AbSurface(Modifier.fillMaxWidth(), color = Ab.colors.raised, shape = Ab.shapes.small) {
                    Box(Modifier.padding(Ab.space.m).heightIn(min = 160.dp, max = 360.dp)) {
                        if (s.text.isEmpty()) AbText("01  Artist - Title [Label]\n02  05:15  Artist - Title\n…", color = Ab.colors.textFaint, style = Ab.type.code)
                        BasicTextField(
                            s.text, vm::setText, Modifier.fillMaxWidth(),
                            textStyle = Ab.type.code.copy(color = Ab.colors.text), cursorBrush = SolidColor(Ab.colors.accent),
                            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false),
                        )
                    }
                }
                if (s.attachTo == null) {
                    AbTextField(s.title, vm::setTitle, "Title (optional)", placeholder = "Read from the tracklist when it has one")
                    AbTextField(s.dj, vm::setDj, "DJ (optional)")
                }
                s.error?.let { AbText(it, style = Ab.type.caption, color = Ab.colors.no) }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                    AbButton(if (s.reading) "Reading…" else "Read tracklist", vm::read, enabled = s.text.isNotBlank() && !s.reading)
                    if (s.reading) Spinner(size = 16.dp)
                }
            }
        }
    }
}

@Composable
private fun Result(vm: TracklistViewModel, t: Tracklist, padding: PaddingValues, onOpenList: (String) -> Unit, onPlay: (String) -> Unit) {
    val sum = t.summary
    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "head") {
            Column(Modifier.padding(top = Ab.space.s, bottom = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                Overline(listOfNotNull("Set", t.set.by.takeIf { it.isNotBlank() }, t.set.published).joinToString(" · "), color = Ab.colors.textMuted)
                AbText(t.set.title, style = Ab.type.title, maxLines = 3)
                AbText(
                    "${sum.matched} of ${sum.known} tracks found (${(sum.rate * 100).roundToInt()}%) · ${sum.playable} playable" +
                        (sum.counts["id"]?.let { " · $it IDs" } ?: ""),
                    style = Ab.type.label, color = Ab.colors.textMuted,
                )
                if (sum.pending > 0) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Spinner(size = 12.dp)
                    AbText("Looking up ${sum.pending} on Tidal…", style = Ab.type.caption, color = Ab.colors.textFaint)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    t.listKey?.let { key ->
                        AbButton("Play", { onPlay(key) })
                        AbButton("Open list", { onOpenList(key) }, kind = ButtonKind.Quiet)
                    }
                    AbButton("Another", vm::again, kind = ButtonKind.Ghost)
                }
                AbText("Tap a line to fix its match.", style = Ab.type.caption, color = Ab.colors.textFaint)
            }
        }
        items(t.lines, key = { "l-${it.position}" }) { line -> LineRow(line) { vm.openFix(line.position) } }
    }
}

/** What each match means, in a word, and its colour. */
@Composable
private fun matchLabel(match: String): Pair<String, Color> = when (match) {
    "matched" -> "found" to Ab.colors.keep
    "added" -> "new · from Tidal" to Ab.colors.keep
    "fixed" -> "fixed" to Ab.colors.keep
    "original" -> "original" to Ab.colors.notNow
    "no_tidal" -> "not on Tidal" to Ab.colors.notNow
    "pending" -> "looking" to Ab.colors.textFaint
    "id" -> "ID" to Ab.colors.textFaint
    else -> "not found" to Ab.colors.no
}

@Composable
private fun LineRow(line: TracklistLine, onFix: () -> Unit) {
    val (label, colour) = matchLabel(line.match)
    val track = line.track
    Row(Modifier.fillMaxWidth().clickable(enabled = line.match != "pending", onClick = onFix), verticalAlignment = Alignment.CenterVertically) {
        AbText(line.cue.ifBlank { "${line.position}" }, style = Ab.type.number, color = Ab.colors.textFaint, modifier = Modifier.width(48.dp))
        Column(Modifier.weight(1f).padding(vertical = Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
            AbText(listOf(line.artist, line.title).filter { it.isNotBlank() }.joinToString(" – "), style = Ab.type.body, maxLines = 2)
            val detail = when {
                track != null && (line.match == "original" || "${track.artist} – ${track.title}" != "${line.artist} – ${line.title}") -> "→ ${track.artist} – ${track.title}"
                line.label.isNotBlank() -> line.label
                else -> ""
            }
            if (detail.isNotBlank()) AbText(detail, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
        }
        Row(Modifier.padding(start = Ab.space.s), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
            Dot(colour, live = line.match == "pending")
            AbText(label, style = Ab.type.number, color = colour, maxLines = 1)
        }
    }
}

@Composable
private fun ColumnScope.FixPicker(vm: TracklistViewModel, line: TracklistLine, candidates: List<ListItem>, searching: Boolean) {
    var q by remember(line.position) { mutableStateOf("") }
    Column(Modifier.padding(horizontal = Ab.space.gutter, vertical = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
        Overline("Line ${line.position}${line.cue.takeIf { it.isNotBlank() }?.let { " · $it" } ?: ""}")
        AbText(listOf(line.artist, line.title).filter { it.isNotBlank() }.joinToString(" – "), style = Ab.type.heading, maxLines = 2)
        AbTextField(q, { q = it; vm.findCandidates(it) }, "Search the catalogue", placeholder = "Artist or title")
        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbChip("It's an ID") { vm.fix(isId = true) }
            AbChip("None of these") { vm.fix(clear = true) }
        }
        SectionHeader(if (searching) "Searching…" else if (candidates.isEmpty()) "Nothing in the catalogue" else "Is it one of these?")
    }
    LazyColumn(Modifier.fillMaxWidth().heightIn(max = 380.dp), contentPadding = PaddingValues(horizontal = Ab.space.gutter, vertical = Ab.space.s)) {
        items(candidates, key = { "c-${it.trackId}" }) { c ->
            val (sigil, faint) = c.sigil()
            TrackRow(c.title, listOf(c.artist, c.album).filter { it.isNotBlank() }.joinToString(" · ") + if (c.tidalId == null) " · not on Tidal" else "",
                sigil = sigil, sigilFaint = faint, facts = c.facts(), onClick = { vm.fix(track = c) })
        }
        if (candidates.isEmpty() && !searching) item(key = "none") { EmptyState("No match yet", "Search by another spelling, or mark it as an ID.") }
    }
}
