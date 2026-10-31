package com.georgewaller.audiobrain.design

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.windowInsetsBottomHeight
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/**
 * A sheet that rises from the bottom over a dimmed page: the now-playing drawer, a long-press menu. Drag it down or
 * tap outside to close; with [onExpand], drag it up to open what it's a sheet of in full.
 */
@Composable
fun AbDrawer(open: Boolean, onDismiss: () -> Unit, modifier: Modifier = Modifier, onExpand: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    Box(modifier.fillMaxSize()) {
        AnimatedVisibility(open, enter = fadeIn(), exit = fadeOut()) {
            Box(Modifier.fillMaxSize().background(Ab.colors.scrim).clickable(remember { MutableInteractionSource() }, null, onClick = onDismiss))
        }
        AnimatedVisibility(
            open, modifier = Modifier.align(Alignment.BottomCenter),
            enter = slideInVertically(AbMotion.offset) { it }, exit = slideOutVertically { it },
        ) {
            val drag = remember { Animatable(0f) }
            val scope = rememberCoroutineScope()
            LaunchedEffect(open) { drag.snapTo(0f) }
            Column(
                Modifier
                    .offset { IntOffset(0, drag.value.roundToInt()) }
                    .fillMaxWidth()
                    .background(Ab.colors.surface, Ab.shapes.sheet)
                    .pointerInput(Unit) {
                        detectVerticalDragGestures(
                            onDragEnd = {
                                when {
                                    drag.value > 120f -> onDismiss()
                                    drag.value < -100f && onExpand != null -> onExpand()
                                    else -> scope.launch { drag.animateTo(0f, AbMotion.snappy()) }
                                }
                            },
                        ) { change, dy ->
                            change.consume()
                            // up only as far as it takes to expand, and heavier than down
                            val step = if (dy < 0 || drag.value < 0) dy * 0.6f else dy
                            scope.launch { drag.snapTo((drag.value + step).coerceAtLeast(if (onExpand != null) -200f else 0f)) }
                        }
                    }
                    .clickable(remember { MutableInteractionSource() }, null) {}
                    .padding(horizontal = Ab.space.gutter),
            ) {
                Box(Modifier.fillMaxWidth().padding(vertical = Ab.space.m), contentAlignment = Alignment.Center) {
                    Box(Modifier.size(36.dp, 4.dp).background(Ab.colors.textFaint, Ab.shapes.pill))
                }
                content()
                Spacer(Modifier.height(Ab.space.l))
                Spacer(Modifier.windowInsetsBottomHeight(WindowInsets.navigationBars))
            }
        }
    }
}
