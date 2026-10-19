package com.georgewaller.audiobrain.pages

import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.height
import androidx.compose.ui.draw.clip
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.graphics.Brush
import com.georgewaller.audiobrain.design.Dot
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.design.SegmentTabs
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.MapData
import com.georgewaller.audiobrain.data.TrackDto
import com.georgewaller.audiobrain.data.TrackRef
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbChip
import com.georgewaller.audiobrain.design.AbSurface
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbTextField
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.DarkColors
import com.georgewaller.audiobrain.design.SigilSpec
import com.georgewaller.audiobrain.design.SoundSigil
import com.georgewaller.audiobrain.design.Spinner
import com.georgewaller.audiobrain.design.contextColor
import com.georgewaller.audiobrain.openUrl
import com.georgewaller.audiobrain.shortName
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.hypot
import kotlin.math.roundToInt

/** The map's points prepared once for drawing: one flat x,y array per colour, and a grid for finding a tapped point. */
private class Prepared(data: MapData, val size: Float, colourMode: String) {
    val ids = LongArray(data.points.size)
    val xs = FloatArray(data.points.size)
    val ys = FloatArray(data.points.size)
    val flags = IntArray(data.points.size)
    val index = HashMap<Long, Int>(data.points.size * 2)
    /** colour (argb) -> interleaved x,y in map space; grey for tracks in no context */
    val byColour: Map<Int, FloatArray>
    val discovery: FloatArray
    val listed: FloatArray
    private val grid = HashMap<Int, MutableList<Int>>()
    private val cells = 64

    val legend: Legend

    init {
        val buckets = HashMap<Int, MutableList<Float>>()
        val disc = ArrayList<Float>()
        val lst = ArrayList<Float>()
        val grey = DarkColors.textFaint.copy(alpha = 0.55f).toArgb()
        val metas = data.points.map { data.metadata[it[0].toLong().toString()] }
        // Categories get distinct colours by how common they are; the long tail shares one "other" colour.
        val categories = when (colourMode) {
            "country" -> metas.map { it?.country.orEmpty() }
            "language" -> metas.map { it?.language.orEmpty() }
            else -> null
        }
        val top = categories?.filter { it.isNotEmpty() }?.groupingBy { it }?.eachCount()
            ?.entries?.sortedByDescending { it.value }?.take(CATEGORY_HUES.size)
            ?.mapIndexed { k, e -> e.key to contextColor(CATEGORY_HUES[k]).toArgb() }?.toMap().orEmpty()
        val other = DarkColors.textMuted.copy(alpha = 0.7f).toArgb()
        // Era and energy are ordered, so they share one ramp; era runs over the middle 96% of years so a few
        // very old tracks don't squash everything else into one end.
        val years = metas.mapNotNull { it?.era }.sorted()
        val yearLo = years.getOrNull((years.size * 0.02).toInt()) ?: 1970
        val yearHi = years.getOrNull((years.size * 0.98).toInt().coerceAtMost(years.size - 1)) ?: 2025
        fun ramp(t: Float) = rampColour((t * RAMP_STEPS).roundToInt() / RAMP_STEPS.toFloat()).toArgb()
        data.points.forEachIndexed { i, p ->
            val x = p[1] / data.scale * size
            val y = p[2] / data.scale * size
            ids[i] = p[0].toLong(); xs[i] = x; ys[i] = y; flags[i] = p[4].toInt(); index[ids[i]] = i
            val hue = p[3]
            val meta = metas[i]
            val colour = when (colourMode) {
                "context" -> if (hue >= 0) contextColor((hue / 10f).roundToInt() * 10f).toArgb() else grey
                "country", "language" -> categories!![i].let { if (it.isEmpty()) grey else top[it] ?: other }
                "era" -> meta?.era?.let { ramp(((it - yearLo).toFloat() / (yearHi - yearLo).coerceAtLeast(1)).coerceIn(0f, 1f)) } ?: grey
                "energy" -> meta?.energy?.let { ramp(it.coerceIn(0f, 1f)) } ?: grey
                else -> grey
            }
            buckets.getOrPut(colour) { ArrayList() }.apply { add(x); add(y) }
            if (flags[i] and 4 != 0) { disc.add(x); disc.add(y) }
            if (flags[i] and 8 != 0) { lst.add(x); lst.add(y) }
            grid.getOrPut(cell(x, y)) { ArrayList() }.add(i)
        }
        byColour = buckets.mapValues { it.value.toFloatArray() }
        legend = when (colourMode) {
            "country", "language" -> Legend.Categories(top.map { (name, c) -> name to c } + ("Other" to other))
            "era" -> Legend.Ramp("$yearLo", "${(yearLo + yearHi) / 2}", "$yearHi")
            "energy" -> Legend.Ramp("Calm", "", "Intense")
            else -> Legend.Categories(
                data.families.filter { it.hue != null }.sortedByDescending { it.size }.take(CATEGORY_HUES.size)
                    .map { shortName(it.name) to contextColor(it.hue).toArgb() },
            )
        }
        discovery = disc.toFloatArray()
        listed = lst.toFloatArray()
    }

    private fun cell(x: Float, y: Float): Int {
        val cx = (x / size * cells).toInt().coerceIn(0, cells - 1)
        val cy = (y / size * cells).toInt().coerceIn(0, cells - 1)
        return cy * cells + cx
    }

    /** The point nearest a map position, within [radius] map units, or -1. */
    fun nearest(x: Float, y: Float, radius: Float): Int {
        var best = -1
        var bestD = radius
        val r = (radius / size * cells).toInt() + 1
        val cx = (x / size * cells).toInt()
        val cy = (y / size * cells).toInt()
        for (gy in cy - r..cy + r) for (gx in cx - r..cx + r) {
            if (gx !in 0 until cells || gy !in 0 until cells) continue
            grid[gy * cells + gx]?.forEach { i ->
                val d = hypot(xs[i] - x, ys[i] - y)
                if (d < bestD) { bestD = d; best = i }
            }
        }
        return best
    }
}

/** What the map's colours mean: named swatches, or a low-to-high ramp. */
private sealed interface Legend {
    data class Categories(val items: List<Pair<String, Int>>) : Legend
    data class Ramp(val low: String, val mid: String, val high: String) : Legend
}

/** Hues for the most common countries or languages, spaced so neighbours in the key never look alike. */
private val CATEGORY_HUES = listOf(205f, 28f, 140f, 330f, 52f, 265f, 175f, 0f)
private const val RAMP_STEPS = 24
/** Deep violet through teal to warm yellow: ordered, readable on the dark ground, and kind to colour blindness. */
private val RAMP = listOf(Color(0xFF5A4FCF), Color(0xFF2E8FB0), Color(0xFF35B48C), Color(0xFFB8D45A), Color(0xFFF4D35E))
private fun rampColour(t: Float): Color {
    val x = t.coerceIn(0f, 1f) * (RAMP.size - 1)
    val i = x.toInt().coerceAtMost(RAMP.size - 2)
    return androidx.compose.ui.graphics.lerp(RAMP[i], RAMP[i + 1], x - i)
}

/**
 * Your catalogue as a place: every analysed track where its sound and the way you play it put it. Pinch and pan;
 * tap a point for the track. Layers show your contexts' colours, your lists as labelled areas, and Fresh and
 * Rediscovery picked out; a search lights up its matches and moves there.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MapPage(vm: AppViewModel, graph: DataGraph, onMenu: (ListItem, String) -> Unit) {
    val data by graph.map.data.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    LaunchedEffect(Unit) { graph.map.load() }

    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    var colourMode by remember { mutableStateOf("context") }
    var showLists by remember { mutableStateOf(true) }
    var showDiscovery by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    var hits by remember { mutableStateOf<Set<Long>?>(null) }
    var searching by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<Int?>(null) }
    var detail by remember { mutableStateOf<TrackDto?>(null) }
    var viewport by remember { mutableStateOf(1f) }
    var placedInitially by remember { mutableStateOf(false) }
    var focusedList by remember { mutableStateOf<String?>(null) }
    var controlsHeight by remember { mutableFloatStateOf(0f) }
    var keyTop by remember { mutableFloatStateOf(Float.MAX_VALUE) }
    val listLabels = remember { ArrayList<Pair<android.graphics.RectF, String>>() }
    val density = LocalDensity.current
    val prepared = remember(data?.version, viewport, colourMode) { data?.let { Prepared(it, viewport, colourMode) } }
    val nowPlaying by vm.track.collectAsStateWithLifecycle()
    // the now-playing chip covers the bottom of the page only while something plays
    val session by vm.nowPlaying.collectAsStateWithLifecycle()
    val chipRoom = if (session?.hasTrack == true) 76.dp else Ab.space.s
    val pulse by rememberInfiniteTransition(label = "map-playing").animateFloat(0f, 1f, infiniteRepeatable(tween(1100, easing = LinearEasing), RepeatMode.Restart), label = "map-playing")

    // search: names, then fit the view to the matches
    LaunchedEffect(query) {
        if (query.isBlank()) { hits = null; return@LaunchedEffect }
        delay(400)
        searching = true
        try {
            val r = graph.api.search(query, false)
            val ids = r.tracks.mapNotNull { it.trackId }.toSet()
            hits = ids
            val p = prepared
            if (p != null) {
                val pts = ids.mapNotNull { p.index[it] }
                if (pts.isNotEmpty()) {
                    val minX = pts.minOf { p.xs[it] }; val maxX = pts.maxOf { p.xs[it] }
                    val minY = pts.minOf { p.ys[it] }; val maxY = pts.maxOf { p.ys[it] }
                    val span = maxOf(maxX - minX, maxY - minY, viewport * 0.08f)
                    scale = (viewport / span * 0.7f).coerceIn(1f, 30f)
                    offset = Offset(viewport / 2 - (minX + maxX) / 2 * scale, viewport / 2 - (minY + maxY) / 2 * scale)
                }
            }
        } catch (_: ApiException) { }
        searching = false
    }
    LaunchedEffect(selected) {
        detail = null
        val i = selected ?: return@LaunchedEffect
        val p = prepared ?: return@LaunchedEffect
        detail = runCatching { graph.api.track(p.ids[i]) }.getOrNull()
    }

    val textColour = Ab.colors.text
    val faint = Ab.colors.textFaint
    Box(Modifier.fillMaxSize().background(Ab.colors.ground)) {
        val p = prepared
        Canvas(
            Modifier
                .fillMaxSize()
                .clipToBounds()
                .pointerInput(Unit) {
                    detectTransformGestures { centroid, pan, zoom, _ ->
                        val newScale = (scale * zoom).coerceIn(0.8f, 40f)
                        // zoom about the fingers
                        offset = (offset - centroid) * (newScale / scale) + centroid + pan
                        scale = newScale
                    }
                }
                .pointerInput(prepared) {
                    detectTapGestures(onDoubleTap = { at ->
                        // double-tap zooms in two times around the spot; at full zoom it goes back to the whole map
                        val newScale = if (scale >= 30f) 1f else (scale * 2f).coerceAtMost(40f)
                        offset = if (newScale == 1f) Offset((size.width - viewport) / 2, (size.height - viewport) / 2) else (offset - at) * (newScale / scale) + at
                        scale = newScale
                    }) { tap ->
                        // a list's label focuses it (tap again to show them all)
                        listLabels.firstOrNull { it.first.contains(tap.x, tap.y) }?.let { (_, key) ->
                            focusedList = if (focusedList == key) null else key
                            return@detectTapGestures
                        }
                        val pp = prepared ?: return@detectTapGestures
                        val mx = (tap.x - offset.x) / scale
                        val my = (tap.y - offset.y) / scale
                        val i = pp.nearest(mx, my, with(density) { 24.dp.toPx() } / scale)
                        selected = if (i >= 0) i else null
                    }
                },
        ) {
            if (viewport != size.minDimension) viewport = size.minDimension
            if (p == null) return@Canvas
            if (!placedInitially && controlsHeight > 0f && keyTop < Float.MAX_VALUE && p.xs.isNotEmpty()) {
                // start with the whole map filling the space between the search panel and the key
                placedInitially = true
                val left = p.xs.min(); val right = p.xs.max(); val top = p.ys.min(); val bottom = p.ys.max()
                val areaTop = controlsHeight; val areaBottom = keyTop
                scale = (minOf(size.width / (right - left), (areaBottom - areaTop) / (bottom - top)) * 0.94f).coerceAtLeast(0.2f)
                offset = Offset(size.width / 2 - (left + right) / 2 * scale, (areaTop + areaBottom) / 2 - (top + bottom) / 2 * scale)
            }
            drawIntoCanvas { canvas ->
                val c = canvas.nativeCanvas
                c.save()
                c.translate(offset.x, offset.y)
                c.scale(scale, scale)
                val dot = Paint().apply { isAntiAlias = true; strokeCap = Paint.Cap.ROUND }
                val pointSize = (2.6.dp.toPx() + scale * 0.35.dp.toPx()).coerceAtMost(9.dp.toPx()) / scale
                dot.strokeWidth = pointSize
                val dim = hits != null
                p.byColour.forEach { (colour, pts) ->
                    dot.color = colour
                    dot.alpha = if (dim) 45 else 255
                    c.drawPoints(pts, dot)
                }
                if (showDiscovery) {
                    dot.color = textColour.toArgb(); dot.alpha = 255; dot.style = Paint.Style.STROKE
                    dot.strokeWidth = pointSize * 1.8f
                    c.drawPoints(p.discovery, dot)
                    dot.strokeWidth = pointSize
                }
                hits?.let { set ->
                    dot.color = textColour.toArgb(); dot.alpha = 255; dot.strokeWidth = pointSize * 1.6f
                    val pts = FloatArray(set.size * 2); var k = 0
                    set.forEach { id -> p.index[id]?.let { i -> pts[k++] = p.xs[i]; pts[k++] = p.ys[i] } }
                    c.drawPoints(pts, 0, k, dot)
                }
                if (showLists) {
                    // each list's own tracks ringed in its colour; a focused list also shows its route in play order
                    val ring = Paint().apply { isAntiAlias = true; style = Paint.Style.STROKE; strokeWidth = 1.5.dp.toPx() / scale }
                    val route = Paint().apply { isAntiAlias = true; style = Paint.Style.STROKE; strokeWidth = 1.2.dp.toPx() / scale }
                    data!!.lists.forEach { l ->
                        if (focusedList != null && focusedList != l.key) return@forEach
                        val colour = (if (l.hue != null) contextColor(l.hue) else textColour).toArgb()
                        ring.color = colour
                        val idx = l.members.mapNotNull { p.index[it] }
                        idx.forEach { i -> c.drawCircle(p.xs[i], p.ys[i], 4.5.dp.toPx() / scale, ring) }
                        if (focusedList == l.key && idx.size > 1) {
                            route.color = colour; route.alpha = 150
                            val path = android.graphics.Path().apply { moveTo(p.xs[idx[0]], p.ys[idx[0]]); idx.drop(1).forEach { lineTo(p.xs[it], p.ys[it]) } }
                            c.drawPath(path, route)
                        }
                    }
                }
                selected?.let { i ->
                    val ring = Paint().apply { isAntiAlias = true; style = Paint.Style.STROKE; strokeWidth = 2.dp.toPx() / scale; color = textColour.toArgb() }
                    c.drawCircle(p.xs[i], p.ys[i], 9.dp.toPx() / scale, ring)
                }
                nowPlaying?.id?.let { trackId -> p.index[trackId]?.let { i ->
                    val ring = Paint().apply { isAntiAlias = true; style = Paint.Style.STROKE; strokeWidth = 2.dp.toPx() / scale; color = textColour.toArgb(); alpha = (220 * (1f - pulse)).toInt() }
                    c.drawCircle(p.xs[i], p.ys[i], (10 + pulse * 18).dp.toPx() / scale, ring)
                } }
                c.restore()

                // Labels in screen space, never overlapping, in tiers by zoom: families when the whole map is in view,
                // contexts as you zoom in, then artists, smaller ones appearing as there's room. Lists are always first.
                val d = data!!
                fun screen(pl: com.georgewaller.audiobrain.data.MapPlace) = Offset(pl.x / d.scale * viewport * scale + offset.x, pl.y / d.scale * viewport * scale + offset.y)
                val bold = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
                val placed = ArrayList<android.graphics.RectF>()
                listLabels.clear()
                data class Label(val place: com.georgewaller.audiobrain.data.MapPlace, val text: String, val size: Float, val colour: Int, val alpha: Int, val caps: Boolean = false, val list: Boolean = false)
                val labels = buildList {
                    if (showLists) d.lists.filter { focusedList == null || it.key == focusedList }.forEach { add(Label(it, shortName(it.name), 12f, textColour.toArgb(), 255, list = true)) }
                    if (scale < 2.2f) d.families.sortedByDescending { it.size }.forEach { add(Label(it, it.name.uppercase(), 11f, (it.hue?.let(::labelColour) ?: faint).toArgb(), 255, caps = true)) }
                    if (scale >= 1.6f) d.contexts.sortedByDescending { it.size }.forEach { add(Label(it, shortName(it.name).substringAfter(": "), 12f, (it.hue?.let(::labelColour) ?: faint).toArgb(), 255)) }
                    if (scale >= 3f) {
                        val minTracks = (90f / scale).coerceAtLeast(6f)
                        d.artists.filter { it.size >= minTracks }.sortedByDescending { it.size }.forEach { add(Label(it, it.name, 11f, textColour.toArgb(), 215)) }
                    }
                }
                val paint = Paint().apply { isAntiAlias = true; textAlign = Paint.Align.CENTER }
                labels.forEach { l ->
                    val at = screen(l.place)
                    paint.textSize = l.size.dp.toPx()
                    paint.typeface = bold
                    paint.letterSpacing = if (l.caps) 0.08f else 0f
                    val w = paint.measureText(l.text)
                    val y = at.y - if (l.list) 10.dp.toPx() else 0f
                    val rect = android.graphics.RectF(at.x - w / 2 - 6, y - paint.textSize - 2, at.x + w / 2 + 6, y + 5)
                    // whole labels only, and none under the search box or the key
                    if (rect.left < 0 || rect.right > size.width || rect.top < controlsHeight || rect.bottom > minOf(size.height, keyTop)) return@forEach
                    if (placed.any { android.graphics.RectF.intersects(it, rect) }) return@forEach
                    placed.add(rect)
                    if (l.list) listLabels.add(rect to l.place.key)
                    paint.color = l.colour
                    paint.alpha = l.alpha
                    // a dark outline and a soft drop shadow keep coloured text readable over dense points
                    val c0 = paint.color
                    paint.style = Paint.Style.STROKE; paint.strokeWidth = 4.5.dp.toPx(); paint.strokeJoin = Paint.Join.ROUND
                    paint.color = 0xF2101113.toInt()
                    paint.setShadowLayer(5.dp.toPx(), 0f, 1.5.dp.toPx(), 0xFF000000.toInt())
                    c.drawText(l.text, at.x, y, paint)
                    paint.clearShadowLayer()
                    paint.style = Paint.Style.FILL; paint.color = c0; paint.alpha = l.alpha
                    c.drawText(l.text, at.x, y, paint)
                }
            }
        }

        // search and layers on top
        Column(Modifier.fillMaxWidth().onSizeChanged { controlsHeight = it.height.toFloat() }.padding(horizontal = Ab.space.gutter, vertical = Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbSurface(Modifier.fillMaxWidth(), color = Ab.colors.ground.copy(alpha = 0.92f), shape = Ab.shapes.medium) {
                Column(Modifier.padding(horizontal = Ab.space.xs, vertical = Ab.space.xs)) {
                    AbTextField(query, { query = it }, "", placeholder = "Find a track, artist or album")
                    if (searching || hits != null) Row(Modifier.padding(horizontal = Ab.space.s, vertical = Ab.space.xs), verticalAlignment = Alignment.CenterVertically) {
                        if (searching) Spinner(size = 14.dp)
                        hits?.let { AbText("${it.size} found", style = Ab.type.number, color = Ab.colors.textMuted) }
                    }
                    SegmentTabs(COLOUR_MODES.map { it.second }, COLOUR_MODES.indexOfFirst { it.first == colourMode }, { colourMode = COLOUR_MODES[it].first })
                }
            }
        }

        // the key: what the colours mean, and the overlays; out of the way while a track is open
        val key = prepared?.legend
        if (selected == null && key != null) {
            DisposableEffect(Unit) { onDispose { keyTop = Float.MAX_VALUE } }
            var open by remember { mutableStateOf(true) }
            AbSurface(
                Modifier.align(Alignment.BottomStart).padding(start = Ab.space.gutter, end = Ab.space.gutter, bottom = chipRoom).fillMaxWidth()
                    .onGloballyPositioned { keyTop = it.positionInParent().y },
                color = Ab.colors.ground.copy(alpha = 0.92f), shape = Ab.shapes.medium,
            ) {
                Column(Modifier.padding(Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Row(Modifier.fillMaxWidth().clickable { open = !open }, verticalAlignment = Alignment.CenterVertically) {
                        Overline(COLOUR_MODES.first { it.first == colourMode }.third, Modifier.weight(1f))
                        AbText(if (open) "Hide" else "Key", style = Ab.type.caption, color = Ab.colors.textFaint)
                    }
                    if (open) when (key) {
                        is Legend.Categories -> FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
                            key.items.forEach { (name, colour) ->
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Dot(Color(colour), size = 9.dp)
                                    Spacer(Modifier.width(Ab.space.xs + 2.dp))
                                    AbText(name, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
                                }
                            }
                        }
                        is Legend.Ramp -> Column(verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
                            Box(Modifier.fillMaxWidth().height(8.dp).clip(Ab.shapes.pill).background(Brush.horizontalGradient(RAMP)))
                            Row(Modifier.fillMaxWidth()) {
                                AbText(key.low, style = Ab.type.number, color = Ab.colors.textMuted)
                                Spacer(Modifier.weight(1f))
                                AbText(key.mid, style = Ab.type.number, color = Ab.colors.textMuted)
                                Spacer(Modifier.weight(1f))
                                AbText(key.high, style = Ab.type.number, color = Ab.colors.textMuted)
                            }
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
                        AbChip("Lists", selected = showLists) { showLists = !showLists }
                        AbChip("New and faded", selected = showDiscovery) { showDiscovery = !showDiscovery }
                        Spacer(Modifier.weight(1f))
                        Dot(DarkColors.textFaint.copy(alpha = 0.55f), size = 7.dp)
                        Spacer(Modifier.width(Ab.space.xs))
                        AbText("unknown", style = Ab.type.caption, color = Ab.colors.textFaint)
                    }
                }
            }
        }
        if (data == null) Box(Modifier.align(Alignment.Center)) { AbText("Loading the map…", style = Ab.type.caption, color = Ab.colors.textMuted) }

        // the tapped track
        selected?.let { i ->
            val p2 = prepared ?: return@let
            AbSurface(Modifier.align(Alignment.BottomCenter).padding(start = Ab.space.s, end = Ab.space.s, bottom = chipRoom).fillMaxWidth(), color = Ab.colors.surface, shape = Ab.shapes.medium, border = Ab.colors.line) {
                Column(Modifier.padding(Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    val d = detail
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        val sound = d?.sound
                        SoundSigil(
                            if (sound != null) SigilSpec(listOf(sound.happy, sound.party, sound.danceability, sound.aggressive, sound.sad, sound.relaxed), sound.energy, sound.bpm, d.context?.hue)
                            else SigilSpec.fromSeed("${p2.ids[i]}"), size = 40.dp, faint = sound == null,
                        )
                        Spacer(Modifier.width(Ab.space.m))
                        Column(Modifier.weight(1f)) {
                            AbText(d?.name ?: "…", style = Ab.type.label, maxLines = 1)
                            AbText(
                                listOfNotNull(d?.artist, d?.context?.name?.let(::shortName), d?.let { "${it.plays} plays" }).joinToString(" · "),
                                style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1,
                            )
                        }
                        d?.sound?.let { AbText("${it.bpm.roundToInt()}\n${it.camelot ?: ""}", style = Ab.type.number, color = Ab.colors.textFaint) }
                    }
                    if (d != null) Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        val item = ListItem(kind = "track", trackId = d.id, title = d.name, artist = d.artist, album = d.album.orEmpty(), tidalId = d.tidalId, hue = d.context?.hue, contextId = d.context?.id)
                        AbButton("Keep", { vm.give(Verdict.Keep, item, "map") }, kind = ButtonKind.Quiet)
                        AbButton("No", { vm.give(Verdict.No, item, "map") }, kind = ButtonKind.Ghost)
                        AbButton("Radio", { vm.startRadio(item) }, kind = ButtonKind.Ghost)
                        AbButton("Explore", { vm.explore(trackId = d.id) }, kind = ButtonKind.Ghost)
                        d.tidalUrl?.let { url -> AbButton("Tidal", { openUrl(context, url) }, kind = ButtonKind.Ghost) }
                    }
                }
            }
        }
    }
}

/** Colour modes: key, tab label, and the key's heading. */
private val COLOUR_MODES = listOf(
    Triple("context", "Context", "Colour · your contexts, by family"),
    Triple("country", "Country", "Colour · artist's country"),
    Triple("language", "Language", "Colour · language sung in"),
    Triple("era", "Era", "Colour · year released"),
    Triple("energy", "Energy", "Colour · energy"),
)

/** A context's colour lifted for text: the same hue, lighter, so it reads over the points it labels. */
private fun labelColour(hue: Float) = Color.hsl(((hue % 360f) + 360f) % 360f, 0.62f, 0.76f)
