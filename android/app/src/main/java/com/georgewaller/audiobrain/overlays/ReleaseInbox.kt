package com.georgewaller.audiobrain.overlays

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ReleaseCard
import com.georgewaller.audiobrain.count
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbChip
import com.georgewaller.audiobrain.design.AbDivider
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.openUrl

/** The queue's sources, in the order the inbox offers them, with the names it uses. */
private val SOURCES = listOf("" to "All", "artist" to "Artists you play", "remixer" to "Remixers you like", "label" to "Labels you play")

/**
 * The release queue as an inbox: how much is left and how much you've worked through, filters by why each release is
 * there, one row per artist with the rest folded in, and Save or Skip on every release (with Undo).
 */
@Composable
fun ReleaseInbox(vm: AppViewModel, key: String, padding: PaddingValues) {
    val state by vm.lists.collectAsStateWithLifecycle()
    val decided by vm.queueStates.collectAsStateWithLifecycle()
    val queue by vm.queue.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var source by rememberSaveable { mutableStateOf(key.substringAfter("releases:", "")) }
    var expanded by remember { mutableStateOf(setOf<Long>()) }
    val rows = state.lists.firstOrNull { it.key == if (source.isEmpty()) "releases" else "releases:$source" }?.items.orEmpty()
    // decisions made here that the server's counts don't include yet: the queue still lists them as new
    val everything = state.lists.filter { it.key.startsWith("releases") }.flatMap { l -> l.items.flatMap { listOf(it) + it.others } }.distinctBy { it.releaseId }
    val madeHere = everything.filter { it.state == "new" && decided[it.releaseId] in setOf("saved", "dismissed", "heard") }
    // a folded release has no source of its own: it shares its row's
    val sourceOf = state.lists.filter { it.key.startsWith("releases") }.flatMap { l -> l.items.flatMap { r -> (listOf(r) + r.others).map { it.releaseId to r.source } } }.toMap()
    fun waitingFrom(k: String) = ((if (k.isEmpty()) queue.total else queue.waiting[k] ?: 0) - madeHere.count { k.isEmpty() || sourceOf[it.releaseId] == k }).coerceAtLeast(0)
    val waiting = waitingFrom("")
    val done = queue.decided + madeHere.size

    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "head") {
            Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                AbText("New releases", style = Ab.type.display)
                AbText("${count(waiting)} to look at", style = Ab.type.number, color = Ab.colors.textMuted)
                val share = if (waiting + done > 0) done.toFloat() / (waiting + done) else 0f
                val fill = Ab.colors.ageNew
                val track = Ab.colors.line
                Canvas(Modifier.fillMaxWidth().height(4.dp).semantics { contentDescription = "$done decided of ${waiting + done}" }) {
                    drawRect(track)
                    drawRect(fill, size = size.copy(width = size.width * share))
                }
                AbText("${count(done)} decided so far · ${queue.decidedThisWeek} this week", style = Ab.type.caption, color = Ab.colors.textMuted)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    SOURCES.forEach { (k, label) ->
                        AbChip("$label ${count(waitingFrom(k))}", selected = source == k) { source = k }
                    }
                }
            }
        }
        if (rows.isEmpty()) item(key = "empty") { EmptyState("Nothing waiting", "New releases from artists, remixers and labels you play arrive here each day.") }
        items(rows, key = { "r-${it.releaseId}" }) { r ->
            ReleaseCard(r, decided, vm::decide, { openUrl(context, it.url) }, r.releaseId in expanded, why = source.isEmpty() || source == "label") {
                expanded = r.releaseId?.let { id -> if (id in expanded) expanded - id else expanded + id } ?: expanded
            }
            AbDivider()
        }
        if (rows.isNotEmpty() && rows.size < (if (source.isEmpty()) queue.total else queue.waiting[source] ?: 0)) item(key = "more") {
            AbText("Showing the best ${rows.size} artists; the rest come in as you decide these.", Modifier.padding(vertical = Ab.space.l), style = Ab.type.caption, color = Ab.colors.textMuted)
        }
    }
}
