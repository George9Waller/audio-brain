package com.georgewaller.audiobrain.pages

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.CardRow
import com.georgewaller.audiobrain.CloseToRow
import com.georgewaller.audiobrain.DecidedLine
import com.georgewaller.audiobrain.DiscoverSection
import com.georgewaller.audiobrain.ItemRow
import com.georgewaller.audiobrain.ReleaseCard
import com.georgewaller.audiobrain.SetCard
import com.georgewaller.audiobrain.Slot
import com.georgewaller.audiobrain.ThenAndNowRow
import com.georgewaller.audiobrain.ThenLine
import com.georgewaller.audiobrain.count
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.openUrl
import com.georgewaller.audiobrain.refill

/**
 * Discover: one page of things you haven't played, or haven't for a long time, in sections named for why each is
 * here, each showing its evidence, each in its own card. The release queue is split by why (artists you play,
 * remixers you like, labels you play), one row per artist; deciding one brings the next up, and its "All" opens the
 * inbox to work through it. Fresh and New to you live in Lists.
 */
@Composable
fun DiscoverPage(vm: AppViewModel, padding: PaddingValues, onOpenList: (String) -> Unit, onMenu: (ListItem, String) -> Unit) {
    val state by vm.lists.collectAsStateWithLifecycle()
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val decided by vm.queueStates.collectAsStateWithLifecycle()
    val queue by vm.queue.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var expanded by remember { mutableStateOf(setOf<Long>()) }
    // releases decided on this visit, which stay as lines you can undo while the next ones move up
    var here by remember { mutableStateOf(setOf<Long>()) }
    fun decide(item: ListItem, to: String) {
        vm.decide(item, to)
        item.releaseId?.let { here = here + it }
    }
    fun list(key: String) = state.lists.firstOrNull { it.key == key }
    val new = Ab.colors.ageNew

    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "title") {
            Column(Modifier.padding(top = Ab.space.s)) {
                AbText("Discover", style = Ab.type.display)
                AbText("Things you haven't played yet, or haven't for a long time", style = Ab.type.caption, color = Ab.colors.textMuted)
                if (queue.total > 0) Row(Modifier.clickable { onOpenList("releases") }.heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
                    AbText("${count(queue.total)} new releases to look at →", style = Ab.type.label, color = Ab.colors.ageNew)
                }
            }
        }
        if (state.lists.none { it.section == "discover" }) item(key = "empty") { EmptyState("Nothing here yet", state.error ?: "The Mixes job builds these each morning.") }

        fun releases(source: String, title: String, caption: String, shown: Int) {
            val l = list("releases:$source")?.takeIf { it.items.isNotEmpty() } ?: return
            item(key = "s-$source") {
                val waiting = queue.waiting[source] ?: l.items.size
                val slots = refill(l.items, decided, here, shown)
                if (slots.isEmpty()) return@item
                DiscoverSection("New · ${count(waiting)} waiting", new, title, caption, listOf("All ${count(waiting)}" to { onOpenList(l.key) })) {
                    slots.forEach { slot ->
                        val r = slot.item
                        CardRow {
                            when (slot) {
                                is Slot.Decided -> DecidedLine(r, slot.state) { decide(r, "new") }
                                // the section says why, except which label
                                is Slot.Open -> ReleaseCard(r, decided, ::decide, { openUrl(context, it.url) }, r.releaseId in expanded, why = source == "label", fresh = slot.fresh) {
                                    expanded = r.releaseId?.let { id -> if (id in expanded) expanded - id else expanded + id } ?: expanded
                                }
                            }
                        }
                    }
                }
            }
        }
        releases("artist", "Artists you play", "Their new releases, closest to your taste first", 3)
        releases("remixer", "Remixers you like", "New work from people who remixed tracks you love", 2)
        releases("label", "Labels you play", "Releases on the labels behind your tracks", 2)

        list("artists")?.takeIf { it.items.isNotEmpty() }?.let { l ->
            item(key = "s-artists") {
                DiscoverSection("New · ${l.items.size} artists", new, "Close to artists you love", "Artists you haven't played, next to several you do", listOf("All ${l.items.size}" to { onOpenList(l.key) })) {
                    l.items.take(3).forEach { a -> CardRow { CloseToRow(a) { openUrl(context, a.url) } } }
                }
            }
        }

        val past = list("rediscovery")
        val revisit = list("revisit")
        if (past != null || revisit != null) item(key = "s-past") {
            val n = (past?.items?.size ?: 0)
            DiscoverSection("Era · $n tracks", Ab.colors.ageEra, "Back from your past", "Tracks and artists you loved and let go",
                listOfNotNull(past?.let { p -> "All $n tracks" to { onOpenList(p.key) } }, revisit?.takeIf { it.items.size > 2 }?.let { r -> "All ${r.items.size} artists to come back to" to { onOpenList(r.key) } })) {
                past?.tracks?.take(2)?.forEach { t ->
                    CardRow {
                        Column {
                            // the line under it says how much and when, so the row needn't
                            ItemRow(if (t.then != null) t.copy(reason = "") else t, verdicts[t.ref.key], { v -> vm.give(v, t, "discover:rediscovery") }, { onMenu(t, "discover:rediscovery") })
                            t.then?.let { ThenLine(it, Modifier.padding(bottom = Ab.space.s)) }
                        }
                    }
                }
                revisit?.items?.take(2)?.forEach { a -> CardRow { ThenAndNowRow(a) { openUrl(context, a.url) } } }
            }
        }

        list("sets")?.takeIf { it.items.isNotEmpty() }?.let { l ->
            item(key = "s-sets") {
                DiscoverSection("Habit · ${l.items.size} sets", Ab.colors.ageHabit, "Long listens", "Sets on channels you save from, by how much of the tracklist you play", listOf("All ${l.items.size}" to { onOpenList(l.key) })) {
                    l.items.take(3).forEach { s -> CardRow { SetCard(s) { openUrl(context, s.url) } } }
                }
            }
        }
        list("albums")?.takeIf { it.items.isNotEmpty() }?.let { l ->
            item(key = "s-albums") {
                DiscoverSection("${l.items.size} albums", Ab.colors.textMuted, "Albums to go back to", "Saved albums you haven't played for a year, and ones by artists you love", listOf("All ${l.items.size}" to { onOpenList(l.key) })) {
                    l.items.take(3).forEach { a ->
                        CardRow {
                            Column(Modifier.clickable { openUrl(context, a.url) }.heightIn(min = 52.dp).padding(vertical = Ab.space.s)) {
                                AbText(a.title, style = Ab.type.heading, maxLines = 1)
                                AbText(listOf(a.artist, a.reason).filter { it.isNotBlank() }.joinToString(" · "), style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 2)
                            }
                        }
                    }
                }
            }
        }
    }
}
