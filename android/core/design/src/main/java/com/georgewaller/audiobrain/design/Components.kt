package com.georgewaller.audiobrain.design

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

@Composable
fun AbText(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = Ab.type.body,
    color: Color = LocalContentColor.current,
    maxLines: Int = Int.MAX_VALUE,
) {
    BasicText(text, modifier, style.copy(color = color), overflow = TextOverflow.Ellipsis, maxLines = maxLines)
}

@Composable
fun AbIcon(icon: ImageVector, modifier: Modifier = Modifier, tint: Color = LocalContentColor.current, size: Dp = 22.dp, description: String? = null) {
    Image(icon, description, modifier.size(size), colorFilter = ColorFilter.tint(tint))
}

/** A filled area that sets the content colour for what's inside it. */
@Composable
fun AbSurface(
    modifier: Modifier = Modifier,
    color: Color = Ab.colors.surface,
    shape: Shape = Ab.shapes.medium,
    border: Color? = null,
    contentColor: Color = Ab.colors.text,
    content: @Composable () -> Unit,
) {
    Box(
        modifier
            .clip(shape)
            .background(color, shape)
            .then(if (border != null) Modifier.border(1.dp, border, shape) else Modifier)
    ) {
        CompositionLocalProvider(LocalContentColor provides contentColor, content = content)
    }
}

@Composable
fun AbCard(modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, padding: PaddingValues = PaddingValues(Ab.space.l), content: @Composable () -> Unit) {
    AbSurface(modifier.then(if (onClick != null) Modifier.pressScale().clickable(onClick = onClick) else Modifier)) {
        Box(Modifier.padding(padding)) { content() }
    }
}

/** Shrinks a touch target a little while it's held: tactile without being bouncy. */
@Composable
fun Modifier.pressScale(source: MutableInteractionSource? = null): Modifier {
    if (source == null) return this
    val pressed by source.collectIsPressedAsState()
    val scale by animateFloatAsState(if (pressed) 0.96f else 1f, AbMotion.snappy(), label = "press")
    return this.scale(scale)
}

enum class ButtonKind { Primary, Quiet, Ghost }

@Composable
fun AbButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    kind: ButtonKind = ButtonKind.Primary,
    icon: ImageVector? = null,
    enabled: Boolean = true,
) {
    val colors = Ab.colors
    val source = remember { MutableInteractionSource() }
    val (background, content) = when (kind) {
        ButtonKind.Primary -> colors.accent to colors.onAccent
        ButtonKind.Quiet -> colors.raised to colors.text
        ButtonKind.Ghost -> Color.Transparent to colors.text
    }
    AbSurface(
        modifier
            .pressScale(source)
            .alphaIf(!enabled)
            .clickable(source, PressIndication, enabled = enabled, role = Role.Button, onClick = onClick),
        color = background, shape = Ab.shapes.pill, contentColor = content,
        border = if (kind == ButtonKind.Ghost) colors.line else null,
    ) {
        Row(
            Modifier.heightIn(min = 44.dp).padding(horizontal = Ab.space.l),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Ab.space.s, Alignment.CenterHorizontally),
        ) {
            if (icon != null) AbIcon(icon, size = 18.dp)
            AbText(label, style = Ab.type.label, maxLines = 1)
        }
    }
}

@Composable
fun AbIconButton(icon: ImageVector, description: String, onClick: () -> Unit, modifier: Modifier = Modifier, tint: Color = LocalContentColor.current, background: Color = Color.Transparent, size: Dp = Ab.space.touch) {
    val source = remember { MutableInteractionSource() }
    Box(
        modifier
            .size(size)
            .pressScale(source)
            .clip(Ab.shapes.pill)
            .background(background)
            .clickable(source, PressIndication, role = Role.Button, onClickLabel = description, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { AbIcon(icon, tint = tint, description = description) }
}

@Composable
fun AbChip(label: String, modifier: Modifier = Modifier, selected: Boolean = false, color: Color = Ab.colors.accent, onClick: (() -> Unit)? = null) {
    val background by animateColorAsState(if (selected) color.copy(alpha = 0.18f) else Ab.colors.raised, AbMotion.smooth(), label = "chip")
    val content = if (selected) color else Ab.colors.textMuted
    AbSurface(
        modifier.then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
        color = background, shape = Ab.shapes.pill, contentColor = content,
    ) {
        AbText(label, Modifier.padding(horizontal = Ab.space.m, vertical = 6.dp), style = Ab.type.label, maxLines = 1)
    }
}

/** A small coloured status word: "running", "failed", "offline". [live] pulses the dot. */
@Composable
fun StatusPill(label: String, color: Color, modifier: Modifier = Modifier, live: Boolean = false) {
    AbSurface(modifier, color = color.copy(alpha = 0.14f), shape = Ab.shapes.pill, contentColor = color) {
        Row(Modifier.padding(horizontal = 10.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Dot(color, live)
            Spacer(Modifier.width(6.dp))
            AbText(label, style = Ab.type.overline, maxLines = 1)
        }
    }
}

@Composable
fun Dot(color: Color, live: Boolean = false, size: Dp = 7.dp) {
    val alpha = if (live) {
        val pulse = rememberInfiniteTransition(label = "pulse")
        pulse.animateFloat(1f, 0.25f, infiniteRepeatable(tween(700), RepeatMode.Reverse), label = "dot").value
    } else 1f
    Canvas(Modifier.size(size)) { drawCircle(color.copy(alpha = color.alpha * alpha)) }
}

@Composable
fun Overline(text: String, modifier: Modifier = Modifier, color: Color = Ab.colors.textFaint) {
    AbText(text.uppercase(), modifier, style = Ab.type.overline, color = color, maxLines = 1)
}

@Composable
fun SectionHeader(title: String, modifier: Modifier = Modifier, trailing: (@Composable RowScope.() -> Unit)? = null) {
    Row(modifier.fillMaxWidth().padding(top = Ab.space.xl, bottom = Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
        Overline(title, Modifier.weight(1f))
        trailing?.invoke(this)
    }
}

@Composable
fun AbDivider(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(1.dp).background(Ab.colors.line))
}

@Composable
fun AbTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    secret: Boolean = false,
    keyboardType: KeyboardType = KeyboardType.Text,
) {
    Column(modifier) {
        if (label.isNotEmpty()) {  // an empty label leaves just the field, where the placeholder says enough
            Overline(label)
            Spacer(Modifier.height(Ab.space.xs))
        }
        AbSurface(Modifier.fillMaxWidth(), color = Ab.colors.raised, shape = Ab.shapes.small) {
            Box(Modifier.padding(horizontal = Ab.space.m, vertical = Ab.space.m)) {
                if (value.isEmpty()) AbText(placeholder, color = Ab.colors.textFaint, maxLines = 1)
                BasicTextField(
                    value, onValueChange, Modifier.fillMaxWidth(),
                    textStyle = Ab.type.body.copy(color = Ab.colors.text),
                    singleLine = true,
                    cursorBrush = SolidColor(Ab.colors.accent),
                    keyboardOptions = KeyboardOptions(keyboardType = keyboardType, autoCorrectEnabled = false),
                    visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
                )
            }
        }
    }
}

@Composable
fun AbSwitch(checked: Boolean, onCheckedChange: (Boolean) -> Unit, modifier: Modifier = Modifier) {
    val colors = Ab.colors
    val position by animateFloatAsState(if (checked) 1f else 0f, AbMotion.snappy(), label = "switch")
    val track by animateColorAsState(if (checked) colors.accent else colors.overlay, AbMotion.smooth(), label = "track")
    Canvas(
        modifier
            .size(46.dp, 28.dp)
            .clip(Ab.shapes.pill)
            .clickable(role = Role.Switch) { onCheckedChange(!checked) }
    ) {
        drawRoundRect(track, cornerRadius = androidx.compose.ui.geometry.CornerRadius(size.height / 2))
        val r = size.height / 2 - 3.dp.toPx()
        val x = size.height / 2 + (size.width - size.height) * position
        drawCircle(if (checked) colors.onAccent else colors.textMuted, r, Offset(x, size.height / 2))
    }
}

/** A row with a label, an optional description and a control at the end. */
@Composable
fun SettingRow(title: String, modifier: Modifier = Modifier, description: String? = null, onClick: (() -> Unit)? = null, trailing: @Composable () -> Unit = {}) {
    Row(
        modifier
            .fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .heightIn(min = 56.dp)
            .padding(vertical = Ab.space.s),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            AbText(title, style = Ab.type.heading)
            if (description != null) AbText(description, style = Ab.type.caption, color = Ab.colors.textMuted)
        }
        Spacer(Modifier.width(Ab.space.m))
        trailing()
    }
}

/** Progress through a run's steps: done, failed and the current one, as segments. */
@Composable
fun StepTrack(total: Int, done: Int, failed: Int, running: Boolean, modifier: Modifier = Modifier) {
    val colors = Ab.colors
    val shimmer = if (running) rememberInfiniteTransition(label = "steps")
        .animateFloat(0.35f, 1f, infiniteRepeatable(tween(650, easing = LinearEasing), RepeatMode.Reverse), label = "current").value else 1f
    Canvas(modifier.fillMaxWidth().height(4.dp)) {
        if (total <= 0) return@Canvas
        val gap = 2.dp.toPx()
        val w = (size.width - gap * (total - 1)) / total
        for (i in 0 until total) {
            val color = when {
                i < done - failed -> colors.accent
                i < done -> colors.no
                i == done && running -> colors.accent.copy(alpha = shimmer * 0.7f)
                else -> colors.overlay
            }
            drawRoundRect(color, Offset(i * (w + gap), 0f), androidx.compose.ui.geometry.Size(w, size.height), androidx.compose.ui.geometry.CornerRadius(size.height))
        }
    }
}

@Composable
fun Spinner(modifier: Modifier = Modifier, color: Color = Ab.colors.accent, size: Dp = 18.dp) {
    val angle = rememberInfiniteTransition(label = "spin").animateFloat(0f, 360f, infiniteRepeatable(tween(900, easing = LinearEasing)), label = "angle").value
    Canvas(modifier.size(size)) {
        drawArc(color, angle, 270f, false, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round))
    }
}

@Composable
fun EmptyState(title: String, body: String, modifier: Modifier = Modifier, action: (@Composable () -> Unit)? = null) {
    Column(modifier.fillMaxWidth().padding(vertical = Ab.space.xxl), horizontalAlignment = Alignment.CenterHorizontally) {
        AbIcon(AbIcons.Wave, tint = Ab.colors.textFaint, size = 32.dp)
        Spacer(Modifier.height(Ab.space.m))
        AbText(title, style = Ab.type.heading)
        Spacer(Modifier.height(Ab.space.xs))
        AbText(body, style = Ab.type.caption, color = Ab.colors.textMuted)
        if (action != null) {
            Spacer(Modifier.height(Ab.space.l))
            action()
        }
    }
}

private fun Modifier.alphaIf(dim: Boolean) = if (dim) this.alpha(0.4f) else this
