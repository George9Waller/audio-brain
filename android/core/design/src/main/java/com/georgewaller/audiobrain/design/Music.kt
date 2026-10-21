package com.georgewaller.audiobrain.design

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.drawscope.CanvasDrawScope
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * What a sound sigil is drawn from. [moods] are 0-1 values in a fixed order (happy, party, danceability, aggressive,
 * sad, relaxed) and become the lobes: ranked within the track's context where the server sends that, so one list's
 * tracks differ. [energy] is the ring's weight; [bpm] the ticks around it; [hue] its context's colour, or null for a
 * track outside your contexts (drawn grey). [camelot] (e.g. "8A") turns the shape 30° a step round the wheel, draws
 * minor keys hollow and nudges the colour, so keys that mix look related; [vocal] puts a dot in the middle.
 */
@Immutable
data class SigilSpec(
    val moods: List<Float>, val energy: Float, val bpm: Float, val hue: Float?,
    val camelot: String? = null, val vocal: Boolean = false,
) {
    val keyNumber: Int? get() = camelot?.dropLast(1)?.toIntOrNull()
    val minor: Boolean get() = camelot?.endsWith("A") == true

    companion object {
        /** A stable stand-in for a track that hasn't been analysed: same names, same glyph. Drawn fainter. */
        fun fromSeed(seed: String, hue: Float? = null): SigilSpec {
            var h = seed.hashCode().toLong() and 0xffffffffL
            fun next(): Float { h = (h * 6364136223846793005L + 1442695040888963407L); return ((h ushr 33) % 1000) / 1000f }
            return SigilSpec(List(6) { 0.25f + next() * 0.5f }, 0.3f + next() * 0.4f, 90f + next() * 50f, hue)
        }
    }
}

/**
 * The glyph for a track's sound: lobes for its moods, a ring for energy, ticks for tempo, in its context's colour.
 * Deterministic, so you learn to recognise a sound before reading the name. Strokes scale with [size], so it works
 * from a 28 dp row to the full Home page. [playing] makes it pulse on the beat (from the BPM, not the audio).
 */
@Composable
fun SoundSigil(spec: SigilSpec, modifier: Modifier = Modifier, size: Dp = 44.dp, playing: Boolean = false, faint: Boolean = false) {
    val ground = Ab.colors.ground
    val beatMs = (60_000f / spec.bpm.coerceIn(60f, 200f)).roundToInt()
    val pulse = if (playing) {
        rememberInfiniteTransition(label = "beat").animateFloat(1f, 1.05f, infiniteRepeatable(tween(beatMs / 2, easing = FastOutSlowInEasing), RepeatMode.Reverse), label = "pulse").value
    } else 1f
    val path = remember(spec) { Path() }
    Canvas(modifier.size(size).scale(pulse)) { drawSigil(spec, path, ground, faint) }
}

/** The sigil as a bitmap [px] square, for places Compose can't draw into: the home screen widget. */
fun sigilBitmap(spec: SigilSpec, px: Int, density: Float, faint: Boolean = false): android.graphics.Bitmap {
    val image = ImageBitmap(px, px)
    CanvasDrawScope().draw(Density(density), LayoutDirection.Ltr, androidx.compose.ui.graphics.Canvas(image), Size(px.toFloat(), px.toFloat())) {
        drawSigil(spec, Path(), DarkColors.ground, faint)
    }
    return image.asAndroidBitmap()
}

/** The sigil's colour: its context's, shifted a little round the Camelot wheel so keys that mix look related. */
fun sigilColor(spec: SigilSpec): Color {
    val key = spec.keyNumber
    return if (spec.hue != null && key != null)
        Color.hsl(((spec.hue + (key - 6.5f) / 5.5f * 16f) % 360f + 360f) % 360f, 0.58f, if (spec.minor) 0.59f else 0.66f)
    else contextColor(spec.hue)
}

private fun DrawScope.drawSigil(spec: SigilSpec, path: Path, ground: Color, faint: Boolean) {
    val key = spec.keyNumber
    val color = sigilColor(spec)
    val c = Offset(size.width / 2, size.height / 2)
    val outer = size.minDimension / 2
    val k = (size.minDimension / 44.dp.toPx()).coerceAtLeast(0.7f)  // stroke scale
    val alpha = if (faint) 0.45f else 1f
    val ticks = (spec.bpm / 10f).roundToInt().coerceIn(6, 20)
    rotate(-90f, c) {
        for (i in 0 until ticks) {
            val a = (2 * PI * i / ticks).toFloat()
            drawLine(color.copy(alpha = 0.5f * alpha), c + polar(a, outer * 0.86f), c + polar(a, outer), 1.2.dp.toPx() * k, StrokeCap.Round)
        }
    }
    drawCircle(color.copy(alpha = 0.9f * alpha), outer * 0.72f, c, style = Stroke((0.8f + spec.energy * 3.2f).dp.toPx() * k))
    val n = spec.moods.size
    val turn = if (key != null) ((key - 1) * PI / 6).toFloat() else 0f
    val points = spec.moods.mapIndexed { i, m -> c + polar((2 * PI * i / n - PI / 2).toFloat() + turn, outer * (0.17f + 0.44f * m.coerceIn(0f, 1f))) }
    path.reset()
    val start = (points.last() + points.first()) / 2f
    path.moveTo(start.x, start.y)
    for (i in points.indices) {
        val p = points[i]
        val m = (p + points[(i + 1) % n]) / 2f
        path.quadraticTo(p.x, p.y, m.x, m.y)
    }
    path.close()
    val hollow = key != null && spec.minor
    if (hollow) drawPath(path, color.copy(alpha = alpha), style = Stroke(1.8.dp.toPx() * k, join = StrokeJoin.Round))
    else drawPath(path, color.copy(alpha = alpha))
    if (spec.vocal) drawCircle(if (hollow) color.copy(alpha = alpha) else ground, maxOf(1.6.dp.toPx(), outer * 0.075f), c)
}

private fun polar(angle: Float, r: Float) = Offset(cos(angle) * r, sin(angle) * r)

/** A 0-1 level as a short flat bar: energy, fit. */
@Composable
fun LevelBar(value: Float, modifier: Modifier = Modifier, color: Color = Ab.colors.textMuted, width: Dp = 44.dp) {
    val v by animateFloatAsState(value.coerceIn(0f, 1f), AbMotion.smooth(), label = "level")
    val track = Ab.colors.line
    Canvas(modifier.size(width, 3.dp)) {
        drawRect(track)
        drawRect(color, size = size.copy(width = size.width * v))
    }
}

/** What a list is made of: one flat segment per track, in order, in its context's colour (grey when it has none). */
@Composable
fun ColourStrip(hues: List<Float?>, modifier: Modifier = Modifier, height: Dp = 4.dp) {
    val faint = Ab.colors.textFaint.copy(alpha = 0.5f)
    Canvas(modifier.fillMaxWidth().height(height)) {
        if (hues.isEmpty()) return@Canvas
        val gap = 1.dp.toPx()
        val w = (size.width - gap * (hues.size - 1)) / hues.size
        hues.forEachIndexed { i, h -> drawRect(if (h == null) faint else contextColor(h), Offset(i * (w + gap), 0f), size.copy(width = w.coerceAtLeast(0.5f))) }
    }
}

/** The four verdicts, as the design system draws them. The data layer maps these to the API's names. */
enum class VerdictStyle(val label: String, val icon: ImageVector) {
    Keep("Keep", AbIcons.Keep), No("No", AbIcons.No), NotNow("Not now", AbIcons.NotNow), Never("Never", AbIcons.Never);

    @Composable
    fun color(): Color = when (this) {
        Keep -> Ab.colors.keep
        No -> Ab.colors.no
        NotNow -> Ab.colors.notNow
        Never -> Ab.colors.never
    }
}

/** One tap per verdict, with a haptic tick. [selected] is the verdict already given, shown filled. */
@Composable
fun VerdictBar(
    selected: VerdictStyle?,
    onVerdict: (VerdictStyle) -> Unit,
    modifier: Modifier = Modifier,
    options: List<VerdictStyle> = VerdictStyle.entries,
) {
    val haptics = LocalHapticFeedback.current
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
        options.forEach { verdict ->
            val on = verdict == selected
            val color = verdict.color()
            val background by animateColorAsState(if (on) color else Color.Transparent, AbMotion.smooth(), label = "verdict")
            val source = remember { MutableInteractionSource() }
            AbSurface(
                Modifier
                    .weight(1f)
                    .pressScale(source)
                    .clickable(source, PressIndication, role = Role.Button, onClickLabel = verdict.label) {
                        haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                        onVerdict(verdict)
                    },
                color = background, shape = Ab.shapes.small, border = if (on) null else Ab.colors.line,
                contentColor = if (on) Ab.colors.ground else color,
            ) {
                Box(Modifier.heightIn(min = 44.dp).fillMaxWidth(), contentAlignment = Alignment.Center) {
                    AbText(verdict.label, style = Ab.type.label, maxLines = 1)
                }
            }
        }
    }
}

/**
 * A track: sigil, title, a second line (artist and why it's here), facts in mono at the end. Swipe right for
 * [onSwipeRight] (Keep), left for [onSwipeLeft] (No); long-press for more. [verdict] shows as a thin edge on the left.
 */
@Composable
fun TrackRow(
    title: String,
    subtitle: String,
    modifier: Modifier = Modifier,
    sigil: SigilSpec? = null,
    sigilFaint: Boolean = false,
    facts: String? = null,
    verdict: VerdictStyle? = null,
    playing: Boolean = false,
    onClick: (() -> Unit)? = null,
    onLongClick: (() -> Unit)? = null,
    onSwipeRight: (() -> Unit)? = null,
    onSwipeLeft: (() -> Unit)? = null,
    rightLabel: String = "Keep",
    leftLabel: String = "No",
    trailing: (@Composable () -> Unit)? = null,
) {
    val haptics = LocalHapticFeedback.current
    val scope = rememberCoroutineScope()
    val offset = remember { Animatable(0f) }
    val threshold = with(LocalDensity.current) { 72.dp.toPx() }
    val keepColor = Ab.colors.keep
    val noColor = Ab.colors.no
    Box(modifier.fillMaxWidth()) {
        // what a swipe will do, revealed under the row
        if (offset.value != 0f) {
            val right = offset.value > 0
            val reached = abs(offset.value) >= threshold
            Box(
                Modifier.matchParentSize().background(if (right) keepColor.copy(alpha = if (reached) 1f else 0.35f) else noColor.copy(alpha = if (reached) 1f else 0.35f)),
                contentAlignment = if (right) Alignment.CenterStart else Alignment.CenterEnd,
            ) {
                AbText(if (right) rightLabel else leftLabel, Modifier.padding(horizontal = Ab.space.l), style = Ab.type.label, color = Ab.colors.ground)
            }
        }
        Row(
            Modifier
                .offset { IntOffset(offset.value.roundToInt(), 0) }
                .background(Ab.colors.ground)
                .then(
                    if (onSwipeLeft != null || onSwipeRight != null) Modifier.pointerInput(onSwipeLeft, onSwipeRight) {
                        var crossed = false
                        detectHorizontalDragGestures(
                            onDragEnd = {
                                val v = offset.value
                                scope.launch { offset.animateTo(0f, AbMotion.snappy()) }
                                if (v >= threshold) onSwipeRight?.invoke() else if (v <= -threshold) onSwipeLeft?.invoke()
                                crossed = false
                            },
                            onDragCancel = { scope.launch { offset.animateTo(0f) } },
                        ) { change, delta ->
                            change.consume()
                            val allowed = (offset.value + delta).let { if (it > 0 && onSwipeRight == null || it < 0 && onSwipeLeft == null) 0f else it }
                            scope.launch { offset.snapTo(allowed.coerceIn(-threshold * 1.6f, threshold * 1.6f)) }
                            val now = abs(allowed) >= threshold
                            if (now && !crossed) haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                            crossed = now
                        }
                    } else Modifier
                )
                .then(if (onClick != null || onLongClick != null) Modifier.combinedClickable(onClick = { onClick?.invoke() }, onLongClick = onLongClick) else Modifier)
                .fillMaxWidth()
                .heightIn(min = 56.dp)
                .padding(vertical = Ab.space.s),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.width(3.dp).height(28.dp).background(verdict?.color() ?: Color.Transparent))
            Spacer(Modifier.width(Ab.space.s))
            if (sigil != null) SoundSigil(sigil, size = 30.dp, faint = sigilFaint, playing = playing)
            else Box(Modifier.size(30.dp))
            Spacer(Modifier.width(Ab.space.m))
            Column(Modifier.weight(1f)) {
                AbText(title, style = Ab.type.label, maxLines = 1)
                AbText(subtitle, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
            }
            if (facts != null) {
                Spacer(Modifier.width(Ab.space.s))
                AbText(facts, style = Ab.type.number, color = Ab.colors.textFaint, maxLines = 2)
            }
            if (trailing != null) {
                Spacer(Modifier.width(Ab.space.s))
                trailing()
            }
        }
    }
}

/** A list (playlist or discovery list): title, a mono meta line, its colour strip, and whether it's synced to Tidal. */
@Composable
fun ListRow(title: String, meta: String, hues: List<Float?>, modifier: Modifier = Modifier, synced: Boolean = false, onClick: () -> Unit) {
    Column(
        modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = Ab.space.m),
        verticalArrangement = Arrangement.spacedBy(Ab.space.s),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AbText(title, Modifier.weight(1f), style = Ab.type.heading, maxLines = 1)
            AbText(meta, style = Ab.type.number, color = Ab.colors.textFaint, maxLines = 1)
            Spacer(Modifier.width(Ab.space.s))
            SyncMark(synced)
        }
        ColourStrip(hues)
    }
}

/** Filled when a list is kept in sync with Tidal, an outline when it isn't. */
@Composable
fun SyncMark(synced: Boolean) {
    val keep = Ab.colors.keep
    val faint = Ab.colors.textFaint
    Canvas(Modifier.size(8.dp)) {
        if (synced) drawCircle(keep) else drawCircle(faint, style = Stroke(1.5.dp.toPx()))
    }
}

/** A suggestion to act on: a label, why, and an arrow. */
@Composable
fun ActionRow(label: String, detail: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Row(
        modifier.fillMaxWidth().clickable(onClick = onClick).heightIn(min = 52.dp).padding(vertical = Ab.space.s),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            AbText(label, style = Ab.type.label, maxLines = 1)
            AbText(detail, style = Ab.type.caption, color = Ab.colors.textFaint, maxLines = 1)
        }
        AbText("→", style = Ab.type.number, color = Ab.colors.textMuted)
    }
}

/** The box that opens the Ask page, with a blinking caret. */
@Composable
fun AskBox(placeholder: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val caret = rememberInfiniteTransition(label = "caret").animateFloat(1f, 0f, infiniteRepeatable(tween(550, easing = LinearEasing), RepeatMode.Reverse), label = "blink").value
    val textColor = Ab.colors.text
    AbSurface(modifier.fillMaxWidth().clickable(onClick = onClick), color = Color.Transparent, shape = Ab.shapes.medium, border = Ab.colors.textFaint) {
        Row(Modifier.heightIn(min = 48.dp).padding(horizontal = Ab.space.l), verticalAlignment = Alignment.CenterVertically) {
            Canvas(Modifier.size(2.dp, 16.dp)) { drawRect(textColor.copy(alpha = if (caret > 0.5f) 1f else 0f)) }
            Spacer(Modifier.width(Ab.space.m))
            AbText(placeholder, style = Ab.type.body, color = Ab.colors.textMuted, maxLines = 1)
        }
    }
}

/** The five pages' names along the top; the current one bright, its neighbours fading with distance. */
@Composable
fun PageLabels(labels: List<String>, position: Float, modifier: Modifier = Modifier, alerts: Set<Int> = emptySet(), onSelect: (Int) -> Unit) {
    Row(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
        labels.forEachIndexed { i, label ->
            val distance = abs(position - i).coerceAtMost(2f)
            val color = lerpColor(Ab.colors.text, Ab.colors.textFaint, (distance).coerceIn(0f, 1f))
            Box(Modifier.clickable(remember { MutableInteractionSource() }, null, role = Role.Tab) { onSelect(i) }.padding(horizontal = 9.dp, vertical = 10.dp)) {
                AbText(label.uppercase(), style = Ab.type.overline, color = color)
                if (i in alerts) Box(Modifier.align(Alignment.TopEnd).offset(x = 6.dp, y = (-3).dp)) { Dot(Ab.colors.notNow, size = 5.dp) }
            }
        }
    }
}

private fun lerpColor(a: Color, b: Color, t: Float) = androidx.compose.ui.graphics.lerp(a, b, t)

/** Flat tabs with an underline that slides to the selected one. */
@Composable
fun SegmentTabs(labels: List<String>, selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier) {
    BoxWithConstraints(modifier.fillMaxWidth().height(40.dp)) {
        val segment = maxWidth / labels.size
        val x by animateFloatAsState(selected.toFloat(), AbMotion.snappy(), label = "tabs")
        Box(Modifier.align(Alignment.BottomStart).fillMaxWidth().height(1.dp).background(Ab.colors.line))
        Box(Modifier.align(Alignment.BottomStart).offset(x = segment * x).width(segment).height(2.dp).background(Ab.colors.text))
        Row(Modifier.fillMaxWidth().fillMaxHeight()) {
            labels.forEachIndexed { i, label ->
                val color by animateColorAsState(if (i == selected) Ab.colors.text else Ab.colors.textFaint, AbMotion.smooth(), label = "tab")
                Box(Modifier.weight(1f).fillMaxHeight().clickable(remember { MutableInteractionSource() }, null, role = Role.Tab) { onSelect(i) }, contentAlignment = Alignment.Center) {
                    AbText(label, style = Ab.type.label, color = color, maxLines = 1)
                }
            }
        }
    }
}

/**
 * The now-playing chip at the bottom of every page but Home: sigil, title, artist. Tap or drag up to open the drawer.
 */
@Composable
fun NowChip(title: String, subtitle: String, sigil: SigilSpec, playing: Boolean, modifier: Modifier = Modifier, sigilFaint: Boolean = false, syncColor: Color? = null, onOpen: () -> Unit) {
    AbSurface(
        modifier
            .fillMaxWidth()
            .pointerInput(Unit) {
                var total = 0f
                detectVerticalDragGestures(onDragEnd = { if (total < -24f) onOpen(); total = 0f }) { change, dy -> change.consume(); total += dy }
            }
            .clickable(onClick = onOpen),
        color = Ab.colors.surface, shape = Ab.shapes.medium, border = Ab.colors.line,
    ) {
        Row(Modifier.heightIn(min = 54.dp).padding(horizontal = Ab.space.m), verticalAlignment = Alignment.CenterVertically) {
            Box {
                SoundSigil(sigil, size = 32.dp, playing = playing, faint = sigilFaint)
                if (syncColor != null) Box(Modifier.align(Alignment.TopEnd)) { Dot(syncColor, size = 7.dp) }
            }
            Spacer(Modifier.width(Ab.space.m))
            Column(Modifier.weight(1f)) {
                AbText(title, style = Ab.type.label, maxLines = 1)
                AbText(subtitle, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
            }
            AbText("▴", style = Ab.type.number, color = Ab.colors.textFaint)
        }
    }
}

/**
 * While a mix is being made: dots in your context colours drift in and gather into a column, over and over, with
 * a line beneath that steps through what the server is doing. The server answers in one go, so the steps are paced
 * here, not reported.
 */
@Composable
fun BuildingAnimation(hues: List<Float?>, stages: List<Pair<String, String>>, modifier: Modifier = Modifier) {
    val t = rememberInfiniteTransition(label = "build").animateFloat(0f, 1f, infiniteRepeatable(tween(3200, easing = LinearEasing)), label = "t").value
    var stage by remember { mutableIntStateOf(0) }
    LaunchedEffect(stages) { while (true) { delay(1800); stage = (stage + 1) % stages.size.coerceAtLeast(1) } }
    val dots = remember(hues) {
        hues.take(28).mapIndexed { i, h ->
            val sx = ((i * 97) % 100) / 100f
            val sy = ((i * 53) % 100) / 100f
            Triple(h, Offset(sx, sy), i)
        }
    }
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Canvas(Modifier.size(240.dp, 300.dp)) {
            val n = dots.size.coerceAtLeast(1)
            dots.forEach { (h, start, i) ->
                val delayed = ((t - (i % 7) * 0.035f) % 1f + 1f) % 1f
                val p = when {
                    delayed < 0.12f -> 0f
                    delayed < 0.55f -> easeInOut((delayed - 0.12f) / 0.43f)
                    delayed < 0.8f -> 1f
                    else -> 1f - easeInOut((delayed - 0.8f) / 0.2f)
                }
                val from = Offset(start.x * size.width, start.y * size.height)
                val to = Offset(size.width / 2, size.height * (0.08f + 0.84f * (i % n) / n))
                val pos = from + (to - from) * p
                drawCircle(contextColor(h).copy(alpha = 0.3f + 0.7f * p), 4.5.dp.toPx(), pos)
            }
        }
        Spacer(Modifier.height(Ab.space.l))
        val (headline, detail) = stages.getOrElse(stage) { "" to "" }
        AbText(headline, style = Ab.type.label)
        AbText(detail, style = Ab.type.number, color = Ab.colors.textMuted)
    }
}

private fun easeInOut(x: Float) = (if (x < 0.5f) 2 * x * x else 1 - (-2 * x + 2).let { it * it } / 2).coerceIn(0f, 1f)

/** A small flat level marker, for rows where a bar is too much: ▮▮▮▯ style dots. */
@Composable
fun Pips(value: Float, modifier: Modifier = Modifier, count: Int = 4, color: Color = Ab.colors.textMuted) {
    val off = Ab.colors.line
    Canvas(modifier.size((count * 6).dp, 8.dp)) {
        val filled = (value.coerceIn(0f, 1f) * count).roundToInt()
        for (i in 0 until count) drawRoundRect(if (i < filled) color else off, Offset(i * 6.dp.toPx(), 0f), androidx.compose.ui.geometry.Size(4.dp.toPx(), size.height), CornerRadius(1.dp.toPx()))
    }
}
