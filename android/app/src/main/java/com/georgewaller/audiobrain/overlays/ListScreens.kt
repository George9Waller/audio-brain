package com.georgewaller.audiobrain.overlays

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ItemRow
import com.georgewaller.audiobrain.LinkRow
import com.georgewaller.audiobrain.ReleaseRow
import com.georgewaller.audiobrain.camelotCompatible
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbTextField
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.Dot
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.design.SyncMark
import com.georgewaller.audiobrain.design.SectionHeader
import com.georgewaller.audiobrain.design.TrackRow
import com.georgewaller.audiobrain.sigil
import com.georgewaller.audiobrain.shortReason
import com.georgewaller.audiobrain.style
import com.georgewaller.audiobrain.design.AbIconButton
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.contextColor
import com.georgewaller.audiobrain.kindLabel
import com.georgewaller.audiobrain.meta
import com.georgewaller.audiobrain.openUrl
import androidx.compose.ui.platform.LocalContext

/** One list: what it's made of (energy across it, its key path), play and sync, then every track with swipes. */
@Composable
fun ListDetail(vm: AppViewModel, key: String, padding: PaddingValues, onMenu: (ListItem, String) -> Unit, onDeleted: () -> Unit = {}) {
    val state by vm.lists.collectAsStateWithLifecycle()
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val decided by vm.queueStates.collectAsStateWithLifecycle()
    val list = state.lists.firstOrNull { it.key == key }
    if (list == null) {
        EmptyState("This list has gone", "It may have been rebuilt; go back and pick it again.")
        return
    }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        val canSync = list.section == "lists" && list.tracks.isNotEmpty()
        val mine = list.kind in USER_MADE
        item(key = "head") { ListHeader(
            list, onSync = if (canSync) { on -> vm.setSync(list, on) } else null, onPin = if (canSync) { on -> vm.setPinned(list, on) } else null,
            onRename = if (mine) { title -> vm.renameList(list, title) } else null,
            onDelete = if (mine) { { vm.deleteList(list); onDeleted() } } else null,
        ) {
            val context = LocalContext.current
            Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                if (list.tracks.isNotEmpty()) AbButton("Play now", { vm.play(list.key) })
                val url = list.playlist?.tidalUrl
                if (list.sync && url != null) AbButton("Open in Tidal", { openUrl(context, url) }, kind = ButtonKind.Ghost, icon = AbIcons.Open)
                if (list.kind in setOf("radio", "explore", "interest")) AbButton("Add 20 more", { vm.extendRadio(list.key) }, kind = ButtonKind.Ghost)
            }
            if (list.tracks.isNotEmpty()) {
                Column(verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
                    AbText("How was this list?", style = Ab.type.caption, color = Ab.colors.textMuted)
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
                        (1..5).forEach { rating -> AbButton("$rating", { vm.rateMix(list.key, rating) }, kind = ButtonKind.Ghost) }
                    }
                    if (list.tracks.size >= 2) {
                        val left = list.tracks[0]; val right = list.tracks[1]
                        AbText("Which fits better?", style = Ab.type.caption, color = Ab.colors.textMuted)
                        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
                            AbButton(left.title, { vm.compareInMix(list.key, left, right, "left") }, kind = ButtonKind.Quiet)
                            AbButton(right.title, { vm.compareInMix(list.key, left, right, "right") }, kind = ButtonKind.Quiet)
                        }
                    }
                }
            }
        } }
        // in a playlist, swiping left takes the track out of the list (it isn't a No: long-press for that)
        val editable = list.section == "lists"
        itemsIndexed(list.items, key = { i, it -> "$i-${it.kind}-${it.trackId ?: it.releaseId ?: it.title}" }) { _, item ->
            val context = LocalContext.current
            when {
                item.isTrack -> ItemRow(
                    item, verdicts[item.ref.key], { v -> vm.give(v, item, "list:${list.key}") }, { onMenu(item, "list:${list.key}") },
                    onSwipeLeft = if (editable) ({ vm.editList(list, remove = item) }) else null, leftLabel = if (editable) "Remove" else "No",
                )
                item.kind == "release" -> ReleaseRow(item, decided[item.releaseId], { s -> vm.decide(item, s) }, { openUrl(context, item.url) })
                else -> LinkRow(item)
            }
        }
        if (editable && list.tracks.isNotEmpty()) {
            item(key = "suggest-h") { SectionHeader("Suggested for this list") }
            item(key = "suggest") { Suggestions(vm, list, onMenu) }
        }
    }
}

/** Lists you made, which you can rename and delete; the rest are rebuilt by the server. */
private val USER_MADE = setOf("asked", "radio", "explore", "set")

/** Tracks that would fit the list, fetched when you reach the bottom; swipe right or tap Add to put one in. */
@Composable
private fun Suggestions(vm: AppViewModel, list: MixList, onMenu: (ListItem, String) -> Unit) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    var items by remember(list.key) { mutableStateOf<List<ListItem>?>(null) }
    var error by remember(list.key) { mutableStateOf<String?>(null) }
    LaunchedEffect(list.key) { try { items = vm.suggestionsFor(list.key) } catch (e: ApiException) { error = e.message } }
    val present = list.items.mapNotNull { it.trackId }.toSet()
    Column {
        val shown = items?.filter { it.trackId !in present }
        when {
            shown == null -> AbText(error ?: "Finding tracks that fit…", style = Ab.type.caption, color = Ab.colors.textFaint)
            shown.isEmpty() -> AbText("Nothing more to suggest right now", style = Ab.type.caption, color = Ab.colors.textFaint)
            else -> shown.forEach { s ->
                val (sigil, faint) = s.sigil()
                TrackRow(
                    s.title, listOf(s.artist, s.shortReason()).filter { it.isNotBlank() }.joinToString(" · "), sigil = sigil, sigilFaint = faint,
                    verdict = verdicts[s.ref.key]?.style(), onClick = { onMenu(s, "suggested:${list.key}") }, onLongClick = { onMenu(s, "suggested:${list.key}") },
                    onSwipeRight = { vm.editList(list, add = s) }, rightLabel = "Add",
                    trailing = { AbButton("Add", { vm.editList(list, add = s) }, kind = ButtonKind.Ghost) },
                )
            }
        }
    }
}

/** A list's title block: kind, colour, counts, the energy arc and key path, and [actions]. Shared by lists and drafts. */
@Composable
fun ListHeader(
    list: MixList, onSync: ((Boolean) -> Unit)? = null, onPin: ((Boolean) -> Unit)? = null,
    onRename: ((String) -> Unit)? = null, onDelete: (() -> Unit)? = null, actions: @Composable () -> Unit,
) {
    var editing by remember(list.key) { mutableStateOf<String?>(null) }
    var confirming by remember(list.key) { mutableStateOf(false) }
    Column(Modifier.padding(top = Ab.space.s, bottom = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            list.context?.let { Dot(contextColor(it.hue), size = 8.dp); Spacer(Modifier.width(Ab.space.s)) }
            Overline("${list.kindLabel()} · ${list.meta()}", color = Ab.colors.textMuted)
            Spacer(Modifier.weight(1f))
            // sync with Tidal: its own playlist there, kept up to date, to download
            if (onSync != null) AbIconButton(
                AbIcons.Sync, if (list.sync) "Stop syncing with Tidal" else "Sync to Tidal", { onSync(!list.sync) },
                tint = if (list.sync) Ab.colors.keep else Ab.colors.textFaint, size = 40.dp,
            )
            if (onPin != null && list.sync) AbButton(if (list.pinned) "Pinned" else "Pin", { onPin(!list.pinned) }, kind = ButtonKind.Ghost)
            if (onRename != null) AbIconButton(AbIcons.Edit, "Rename", { editing = list.title }, tint = Ab.colors.textFaint, size = 40.dp)
            if (onDelete != null) AbIconButton(AbIcons.Delete, "Delete this list", { confirming = true }, tint = Ab.colors.textFaint, size = 40.dp)
        }
        if (confirming && onDelete != null) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbText("Delete this list?", style = Ab.type.caption, color = Ab.colors.textMuted, modifier = Modifier.weight(1f))
            AbButton("Delete", { confirming = false; onDelete() })
            AbButton("Cancel", { confirming = false }, kind = ButtonKind.Ghost)
        }
        val draftTitle = editing
        if (draftTitle != null && onRename != null) {
            AbTextField(draftTitle, { editing = it }, label = "", placeholder = "Name this list")
            Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                AbButton("Save", { onRename(draftTitle.trim()); editing = null }, enabled = draftTitle.isNotBlank())
                AbButton("Cancel", { editing = null }, kind = ButtonKind.Ghost)
            }
        } else AbText(list.title, style = Ab.type.title, maxLines = 3)
        if (list.subtitle.isNotBlank()) AbText(list.subtitle, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 3)
        val energies = list.tracks.mapNotNull { it.sound?.energy }
        if (energies.size > 2) EnergyArc(energies)
        val keys = list.tracks.mapNotNull { it.sound?.camelot }
        if (keys.size > 2) KeyPath(keys)
        actions()
    }
}

/** Energy across the list in play order, as a line over a faint fill. */
@Composable
fun EnergyArc(energies: List<Float>) {
    val line = Ab.colors.text
    val base = Ab.colors.line
    Canvas(Modifier.fillMaxWidth().height(44.dp)) {
        val n = energies.size
        val pts = energies.mapIndexed { i, e -> Offset(i * size.width / (n - 1), size.height - 3.dp.toPx() - e.coerceIn(0f, 1f) * (size.height - 8.dp.toPx())) }
        drawLine(base, Offset(0f, size.height - 1f), Offset(size.width, size.height - 1f))
        val path = Path().apply { moveTo(pts[0].x, pts[0].y); pts.drop(1).forEach { lineTo(it.x, it.y) } }
        val fill = Path().apply { addPath(path); lineTo(size.width, size.height); lineTo(0f, size.height); close() }
        drawPath(fill, line.copy(alpha = 0.07f))
        drawPath(path, line, style = Stroke(1.4.dp.toPx()))
    }
}

/** The key path in Camelot notation, clashes between neighbours in the No colour. */
@Composable
fun KeyPath(keys: List<String>) {
    val faint = Ab.colors.textFaint
    val clash = Ab.colors.no
    val text = buildAnnotatedString {
        keys.forEachIndexed { i, k ->
            if (i > 0) withStyle(SpanStyle(color = faint)) { append(" → ") }
            withStyle(SpanStyle(color = if (i > 0 && !camelotCompatible(keys[i - 1], k)) clash else faint)) { append(k) }
        }
    }
    BasicText(text, Modifier.horizontalScroll(rememberScrollState()), style = Ab.type.number, softWrap = false)
}

/** A context's tracks, core to edge, or an artist's: fetched when opened. */
@Composable
fun TracksScreen(vm: AppViewModel, title: String, loader: suspend () -> List<ListItem>, padding: PaddingValues, onMenu: (ListItem, String) -> Unit) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    var items by remember { mutableStateOf<List<ListItem>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(title) { try { items = loader() } catch (e: ApiException) { error = e.message } }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "t") {
            Column(Modifier.padding(top = Ab.space.s, bottom = Ab.space.m)) {
                AbText(title, style = Ab.type.title, maxLines = 2)
                AbText("From the tracks that define it to the ones at its edge", style = Ab.type.caption, color = Ab.colors.textMuted)
            }
        }
        val list = items
        if (list == null) item(key = "l") { EmptyState(if (error != null) "Couldn't load it" else "Loading", error ?: "") }
        else items(list, key = { "t-${it.trackId}" }) { item -> ItemRow(item, verdicts[item.ref.key], { v -> vm.give(v, item, "catalogue") }, { onMenu(item, "catalogue") }) }
    }
}
