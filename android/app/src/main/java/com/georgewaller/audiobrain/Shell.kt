package com.georgewaller.audiobrain

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.width
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbDrawer
import com.georgewaller.audiobrain.design.AbIconButton
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.AbSurface
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.DesignCatalog
import com.georgewaller.audiobrain.design.NowChip
import com.georgewaller.audiobrain.design.PageLabels
import com.georgewaller.audiobrain.jobs.JobsScreen
import com.georgewaller.audiobrain.jobs.JobsViewModel
import com.georgewaller.audiobrain.overlays.AskScreen
import com.georgewaller.audiobrain.overlays.BuildingScreen
import com.georgewaller.audiobrain.overlays.DraftScreen
import com.georgewaller.audiobrain.overlays.ItemMenuContent
import com.georgewaller.audiobrain.overlays.ListDetail
import com.georgewaller.audiobrain.overlays.NowDrawerContent
import com.georgewaller.audiobrain.overlays.AlbumScreen
import com.georgewaller.audiobrain.overlays.ArtistScreen
import com.georgewaller.audiobrain.overlays.SetScreen
import com.georgewaller.audiobrain.pages.LibraryViewModel
import com.georgewaller.audiobrain.pages.MapPage
import com.georgewaller.audiobrain.design.AbChip
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.LocalConfiguration
import com.georgewaller.audiobrain.pages.CataloguePage
import com.georgewaller.audiobrain.pages.DiscoverPage
import com.georgewaller.audiobrain.pages.FocusScreen
import com.georgewaller.audiobrain.pages.HomePage
import com.georgewaller.audiobrain.pages.ListsPage
import com.georgewaller.audiobrain.pages.MorePage
import com.georgewaller.audiobrain.player.SessionTracker
import com.georgewaller.audiobrain.settings.DeviceCheckScreen
import com.georgewaller.audiobrain.settings.SettingsScreen
import com.georgewaller.audiobrain.sets.TracklistScreen
import com.georgewaller.audiobrain.sets.TracklistViewModel
import kotlinx.coroutines.launch

private val PAGES = listOf("Lists", "Home", "Discover", "Catalogue", "More")
private const val HOME = 1

/** Screens that slide over the pages; back pops them. */
sealed interface Overlay {
    data class List(val key: String) : Overlay
    data class Artist(val id: Long) : Overlay
    data class Album(val id: Long) : Overlay
    data class Set(val id: Long) : Overlay
    data class Ask(val prefill: String = "") : Overlay
    /** Give a tracklist: [prefill] from the share sheet, [attachTo] an existing set, [show] a provided set's matches. */
    data class Tracklist(val prefill: String = "", val attachTo: Long? = null, val show: Long? = null) : Overlay
    data object Focus : Overlay
    data object Jobs : Overlay
    data object Settings : Overlay
    data object DeviceCheck : Overlay
    data object Catalog : Overlay
}

/**
 * The frame: five pages you slide between with Home in the middle, the now-playing chip and its drawer, overlays
 * for lists and asking, and the Building and Draft screens while a mix is being made.
 */
@Composable
fun Shell(graph: DataGraph, openSettings: Boolean, onSettingsOpened: () -> Unit, shared: String? = null, onShareHandled: () -> Unit = {}, openList: String? = null, onListOpened: () -> Unit = {}) {
    val context = LocalContext.current
    val vm: AppViewModel = viewModel { AppViewModel(graph, context.applicationContext) }
    val jobs: JobsViewModel = viewModel { JobsViewModel(graph) }
    val library: LibraryViewModel = viewModel { LibraryViewModel(graph) }
    val tracklist: TracklistViewModel = viewModel { TracklistViewModel(graph) }
    val pager = rememberPagerState(initialPage = HOME) { PAGES.size }
    val scope = rememberCoroutineScope()
    val overlays = remember { mutableStateListOf<Overlay>() }
    var drawer by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf<Pair<ListItem, String>?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    val making by vm.making.collectAsStateWithLifecycle()
    val np by vm.nowPlaying.collectAsStateWithLifecycle()
    val track by vm.track.collectAsStateWithLifecycle()
    val pending by vm.pending.collectAsStateWithLifecycle()

    // back from the background: ask the server again if the last try failed (e.g. Tailscale was off)
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { vm.resumed(); library.retryIfFailed() }
    LaunchedEffect(openSettings) { if (openSettings) { overlays.add(Overlay.Settings); onSettingsOpened() } }
    val openTracklist: (Overlay.Tracklist) -> Unit = { o -> tracklist.start(o.prefill, o.attachTo, o.show); overlays.removeAll { it is Overlay.Tracklist }; overlays.add(o) }
    // text or a link shared from another app (a tracklist page, a video, a friend's message) opens as a tracklist
    LaunchedEffect(shared) { if (shared != null) { openTracklist(Overlay.Tracklist(prefill = shared)); onShareHandled() } }
    LaunchedEffect(openList) { if (openList != null) { overlays.removeAll { it is Overlay.List }; overlays.add(Overlay.List(openList)); onListOpened() } }
    LaunchedEffect(Unit) {
        vm.events.collect { e ->
            when (e) {
                is AppViewModel.Event.Message -> message = e.text
                is AppViewModel.Event.OpenList -> { overlays.removeAll { it is Overlay.Ask }; overlays.add(Overlay.List(e.key)) }
            }
        }
    }
    LaunchedEffect(message) { if (message != null) { kotlinx.coroutines.delay(2800); message = null } }
    BackHandler(drawer || menu != null || making !is AppViewModel.Making.Idle || overlays.isNotEmpty() || pager.currentPage != HOME) {
        when {
            drawer -> drawer = false
            menu != null -> menu = null
            making is AppViewModel.Making.Building -> vm.cancelMaking()
            making is AppViewModel.Making.Draft -> vm.discardDraft()
            overlays.isNotEmpty() -> overlays.removeAt(overlays.lastIndex)
            else -> scope.launch { pager.animateScrollToPage(HOME) }
        }
    }

    val playing = np?.takeIf { it.hasTrack }
    val onMenu: (ListItem, String) -> Unit = { item, where -> menu = item to where }
    val onSetTracklist: (Long) -> Unit = { id -> openTracklist(Overlay.Tracklist(show = id)) }
    // the map suits a phone; the DAPs' small screens open on the library, and either can switch
    val wide = LocalConfiguration.current.smallestScreenWidthDp >= 400
    var catalogueMode by rememberSaveable { mutableStateOf(if (wide) "map" else "library") }
    val pad = PaddingValues(start = Ab.space.gutter, end = Ab.space.gutter, bottom = 96.dp)

    Box(Modifier.fillMaxSize().background(Ab.colors.ground)) {
        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            // Pages change by swiping anywhere, along the names, or tapping one. A track row's own swipe (a verdict)
            // takes the gesture first, so the page only moves when the drag starts off a row; on the map a drag pans.
            PageLabels(
                PAGES, pager.currentPage + pager.currentPageOffsetFraction, alerts = if (pending > 0) setOf(4) else emptySet(),
                modifier = Modifier.padding(vertical = Ab.space.xs).pageSwipe { dir -> scope.launch { pager.animateScrollToPage((pager.currentPage + dir).coerceIn(0, PAGES.lastIndex)) } },
            ) { scope.launch { pager.animateScrollToPage(it) } }
            Box(Modifier.weight(1f)) {
            HorizontalPager(pager, Modifier.fillMaxSize().navigationBarsPadding(), beyondViewportPageCount = 1, userScrollEnabled = !(pager.currentPage == 3 && catalogueMode == "map")) { page ->
                when (page) {
                    0 -> ListsPage(vm, pad, { overlays.add(Overlay.List(it)) }, onMenu)
                    1 -> HomePage(vm, pad, onAsk = { overlays.add(Overlay.Ask()) }, onFocus = { overlays.add(Overlay.Focus) }, onMenu = onMenu, onTracklist = onSetTracklist) { overlays.add(Overlay.List(it)) }
                    2 -> DiscoverPage(vm, pad, { overlays.add(Overlay.List(it)) }, onMenu)
                    3 -> {
                        Column(Modifier.fillMaxSize()) {
                            Row(Modifier.padding(horizontal = Ab.space.gutter, vertical = Ab.space.xs), horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                                AbChip("Map", selected = catalogueMode == "map") { catalogueMode = "map" }
                                AbChip("Library", selected = catalogueMode == "library") { catalogueMode = "library" }
                            }
                            if (catalogueMode == "map") MapPage(vm, graph, onMenu)
                            else CataloguePage(vm, library, pad, { overlays.add(Overlay.Artist(it)) }, { overlays.add(Overlay.Album(it)) }, { overlays.add(Overlay.Set(it)) }, onMenu, onPasteTracklist = { openTracklist(Overlay.Tracklist()) })
                        }
                    }
                    else -> MorePage(vm, pad, { overlays.add(Overlay.Jobs) }, { overlays.add(Overlay.Settings) }, { overlays.add(Overlay.DeviceCheck) }, { overlays.add(Overlay.Catalog) })
                }
            }
            }
        }

        // the chip: everywhere but Home, where the sigil is already the now playing; on Home, a handle for the drawer
        val onHome = pager.currentPage == HOME && overlays.isEmpty()
        AnimatedVisibility(playing != null, Modifier.align(Alignment.BottomCenter), enter = slideInVertically { it } + fadeIn(), exit = slideOutVertically { it } + fadeOut()) {
            val p = playing ?: return@AnimatedVisibility
            Box(Modifier.navigationBarsPadding().padding(horizontal = Ab.space.s, vertical = Ab.space.s)) {
                if (onHome) {
                    AbSurface(Modifier.align(Alignment.Center).clickable { drawer = true }, color = Ab.colors.surface, shape = Ab.shapes.pill, border = Ab.colors.line) {
                        AbText("▴  Keep · No · Radio", style = Ab.type.number, color = Ab.colors.textMuted, modifier = Modifier.padding(horizontal = Ab.space.l, vertical = Ab.space.m))
                    }
                } else {
                    val (sigil, faint) = remember(p.ref.key, track) { sigilFor(p, track) }
                    NowChip(p.title, "${p.artist} · ${p.appLabel}", sigil, p.playing, sigilFaint = faint, syncColor = if (pending > 0) Ab.colors.notNow else null) { drawer = true }
                }
            }
        }

        // overlays slide over the pages
        AnimatedContent(
            overlays.lastOrNull(),
            transitionSpec = { (slideInHorizontally { it } + fadeIn()) togetherWith (slideOutHorizontally { it } + fadeOut()) },
            label = "overlay",
        ) { overlay ->
            if (overlay == null) return@AnimatedContent
            if (overlay == Overlay.Focus) { FocusScreen(vm, { overlays.removeAt(overlays.lastIndex) }, onMenu, onSetTracklist); return@AnimatedContent }
            OverlayFrame({ overlays.removeAt(overlays.lastIndex) }) {
                val p = PaddingValues(start = Ab.space.gutter, end = Ab.space.gutter, bottom = 96.dp)
                when (overlay) {
                    is Overlay.List -> ListDetail(vm, overlay.key, p, onMenu) { overlays.remove(overlay) }
                    is Overlay.Artist -> ArtistScreen(vm, graph, overlay.id, p, { overlays.add(Overlay.Album(it)) }, onMenu)
                    is Overlay.Album -> AlbumScreen(vm, graph, overlay.id, p, { overlays.add(Overlay.Artist(it)) }, onMenu)
                    is Overlay.Set -> SetScreen(vm, graph, overlay.id, p, onMenu) { provided -> openTracklist(if (provided) Overlay.Tracklist(show = overlay.id) else Overlay.Tracklist(attachTo = overlay.id)) }
                    is Overlay.Tracklist -> TracklistScreen(tracklist, p, onOpenList = { overlays.add(Overlay.List(it)) }, onPlay = vm::play)
                    is Overlay.Ask -> AskScreen(vm, overlay.prefill, p) { overlays.add(Overlay.List(it)) }
                    Overlay.Jobs -> JobsScreen(jobs, p) { message = it }
                    Overlay.Settings -> SettingsScreen(graph, p, onDeviceCheck = { overlays.add(Overlay.DeviceCheck) }, onCatalog = { overlays.add(Overlay.Catalog) }) { message = it }
                    Overlay.DeviceCheck -> DeviceCheckScreen(p) { message = it }
                    Overlay.Catalog -> DesignCatalog()
                    Overlay.Focus -> Unit
                }
            }
        }

        // making a mix: building, then the draft for review
        AnimatedContent(making, contentKey = { it::class }, transitionSpec = { fadeIn() togetherWith fadeOut() }, label = "making") { m ->
            when (m) {
                is AppViewModel.Making.Building -> OverlayFrame(vm::cancelMaking) { BuildingScreen(vm, m, PaddingValues(horizontal = Ab.space.gutter)) }
                is AppViewModel.Making.Draft -> OverlayFrame(vm::discardDraft) {
                    DraftScreen(vm, m, PaddingValues(start = Ab.space.gutter, end = Ab.space.gutter, bottom = 32.dp), onRefine = { prompt ->
                        vm.cancelMaking(); overlays.add(Overlay.Ask(prompt))
                    }, onMenu = onMenu)
                }
                AppViewModel.Making.Idle -> Unit
            }
        }

        AbDrawer(drawer, { drawer = false }) { NowDrawerContent(vm, onMenu, onSetTracklist) { drawer = false } }
        AbDrawer(menu != null, { menu = null }) { menu?.let { (item, where) -> ItemMenuContent(vm, item, where) { menu = null } } }

        AnimatedVisibility(message != null, Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = 84.dp), enter = fadeIn(), exit = fadeOut()) {
            AbSurface(color = Ab.colors.overlay, shape = Ab.shapes.pill) {
                AbText(message.orEmpty(), style = Ab.type.label, modifier = Modifier.padding(horizontal = Ab.space.l, vertical = Ab.space.m))
            }
        }
    }
}

@Composable
private fun OverlayFrame(onBack: () -> Unit, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxSize().background(Ab.colors.ground).statusBarsPadding().navigationBarsPadding()) {
        Row(Modifier.padding(horizontal = Ab.space.xs), verticalAlignment = Alignment.CenterVertically) {
            AbIconButton(AbIcons.Back, "Back", onBack)
        }
        Box(Modifier.weight(1f)) { content() }
    }
}

/** A sideways swipe here moves one page: -1 for a swipe right (the page to the left), +1 for a swipe left. */
private fun Modifier.pageSwipe(onPage: (Int) -> Unit) = this.pointerInput(Unit) {
    var total = 0f
    detectHorizontalDragGestures(onDragEnd = { if (total < -60f) onPage(1) else if (total > 60f) onPage(-1); total = 0f }) { change, dx -> change.consume(); total += dx }
}
