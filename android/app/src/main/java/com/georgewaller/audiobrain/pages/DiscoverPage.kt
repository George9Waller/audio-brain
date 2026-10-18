package com.georgewaller.audiobrain.pages

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ItemRow
import com.georgewaller.audiobrain.LinkRow
import com.georgewaller.audiobrain.ReleaseRow
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.SectionHeader
import com.georgewaller.audiobrain.design.SegmentTabs
import com.georgewaller.audiobrain.openUrl

private const val SHELF = 6

/**
 * Discover: things to work through, in three tabs so no page holds too much. New is Fresh and the release queue;
 * Rediscover is faded favourites and artists to come back to; Explore is artists to try, albums and sets.
 */
@Composable
fun DiscoverPage(vm: AppViewModel, padding: PaddingValues, onOpenList: (String) -> Unit, onMenu: (ListItem, String) -> Unit) {
    val state by vm.lists.collectAsStateWithLifecycle()
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val decided by vm.queueStates.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val context = LocalContext.current
    fun list(key: String) = state.lists.firstOrNull { it.key == key }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "title") {
            Column {
                AbText("Discover", style = Ab.type.display, modifier = Modifier.padding(top = Ab.space.s, bottom = Ab.space.m))
                SegmentTabs(listOf("New", "Rediscover", "Explore"), tab, { tab = it })
            }
        }
        if (state.lists.isEmpty()) item(key = "empty") { EmptyState("Nothing here yet", state.error ?: "The Mixes job builds these each morning.") }

        fun tracks(key: String, title: String) {
            val l = list(key) ?: return
            item(key = "h-$key") { Header(title, l, onOpenList) }
            itemsIndexed(l.tracks.take(SHELF), key = { i, it -> "$key-$i-${it.trackId}" }) { _, it ->
                ItemRow(it, verdicts[it.ref.key], { v -> vm.give(v, it, "discover:$key") }, { onMenu(it, "discover:$key") })
            }
        }
        fun links(key: String, title: String) {
            val l = list(key) ?: return
            item(key = "h-$key") { Header(title, l, onOpenList) }
            itemsIndexed(l.items.take(SHELF), key = { i, it -> "$key-$i" }) { _, it -> LinkRow(it) }
        }

        when (tab) {
            0 -> {
                tracks("fresh", "Fresh")
                tracks("new", "New to you")
                list("releases")?.let { l ->
                    item(key = "h-releases") { Header("New releases", l, onOpenList) }
                    items(l.items.filter { (decided[it.releaseId] ?: it.state) != "dismissed" }.take(SHELF), key = { "rel-${it.releaseId}" }) { r ->
                        ReleaseRow(r, decided[r.releaseId], { s -> vm.decide(r, s) }, { openUrl(context, r.url) })
                    }
                }
            }
            1 -> {
                tracks("rediscovery", "Rediscovery")
                links("revisit", "Artists to come back to")
            }
            else -> {
                links("artists", "Artists to try")
                links("albums", "Albums")
                links("sets", "Sets for you")
            }
        }
    }
}

@Composable
private fun Header(title: String, list: MixList, onOpenList: (String) -> Unit) {
    SectionHeader(title) {
        if (list.items.size > SHELF) {
            AbText("All ${list.items.size} →", style = Ab.type.number, color = Ab.colors.textMuted, modifier = Modifier.clickable { onOpenList(list.key) }.padding(start = Ab.space.s, top = Ab.space.xs, bottom = Ab.space.xs))
        }
    }
}
