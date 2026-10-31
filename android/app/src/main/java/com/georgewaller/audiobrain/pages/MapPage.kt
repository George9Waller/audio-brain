package com.georgewaller.audiobrain.pages

import android.graphics.Bitmap
import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.animation.core.animateRectAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.CatalogueNav
import com.georgewaller.audiobrain.HueDot
import com.georgewaller.audiobrain.contextTags
import com.georgewaller.audiobrain.count
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.MapData
import com.georgewaller.audiobrain.data.MapPlace
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbDivider
import com.georgewaller.audiobrain.design.AbIconButton
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AgeTagPill
import com.georgewaller.audiobrain.design.SegmentTabs
import com.georgewaller.audiobrain.design.contextColor
import com.georgewaller.audiobrain.shortName
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/** What the map is drawn from: every track, or only those played lately, faded (played, not this year), or never. */
enum class Lens(val label: String, val caption: String) {
    All("All", "Your taste by family. Tap one to open it."),
    Lately("Lately", "Where you've played in the last 45 days"),
    Faded("Faded", "Where you played before, but not this year"),
    Unplayed("Unplayed", "Where your unplayed tracks sound like they belong");

    fun shows(flags: Int): Boolean = when (this) {
        All -> true
        Lately -> flags and LATELY != 0
        Faded -> flags and PLAYED != 0 && flags and (LATELY or THIS_YEAR) == 0
        Unplayed -> flags and PLAYED == 0
    }
}

private const val PLAYED = 2
private const val LATELY = 32
private const val THIS_YEAR = 64
private const val GRID = 320
private const val SEAM = 0xFF0C0D0F.toInt()  // the map's ground
/** The grid reaches this far past the layout's 0–1 square, so regions at its edge fade out rather than stop. */
private const val MARGIN = 0.1f

/** A map position (0–1) to its grid cell along one axis. */
private fun cell(v: Float) = ((v + MARGIN) / (1 + 2 * MARGIN) * (GRID - 1)).toInt()

/** The map's points as flat arrays, 0–1, with each one's colour and family; and each family's points and extent. */
private class Points(data: MapData) {
    val n = data.points.size
    val ids = LongArray(n)
    val xs = FloatArray(n)
    val ys = FloatArray(n)
    val flags = IntArray(n)
    val hue = FloatArray(n)  // its context's, or -1
    val near = FloatArray(n)  // the one it sounds closest to, or -1
    val family = IntArray(n)
    val members: List<IntArray>
    val bounds: Rect
    val familyBounds: List<Rect>

    init {
        val byFamily = List(data.families.size) { ArrayList<Int>() }
        data.points.forEachIndexed { i, p ->
            ids[i] = p[0].toLong(); xs[i] = p[1] / data.scale; ys[i] = p[2] / data.scale; hue[i] = p[3]; flags[i] = p[4].toInt()
            near[i] = p.getOrElse(5) { -1f }; family[i] = p.getOrElse(6) { -1f }.toInt()
            byFamily.getOrNull(family[i])?.add(i)
        }
        members = byFamily.map { it.toIntArray() }
        bounds = extent((0 until n).toList().toIntArray())
        familyBounds = members.map { extent(it) }
    }

    /** Where most of [idx] sit: the 3rd to 97th percentile each way, so a few strays don't set the frame. */
    private fun extent(idx: IntArray): Rect {
        if (idx.isEmpty()) return Rect(0f, 0f, 1f, 1f)
        val x = idx.map { xs[it] }.sorted()
        val y = idx.map { ys[it] }.sorted()
        fun q(v: List<Float>, f: Float) = v[((v.size - 1) * f).toInt()]
        return Rect(q(x, 0.03f), q(y, 0.03f), q(x, 0.97f), q(y, 0.97f))
    }
}

/** The families as soft regions: a density field per family over a grid, blurred, each cell the colour of the family densest there. */
private class Blobs(val image: ImageBitmap, val owner: IntArray) {
    /** Families with some region showing: the rest go unlabelled. */
    val shown: Set<Int> = owner.filter { it >= 0 }.toSet()
}

private fun blobs(p: Points, families: List<MapPlace>, lens: Lens, colours: IntArray): Blobs {
    val density = Array(families.size) { FloatArray(GRID * GRID) }
    for (i in 0 until p.n) {
        val f = p.family[i]
        if (f < 0 || f >= families.size || !lens.shows(p.flags[i])) continue
        val cx = cell(p.xs[i]).coerceIn(0, GRID - 1)
        val cy = cell(p.ys[i]).coerceIn(0, GRID - 1)
        density[f][cy * GRID + cx] += 1f
    }
    density.forEach { repeat(3) { _ -> blur(it, 6) } }
    val owner = IntArray(GRID * GRID) { -1 }
    val strength = FloatArray(GRID * GRID)
    for (c in 0 until GRID * GRID) {
        var best = -1; var v = 0f
        density.forEachIndexed { f, d -> if (d[c] > v) { v = d[c]; best = f } }
        owner[c] = best; strength[c] = v
    }
    // one scale for every family, so a family with little in this lens shrinks and fades beside the rest
    val sorted = strength.filter { it > 0f }.sorted()
    val top = sorted.getOrNull((sorted.size * 0.97f).toInt().coerceAtMost(sorted.size - 1))?.takeIf { it > 0f } ?: 1f
    val alpha = FloatArray(GRID * GRID) { c -> if (owner[c] < 0) 0f else smoothstep(0.04f, 0.5f, strength[c] / top) * 0.5f }
    for (c in alpha.indices) if (alpha[c] < 0.02f) owner[c] = -1
    val pixels = IntArray(GRID * GRID)
    for (c in 0 until GRID * GRID) {
        val f = owner[c]
        if (f < 0) continue
        // where two families meet, a dark seam, so neighbours of a like colour still read as two
        val x = c % GRID; val y = c / GRID
        val seam = listOf(x > 0 && owner[c - 1] >= 0 && owner[c - 1] != f, x < GRID - 1 && owner[c + 1] >= 0 && owner[c + 1] != f,
            y > 0 && owner[c - GRID] >= 0 && owner[c - GRID] != f, y < GRID - 1 && owner[c + GRID] >= 0 && owner[c + GRID] != f).any { it }
        pixels[c] = if (seam) ((0.7f * 255).toInt() shl 24) or (SEAM and 0xFFFFFF) else ((alpha[c] * 255).toInt() shl 24) or (colours[f] and 0xFFFFFF)
    }
    val bitmap = Bitmap.createBitmap(GRID, GRID, Bitmap.Config.ARGB_8888)
    bitmap.setPixels(pixels, 0, GRID, 0, 0, GRID, GRID)
    return Blobs(bitmap.asImageBitmap(), owner)
}

/** A box blur along rows then columns; three passes come close to a Gaussian. */
private fun blur(d: FloatArray, r: Int) {
    val tmp = FloatArray(d.size)
    for (y in 0 until GRID) for (x in 0 until GRID) {
        var s = 0f
        for (k in -r..r) s += d[y * GRID + (x + k).coerceIn(0, GRID - 1)]
        tmp[y * GRID + x] = s / (2 * r + 1)
    }
    for (y in 0 until GRID) for (x in 0 until GRID) {
        var s = 0f
        for (k in -r..r) s += tmp[(y + k).coerceIn(0, GRID - 1) * GRID + x]
        d[y * GRID + x] = s / (2 * r + 1)
    }
}

private fun smoothstep(a: Float, b: Float, x: Float): Float { val t = ((x - a) / (b - a)).coerceIn(0f, 1f); return t * t * (3 - 2 * t) }

/**
 * The map, calm: your families as soft regions, one small label each, and nothing else. Tap a family to open it:
 * its tracks appear as dots in their contexts' colours, with a few contexts named and the list of them below. One
 * control says what it's drawn from: everything, lately, faded or unplayed. Nothing moves unless you tap; the frame
 * glides to a family and back, and that's all.
 */
@Composable
fun MapPage(vm: AppViewModel, graph: DataGraph, nav: CatalogueNav, shown: Boolean) {
    val data by graph.map.data.collectAsStateWithLifecycle()
    // asked for only when the page is on screen; the repository checks the version at most hourly
    LaunchedEffect(shown) { if (shown) graph.map.load() }
    var family by rememberSaveable { mutableStateOf<Int?>(null) }
    var lens by rememberSaveable { mutableStateOf(Lens.All) }
    // room under the controls for the now-playing chip, only while something plays
    val session by vm.nowPlaying.collectAsStateWithLifecycle()
    val chipRoom = if (session?.hasTrack == true) 76.dp else 0.dp
    val d = data
    val points by produceState<Points?>(null, d?.version) { value = d?.let { withContext(Dispatchers.Default) { Points(it) } } }
    val colours = remember(d?.version) { d?.families?.map { contextColor(it.hue).toArgb() }?.toIntArray() ?: IntArray(0) }
    val regions by produceState<Blobs?>(null, points, lens) {
        val p = points ?: return@produceState
        value = withContext(Dispatchers.Default) { blobs(p, d!!.families, lens, colours) }
    }
    val tags = remember(d?.version) { d?.let { contextTags(it.contexts) }.orEmpty() }
    val open = family?.let { d?.families?.getOrNull(it) }
    val contexts = remember(d?.version, open) { open?.let { f -> d!!.contexts.filter { it.family == f.name }.sortedByDescending { it.recent } }.orEmpty() }

    Column(Modifier.fillMaxSize().padding(horizontal = Ab.space.gutter)) {
        Row(Modifier.heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
            if (open != null) AbIconButton(AbIcons.Back, "Back to all families", { family = null })
            AbText(open?.let { shortName(it.name) } ?: "Map", Modifier.weight(1f), style = Ab.type.display, maxLines = 1)
        }
        AbText(
            if (open != null) "${contexts.size} contexts, ${count(open.size)} tracks in them · tap one for its page" else lens.caption,
            style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 2,
        )
        Box(Modifier.weight(1f).fillMaxWidth().padding(top = Ab.space.m).clip(Ab.shapes.large).background(Color(0xFF0C0D0F))) {
            val p = points
            if (p == null || d == null) AbText("Loading the map…", Modifier.align(Alignment.Center), style = Ab.type.caption, color = Ab.colors.textMuted)
            else MapCanvas(p, d, regions, family, lens, contexts, onFamily = { family = it }, onContext = nav.context, onTrack = nav.track)
        }
        SegmentTabs(Lens.entries.map { it.label }, lens.ordinal, { lens = Lens.entries[it] }, Modifier.padding(vertical = Ab.space.m))
        if (open != null) LazyColumn(Modifier.heightIn(max = 300.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(bottom = chipRoom)) {
            items(contexts, key = { it.key }) { c ->
                Row(Modifier.fillMaxWidth().clickable { contextId(c)?.let(nav.context) }.heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                    HueDot(c.hue, near = false)
                    Spacer(Modifier.width(Ab.space.m))
                    AbText(shortName(c.name), Modifier.weight(1f), style = Ab.type.label, maxLines = 1)
                    tags[c.key]?.let { AgeTagPill(it) }
                }
                AbDivider()
            }
        } else Spacer(Modifier.height(chipRoom))
    }
}

private fun contextId(place: MapPlace): Long? = place.key.substringAfter("context:", "").toLongOrNull()

@Composable
private fun MapCanvas(
    p: Points, d: MapData, regions: Blobs?, family: Int?, lens: Lens, contexts: List<MapPlace>,
    onFamily: (Int?) -> Unit, onContext: (Long) -> Unit, onTrack: (Long) -> Unit,
) {
    val density = LocalDensity.current
    // the frame: the whole map, or the open family's part of it; it glides between them
    val target = family?.let { p.familyBounds.getOrNull(it) } ?: p.bounds
    val frame by animateRectAsState(target, tween(450), label = "map-frame")
    // the open family's dots by colour, filled for tracks in a context and hollow for the rest
    val dots = remember(p, family, lens) {
        val f = family ?: return@remember null
        val filled = HashMap<Int, ArrayList<Float>>()
        val hollow = HashMap<Int, ArrayList<Float>>()
        val big = HashMap<Int, ArrayList<Float>>()
        p.members.getOrNull(f)?.forEach { i ->
            if (!lens.shows(p.flags[i])) return@forEach
            val inContext = p.hue[i] >= 0
            val h = if (inContext) p.hue[i] else p.near[i]
            val colour = contextColor(h.takeIf { it >= 0 }).toArgb()
            val to = when {
                !inContext || lens == Lens.Unplayed -> hollow
                lens == Lens.Lately -> big
                else -> filled
            }
            to.getOrPut(colour) { ArrayList() }.apply { add(p.xs[i]); add(p.ys[i]) }
        }
        Triple(filled.mapValues { it.value.toFloatArray() }, hollow.mapValues { it.value.toFloatArray() }, big.mapValues { it.value.toFloatArray() })
    }
    val labels = remember { ArrayList<Triple<android.graphics.RectF, Int, Long?>>() }  // where each label was drawn: family or context
    val ground = Color(0xFF0C0D0F).toArgb()

    Canvas(
        Modifier.fillMaxSize().clipToBounds()
            .semantics { contentDescription = if (family == null) "Your taste by family: ${d.families.joinToString { it.name }}" else "Contexts in ${d.families.getOrNull(family)?.name}" }
            .pointerInput(p, family, regions) {
                detectTapGestures { tap ->
                    labels.firstOrNull { it.first.contains(tap.x, tap.y) }?.let { (_, f, c) -> if (c != null) onContext(c) else onFamily(f); return@detectTapGestures }
                    val (s, o) = transform(frame, size.width.toFloat(), size.height.toFloat())
                    val mx = (tap.x - o.x) / s
                    val my = (tap.y - o.y) / s
                    if (family == null) {
                        val cx = cell(mx); val cy = cell(my)
                        val owner = regions?.owner?.getOrNull(cy * GRID + cx)?.takeIf { cx in 0 until GRID && cy in 0 until GRID && it >= 0 }
                        owner?.let(onFamily)
                    } else {
                        // the nearest dot of this family within a finger's reach
                        val reach = with(density) { 20.dp.toPx() } / s
                        var best = -1; var bestD = reach
                        p.members.getOrNull(family)?.forEach { i ->
                            if (!lens.shows(p.flags[i])) return@forEach
                            val dd = hypot(p.xs[i] - mx, p.ys[i] - my)
                            if (dd < bestD) { bestD = dd; best = i }
                        }
                        if (best >= 0) onTrack(p.ids[best]) else onFamily(null)
                    }
                }
            },
    ) {
        val (s, o) = transform(frame, size.width, size.height)
        regions?.let {
            val side = s * (1 + 2 * MARGIN)
            drawImage(
                it.image, dstOffset = IntOffset((o.x - MARGIN * s).toInt(), (o.y - MARGIN * s).toInt()), dstSize = IntSize(side.toInt(), side.toInt()),
                alpha = if (family == null) 1f else 0.22f, filterQuality = FilterQuality.High,
            )
        }
        labels.clear()
        drawIntoCanvas { canvas ->
            val c = canvas.nativeCanvas
            dots?.let { (filled, hollow, big) ->
                c.save(); c.translate(o.x, o.y); c.scale(s, s)
                val paint = Paint().apply { isAntiAlias = true; strokeCap = Paint.Cap.ROUND }
                val small = 3.dp.toPx() / s
                // tracks only near a context: rings, faint behind the rest unless they're what's asked for
                val rings = if (lens == Lens.Unplayed) 1f else 0.4f
                hollow.forEach { (colour, xy) ->
                    paint.color = colour; paint.alpha = (255 * rings).toInt(); paint.strokeWidth = small * 1.6f; c.drawPoints(xy, paint)
                    paint.color = ground; paint.alpha = 255; paint.strokeWidth = small * 0.8f; c.drawPoints(xy, paint)
                }
                paint.alpha = 255
                filled.forEach { (colour, xy) -> paint.color = colour; paint.strokeWidth = small * 1.2f; c.drawPoints(xy, paint) }
                big.forEach { (colour, xy) -> paint.color = colour; paint.strokeWidth = small * 2.2f; c.drawPoints(xy, paint) }
                c.restore()
            }
            // labels: a family each when closed; the open family's biggest contexts, up to four, when open
            val paint = Paint().apply { isAntiAlias = true; textAlign = Paint.Align.CENTER; typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD) }
            val placed = ArrayList<android.graphics.RectF>()
            val names = if (family == null) d.families.mapIndexed { i, f -> Triple(f, i, null as Long?) }.filter { regions == null || it.second in regions.shown }
            else contexts.sortedByDescending { it.size }.map { Triple(it, family, contextId(it)) }
            var shown = 0
            names.forEach { (place, f, context) ->
                if (context != null && shown >= 4) return@forEach
                val at = Offset(o.x + place.x / d.scale * s, o.y + place.y / d.scale * s)
                val text = if (context == null) shortName(place.name).uppercase() else shortName(place.name)
                paint.textSize = (if (context == null) 11 else 13).dp.toPx()
                paint.letterSpacing = if (context == null) 0.08f else 0f
                paint.typeface = if (context == null) Typeface.create(Typeface.MONOSPACE, Typeface.BOLD) else Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
                val w = paint.measureText(text)
                val pad = 10.dp.toPx()
                // kept inside the frame, and moved up or down a line if another label is in the way
                val x = at.x.coerceIn(w / 2 + pad, size.width - w / 2 - pad)
                val step = paint.textSize + pad
                val rect = listOf(0f, -step, step, -2 * step, 2 * step).asSequence()
                    .map { dy -> android.graphics.RectF(x - w / 2 - pad, at.y + dy - paint.textSize - pad / 2, x + w / 2 + pad, at.y + dy + pad / 2 + 2) }
                    .firstOrNull { r -> r.top >= 0 && r.bottom <= size.height && placed.none { android.graphics.RectF.intersects(it, r) } } ?: return@forEach
                placed.add(rect); labels.add(Triple(rect, f, context)); shown++
                paint.style = Paint.Style.FILL
                paint.color = if (context == null) labelColour(place.hue) else 0xFFECEDEE.toInt()
                paint.setShadowLayer(4.dp.toPx(), 0f, 1.dp.toPx(), 0xFF000000.toInt())
                c.drawText(text, rect.centerX(), rect.bottom - pad / 2 - 2, paint)
                paint.clearShadowLayer()
            }
        }
    }
}

/** Map space (0–1) to the canvas: [frame] fitted and centred, with a margin. */
private fun transform(frame: Rect, w: Float, h: Float): Pair<Float, Offset> {
    val s = min(w / max(frame.width, 0.02f), h / max(frame.height, 0.02f)) * 0.8f
    return s to Offset(w / 2 - frame.center.x * s, h / 2 - frame.center.y * s)
}

/** A family's colour lifted for text: the same hue, lighter, so it reads over its region. */
private fun labelColour(hue: Float?): Int = if (hue == null) 0xFF9AA0A6.toInt() else Color.hsl(((hue % 360f) + 360f) % 360f, 0.62f, 0.76f).toArgb()
