package com.georgewaller.audiobrain.design

import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.foundation.LocalIndication
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.LocalTextSelectionColors
import androidx.compose.foundation.text.selection.TextSelectionColors
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Every colour the app uses. There's no fixed accent: primary actions use the text colour, and the only hues are a
 * context's colour (as a flat mark: a dot, a strip, a sigil), the four verdicts and the age tags on lists (now,
 * lately, an era...), each kept clear of the verdicts'. Components read these, never literals.
 */
@Immutable
data class AbColors(
    val ground: Color,      // behind everything
    val surface: Color,     // the chip, sheets
    val raised: Color,      // controls and pressed rows
    val overlay: Color,     // selected states
    val line: Color,        // hairlines between rows
    val text: Color,
    val textMuted: Color,
    val textFaint: Color,
    val accent: Color,      // primary actions: the text colour, so colour stays for meaning
    val onAccent: Color,
    val keep: Color,
    val no: Color,
    val notNow: Color,
    val never: Color,
    val warning: Color,
    val scrim: Color,
    // age tags: how recent a list is, or what kind of lead it is on Home
    val ageNow: Color = Color(0xFFFF9F5A),
    val ageLately: Color = Color(0xFFD9A27A),
    val ageEra: Color = Color(0xFF8FB3FF),
    val ageNew: Color = Color(0xFF5FD4C4),
    val ageHabit: Color = Color(0xFFE59AD0),
    val ageRetired: Color = Color(0xFF8B9198),
)

val DarkColors = AbColors(
    ground = Color(0xFF101113),
    surface = Color(0xFF16181B),
    raised = Color(0xFF1C1E21),
    overlay = Color(0xFF26292D),
    line = Color(0xFF25282C),
    text = Color(0xFFECEDEE),
    textMuted = Color(0xFF9AA0A6),
    textFaint = Color(0xFF62676E),
    accent = Color(0xFFECEDEE),
    onAccent = Color(0xFF101113),
    keep = Color(0xFF7BD99A),
    no = Color(0xFFFF7A6B),
    notNow = Color(0xFFF2C35B),
    never = Color(0xFFB79BFF),
    warning = Color(0xFFF2C35B),
    scrim = Color(0x8C000000),
)

/**
 * A context's colour from its hue (the server's: its angle in your taste, so it's the same on every screen). [near]
 * is for a track that isn't in the context but sounds closest to it (unplayed ones): the same hue at half saturation.
 */
fun contextColor(hue: Float?, colors: AbColors = DarkColors, near: Boolean = false): Color =
    if (hue == null) colors.textFaint else Color.hsl(((hue % 360f) + 360f) % 360f, contextSaturation(near), 0.62f)

fun contextSaturation(near: Boolean) = if (near) 0.29f else 0.58f

/** A track's colour in a list: its context's hue, and whether it's only near that context rather than in it. */
@Immutable
data class TrackHue(val hue: Float, val near: Boolean = false)

@OptIn(ExperimentalTextApi::class)
private fun archivo(weight: Int, width: Float) =
    Font(R.font.archivo, FontWeight(weight), variationSettings = FontVariation.Settings(FontVariation.weight(weight), FontVariation.width(width)))

/** Archivo, narrowed: long context names fit a 720p DAP screen without wrapping. */
private val DisplayNarrow = FontFamily(archivo(600, 78f), archivo(700, 78f), archivo(800, 72f))

@OptIn(ExperimentalTextApi::class)
private val Sans = FontFamily(
    listOf(400, 500, 600).map { Font(R.font.plex_sans, FontWeight(it), variationSettings = FontVariation.Settings(FontVariation.weight(it))) }
)

private val Mono = FontFamily(Font(R.font.plex_mono_regular, FontWeight.Normal), Font(R.font.plex_mono_medium, FontWeight.Medium))

/** Narrow Archivo for headings, Plex Sans for reading, Plex Mono for numbers (BPM, keys, times, counts). */
@Immutable
data class AbType(
    val display: TextStyle = TextStyle(fontFamily = DisplayNarrow, fontWeight = FontWeight(800), fontSize = 34.sp, lineHeight = 36.sp, letterSpacing = (-0.3).sp),
    val title: TextStyle = TextStyle(fontFamily = DisplayNarrow, fontWeight = FontWeight(700), fontSize = 24.sp, lineHeight = 26.sp),
    val heading: TextStyle = TextStyle(fontFamily = DisplayNarrow, fontWeight = FontWeight(700), fontSize = 17.sp, lineHeight = 20.sp),
    val body: TextStyle = TextStyle(fontFamily = Sans, fontWeight = FontWeight(400), fontSize = 15.sp, lineHeight = 21.sp),
    val label: TextStyle = TextStyle(fontFamily = Sans, fontWeight = FontWeight(600), fontSize = 13.5.sp, lineHeight = 17.sp),
    val caption: TextStyle = TextStyle(fontFamily = Sans, fontWeight = FontWeight(400), fontSize = 12.sp, lineHeight = 16.sp),
    val overline: TextStyle = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Medium, fontSize = 10.5.sp, lineHeight = 13.sp, letterSpacing = 1.1.sp),
    val number: TextStyle = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Normal, fontSize = 11.5.sp, lineHeight = 15.sp),
    val code: TextStyle = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Normal, fontSize = 11.sp, lineHeight = 15.sp),
)

/** A 4 dp grid. */
@Immutable
object AbSpace {
    val xxs: Dp = 2.dp
    val xs: Dp = 4.dp
    val s: Dp = 8.dp
    val m: Dp = 12.dp
    val l: Dp = 16.dp
    val xl: Dp = 24.dp
    val xxl: Dp = 32.dp
    val gutter: Dp = 18.dp  // screen edges
    val touch: Dp = 48.dp   // smallest tappable size; DAP screens are small and used on the move
}

@Immutable
object AbShapes {
    val small = RoundedCornerShape(6.dp)
    val medium = RoundedCornerShape(10.dp)
    val large = RoundedCornerShape(18.dp)
    val sheet = RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp)
    val pill = RoundedCornerShape(50)
}

/** Named springs, so motion feels the same everywhere. */
object AbMotion {
    fun <T> snappy(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.8f, stiffness = Spring.StiffnessMediumLow * 2)
    fun <T> smooth(): FiniteAnimationSpec<T> = spring(dampingRatio = Spring.DampingRatioNoBouncy, stiffness = Spring.StiffnessLow)
    fun <T> bouncy(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.55f, stiffness = Spring.StiffnessMedium)
    val offset: FiniteAnimationSpec<IntOffset> = spring(dampingRatio = 0.9f, stiffness = Spring.StiffnessMediumLow, visibilityThreshold = IntOffset(1, 1))
}

val LocalAbColors = staticCompositionLocalOf { DarkColors }
val LocalAbType = staticCompositionLocalOf { AbType() }
val LocalContentColor = staticCompositionLocalOf { DarkColors.text }

/** The tokens, read as `Ab.colors.text`, `Ab.type.title`. */
object Ab {
    val colors: AbColors
        @Composable @ReadOnlyComposable get() = LocalAbColors.current
    val type: AbType
        @Composable @ReadOnlyComposable get() = LocalAbType.current
    val space = AbSpace
    val shapes = AbShapes
}

@Composable
fun AbTheme(colors: AbColors = DarkColors, content: @Composable () -> Unit) {
    CompositionLocalProvider(
        LocalAbColors provides colors,
        LocalAbType provides AbType(),
        LocalContentColor provides colors.text,
        LocalIndication provides PressIndication,
        LocalTextSelectionColors provides TextSelectionColors(colors.text, colors.text.copy(alpha = 0.3f)),
        content = content,
    )
}
