package com.georgewaller.audiobrain.design

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathBuilder
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp

/** The app's icons: 24-unit line drawings with round ends, drawn here so there's no icon font or Material dependency. */
object AbIcons {
    val Keep by lazy { line("keep") { moveTo(5f, 12.5f); lineTo(10f, 17.5f); lineTo(19f, 7f) } }
    val No by lazy { line("no") { moveTo(6.5f, 6.5f); lineTo(17.5f, 17.5f); moveTo(17.5f, 6.5f); lineTo(6.5f, 17.5f) } }
    val NotNow by lazy { line("not_now") { circle(12f, 12f, 8.5f); moveTo(12f, 7.5f); lineTo(12f, 12f); lineTo(15f, 14f) } }
    val Never by lazy { line("never") { circle(12f, 12f, 8.5f); moveTo(6f, 18f); lineTo(18f, 6f) } }
    val Play by lazy { filled("play") { moveTo(8f, 5.5f); lineTo(19f, 12f); lineTo(8f, 18.5f); close() } }
    val Pause by lazy { filled("pause") { roundRect(6.5f, 5.5f, 4f, 13f); roundRect(13.5f, 5.5f, 4f, 13f) } }
    val Next by lazy { filled("next") { moveTo(5.5f, 6f); lineTo(14.5f, 12f); lineTo(5.5f, 18f); close(); roundRect(16f, 6f, 2.5f, 12f) } }
    val Previous by lazy { filled("previous") { moveTo(18.5f, 6f); lineTo(9.5f, 12f); lineTo(18.5f, 18f); close(); roundRect(5.5f, 6f, 2.5f, 12f) } }
    val Refresh by lazy {
        line("refresh") {
            moveTo(19f, 12f); arcTo(7f, 7f, 0f, true, true, 16.9f, 7f)
            moveTo(17.5f, 3.5f); lineTo(17.5f, 7.5f); lineTo(13.5f, 7.5f)
        }
    }
    val Settings by lazy {
        line("settings") {
            moveTo(4f, 7f); lineTo(20f, 7f); moveTo(4f, 17f); lineTo(20f, 17f)
            circle(9f, 7f, 2.2f); circle(15f, 17f, 2.2f)
        }
    }
    val Close by lazy { line("close") { moveTo(7f, 7f); lineTo(17f, 17f); moveTo(17f, 7f); lineTo(7f, 17f) } }
    val Delete by lazy { line("delete") { moveTo(4.5f, 7f); lineTo(19.5f, 7f); moveTo(9.5f, 7f); lineTo(10f, 4.5f); lineTo(14f, 4.5f); lineTo(14.5f, 7f); moveTo(6.5f, 7f); lineTo(7.5f, 19.5f); lineTo(16.5f, 19.5f); lineTo(17.5f, 7f) } }
    val Edit by lazy { line("edit") { moveTo(5f, 19f); lineTo(5.8f, 15.2f); lineTo(15.5f, 5.5f); lineTo(18.5f, 8.5f); lineTo(8.8f, 18.2f); close(); moveTo(13.5f, 7.5f); lineTo(16.5f, 10.5f) } }
    val Down by lazy { line("down") { moveTo(5.5f, 9.5f); lineTo(12f, 16f); lineTo(18.5f, 9.5f) } }
    val Back by lazy { line("back") { moveTo(14.5f, 5.5f); lineTo(8f, 12f); lineTo(14.5f, 18.5f) } }
    val Open by lazy { line("open") { moveTo(10f, 5f); lineTo(19f, 5f); lineTo(19f, 14f); moveTo(19f, 5f); lineTo(6f, 18f) } }
    val Sparkle by lazy {
        filled("sparkle") {
            moveTo(12f, 3f); quadTo(13f, 11f, 21f, 12f); quadTo(13f, 13f, 12f, 21f); quadTo(11f, 13f, 3f, 12f); quadTo(11f, 11f, 12f, 3f); close()
        }
    }
    val Bolt by lazy { filled("bolt") { moveTo(13.5f, 2.5f); lineTo(5f, 13.5f); lineTo(11f, 13.5f); lineTo(10f, 21.5f); lineTo(19f, 10f); lineTo(13f, 10f); close() } }
    /** Two arrows chasing round: kept in step with Tidal. */
    val Sync by lazy {
        line("sync") {
            moveTo(18.5f, 9.5f); arcTo(7f, 7f, 0f, false, false, 6.2f, 8.2f)
            moveTo(5.5f, 14.5f); arcTo(7f, 7f, 0f, false, false, 17.8f, 15.8f)
            moveTo(18.8f, 5.5f); lineTo(18.6f, 9.6f); lineTo(14.6f, 9.3f)
            moveTo(5.2f, 18.5f); lineTo(5.4f, 14.4f); lineTo(9.4f, 14.7f)
        }
    }
    val Stop by lazy { filled("stop") { roundRect(6.5f, 6.5f, 11f, 11f) } }
    val Device by lazy { line("device") { moveTo(7f, 3.5f); lineTo(17f, 3.5f); lineTo(17f, 20.5f); lineTo(7f, 20.5f); close(); circle(12f, 14.5f, 2.6f); moveTo(10f, 7f); lineTo(14f, 7f) } }
    val Wave by lazy { line("wave") { moveTo(3f, 12f); lineTo(6f, 12f); moveTo(8f, 8f); lineTo(8f, 16f); moveTo(11f, 5f); lineTo(11f, 19f); moveTo(14f, 9f); lineTo(14f, 15f); moveTo(17f, 7f); lineTo(17f, 17f); moveTo(20f, 11f); lineTo(20f, 13f) } }
}

private fun PathBuilder.circle(cx: Float, cy: Float, r: Float) {
    moveTo(cx + r, cy)
    arcTo(r, r, 0f, true, true, cx - r, cy)
    arcTo(r, r, 0f, true, true, cx + r, cy)
    close()
}

private fun PathBuilder.roundRect(x: Float, y: Float, w: Float, h: Float, r: Float = 1f) {
    moveTo(x + r, y); lineTo(x + w - r, y); quadTo(x + w, y, x + w, y + r)
    lineTo(x + w, y + h - r); quadTo(x + w, y + h, x + w - r, y + h)
    lineTo(x + r, y + h); quadTo(x, y + h, x, y + h - r)
    lineTo(x, y + r); quadTo(x, y, x + r, y); close()
}

private fun line(name: String, block: PathBuilder.() -> Unit) = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).path(
    stroke = SolidColor(Color.Black), strokeLineWidth = 1.8f, strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round, pathBuilder = block,
).build()

private fun filled(name: String, block: PathBuilder.() -> Unit) = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).path(
    fill = SolidColor(Color.Black), pathBuilder = block,
).build()
