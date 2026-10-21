package com.georgewaller.audiobrain.widget

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.action.Action
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextAlign
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import com.georgewaller.audiobrain.MainActivity
import com.georgewaller.audiobrain.NowSet
import com.georgewaller.audiobrain.R
import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.data.dataGraph
import com.georgewaller.audiobrain.design.SigilSpec
import com.georgewaller.audiobrain.design.contextColor
import com.georgewaller.audiobrain.design.sigilBitmap
import com.georgewaller.audiobrain.player.SessionTracker
import com.georgewaller.audiobrain.shortName
import com.georgewaller.audiobrain.sigil
import com.georgewaller.audiobrain.sigilFor
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map

/**
 * The home screen widget, one widget in three layouts chosen by the size it's dragged to: a strip at 4×1 (sigil,
 * title, pause, Keep, No), a square at 2×2 (the sigil large) and a pill at 2×1 (sigil, title, Keep). The sigil and
 * title open the app. With nothing playing it shows the top lists, each opening in the app. Redrawn by
 * [NowWidgetUpdater].
 */
class NowWidget : GlanceAppWidget() {
    override val sizeMode = SizeMode.Responsive(setOf(PILL, STRIP, SQUARE, WIDE))

    @OptIn(ExperimentalCoroutinesApi::class)
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val graph = context.dataGraph
        val cached = graph.lists.cached()
        val verdicts = combine(SessionTracker.nowPlaying, SessionTracker.within) { np, within -> within?.ref ?: np?.takeIf { it.hasTrack }?.ref }
            .flatMapLatest { ref -> if (ref == null) flowOf(null) else graph.feedback.latestFor(ref).map { Verdict.fromApi(it?.verdict) } }
        provideContent {
            val np = SessionTracker.nowPlaying.collectAsState().value?.takeIf { it.hasTrack }
            val within = SessionTracker.within.collectAsState().value
            val track = NowSet.track.collectAsState().value
            val setTrack = NowSet.now.collectAsState().value?.line?.track
            val verdict = verdicts.collectAsState(null).value
            val lists = graph.lists.state.collectAsState().value.lists.ifEmpty { cached }.filter { it.kind == "mix" }.take(3)
            val size = LocalSize.current
            val layout = when {
                size.height >= SQUARE.height -> Layout.Square
                size.width >= STRIP.width -> Layout.Strip
                else -> Layout.Pill
            }
            val open = actionStartActivity(Intent(context, MainActivity::class.java))
            Box(GlanceModifier.fillMaxSize().background(GROUND).cornerRadius(18.dp)) {
                if (np == null) Idle(context, layout, lists, open)
                else {
                    val spec = setTrack?.takeIf { it.sound != null }?.sigil() ?: sigilFor(np, track)
                    Playing(context, layout, Now(within?.title ?: np.title, within?.artist ?: np.artist, np.playing, verdict, spec), open)
                }
            }
        }
    }

    private enum class Layout { Pill, Strip, Square }

    private data class Now(val title: String, val artist: String, val playing: Boolean, val verdict: Verdict?, val sigil: Pair<SigilSpec, Boolean>)

    @Composable
    private fun Playing(context: Context, layout: Layout, now: Now, open: Action) {
        when (layout) {
            Layout.Strip -> Row(GlanceModifier.fillMaxSize().padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Sigil(context, now, 46, open)
                Spacer(GlanceModifier.width(10.dp))
                Titles(now, GlanceModifier.defaultWeight().clickable(open))
                Spacer(GlanceModifier.width(6.dp))
                PlayPause(now.playing, 36)
                Spacer(GlanceModifier.width(6.dp))
                VerdictButton(Verdict.Keep, now.verdict, 36, 36)
                Spacer(GlanceModifier.width(6.dp))
                VerdictButton(Verdict.No, now.verdict, 36, 36)
            }
            Layout.Square -> Column(GlanceModifier.fillMaxSize().padding(10.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalAlignment = Alignment.CenterVertically) {
                Sigil(context, now, 62, open)
                Spacer(GlanceModifier.height(6.dp))
                Titles(now, GlanceModifier.fillMaxWidth().clickable(open), center = true)
                Spacer(GlanceModifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    VerdictButton(Verdict.Keep, now.verdict, 40, 28)
                    Spacer(GlanceModifier.width(6.dp))
                    PlayPause(now.playing, 28)
                    Spacer(GlanceModifier.width(6.dp))
                    VerdictButton(Verdict.No, now.verdict, 40, 28)
                }
            }
            Layout.Pill -> Row(GlanceModifier.fillMaxSize().padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Sigil(context, now, 38, open)
                Spacer(GlanceModifier.width(8.dp))
                Titles(now, GlanceModifier.defaultWeight().clickable(open))
                Spacer(GlanceModifier.width(6.dp))
                VerdictButton(Verdict.Keep, now.verdict, 32, 32)
            }
        }
    }

    @Composable
    private fun Idle(context: Context, layout: Layout, lists: List<MixList>, open: Action) {
        val shown = when (layout) { Layout.Square -> 3; Layout.Strip -> 2; Layout.Pill -> 1 }
        Column(GlanceModifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 8.dp).clickable(open), verticalAlignment = Alignment.CenterVertically) {
            if (layout != Layout.Pill) Text("TOP LISTS", style = TextStyle(color = FAINT, fontSize = 9.sp, fontWeight = FontWeight.Medium), maxLines = 1)
            if (lists.isEmpty()) Text("Open Audio Brain to load your lists", style = TextStyle(color = MUTED, fontSize = 12.sp), maxLines = 2)
            lists.take(shown).forEach { l ->
                Row(GlanceModifier.fillMaxWidth().padding(vertical = 3.dp).clickable(actionStartActivity(openList(context, l.key))), verticalAlignment = Alignment.CenterVertically) {
                    Box(GlanceModifier.size(7.dp).cornerRadius(4.dp).background(ColorProvider(contextColor(l.context?.hue)))) {}
                    Spacer(GlanceModifier.width(8.dp))
                    Text(shortName(l.title).removePrefix("Peak-Time "), maxLines = 1, style = TextStyle(color = TEXT, fontSize = 12.5.sp, fontWeight = FontWeight.Medium))
                }
            }
        }
    }

    @Composable
    private fun Sigil(context: Context, now: Now, dp: Int, open: Action) {
        val density = context.resources.displayMetrics.density
        val bitmap = sigilBitmap(now.sigil.first, (dp * density).toInt(), density, faint = now.sigil.second)
        Image(ImageProvider(bitmap), "Open Audio Brain", GlanceModifier.size(dp.dp).clickable(open))
    }

    @Composable
    private fun Titles(now: Now, modifier: GlanceModifier, center: Boolean = false) {
        val align = if (center) TextAlign.Center else TextAlign.Start
        Column(modifier) {
            Text(now.title, maxLines = 1, style = TextStyle(color = TEXT, fontSize = 13.5.sp, fontWeight = FontWeight.Bold, textAlign = align), modifier = GlanceModifier.fillMaxWidth())
            Text(now.artist, maxLines = 1, style = TextStyle(color = MUTED, fontSize = 11.sp, textAlign = align), modifier = GlanceModifier.fillMaxWidth())
        }
    }

    @Composable
    private fun PlayPause(playing: Boolean, dp: Int) {
        Box(
            GlanceModifier.size(dp.dp).cornerRadius((dp / 2).dp).background(RAISED)
                .clickable(actionRunCallback<TransportAction>(actionParametersOf(TransportAction.WHAT to "toggle"))),
            contentAlignment = Alignment.Center,
        ) { Image(ImageProvider(if (playing) R.drawable.ic_widget_pause else R.drawable.ic_widget_play), if (playing) "Pause" else "Play", GlanceModifier.size((dp / 2).dp), colorFilter = ColorFilter.tint(TEXT)) }
    }

    @Composable
    private fun VerdictButton(verdict: Verdict, given: Verdict?, w: Int, h: Int) {
        val on = verdict == given
        val colour = if (verdict == Verdict.Keep) KEEP else NO
        Box(
            GlanceModifier.size(w.dp, h.dp).cornerRadius(8.dp).background(if (on) colour else RAISED)
                .clickable(actionRunCallback<VerdictAction>(actionParametersOf(VerdictAction.VERDICT to verdict.api))),
            contentAlignment = Alignment.Center,
        ) {
            Image(
                ImageProvider(if (verdict == Verdict.Keep) R.drawable.ic_widget_keep else R.drawable.ic_widget_no),
                if (verdict == Verdict.Keep) "Keep" else "No", GlanceModifier.size(16.dp), colorFilter = ColorFilter.tint(if (on) GROUND else colour),
            )
        }
    }

    private fun openList(context: Context, key: String) = Intent(context, MainActivity::class.java)
        .setAction("${context.packageName}.LIST.$key")  // distinct per list, so each row gets its own pending intent
        .putExtra(MainActivity.EXTRA_LIST, key)

    companion object {
        private val PILL = DpSize(110.dp, 40.dp)
        private val STRIP = DpSize(250.dp, 40.dp)
        private val SQUARE = DpSize(110.dp, 110.dp)
        private val WIDE = DpSize(250.dp, 110.dp)  // 4×2: the square's layout, with room to spare

        // the design system's dark colours (core:design's AbColors)
        private val GROUND = ColorProvider(Color(0xFF101113))
        private val RAISED = ColorProvider(Color(0xFF1A1C1F))
        private val TEXT = ColorProvider(Color(0xFFECEDEE))
        private val MUTED = ColorProvider(Color(0xFF9AA0A6))
        private val FAINT = ColorProvider(Color(0xFF62676E))
        private val KEEP = ColorProvider(Color(0xFF7BD99A))
        private val NO = ColorProvider(Color(0xFFFF7A6B))
    }
}

class NowWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = NowWidget()
}

/** Keep or No from the widget: into the outbox, like the notification's buttons. Within a set, for its current track. */
class VerdictAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val verdict = Verdict.fromApi(parameters[VERDICT]) ?: return
        val np = SessionTracker.nowPlaying.value?.takeIf { it.hasTrack } ?: return
        context.dataGraph.feedback.give(verdict, SessionTracker.within.value?.ref ?: np.ref, where = "widget")
        NowWidget().updateAll(context)
    }

    companion object {
        val VERDICT = ActionParameters.Key<String>("verdict")
    }
}

/** Play or pause from the widget, on whichever player is playing. */
class TransportAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        when (parameters[WHAT]) {
            "previous" -> SessionTracker.previous()
            "next" -> SessionTracker.next()
            else -> SessionTracker.playPause()
        }
    }

    companion object {
        val WHAT = ActionParameters.Key<String>("what")
    }
}
